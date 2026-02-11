/**
 * Arena2API - Background Script (Firefox) v2.0
 *
 * 支持：
 * - 远程服务器 URL
 * - Profile ID（每个浏览器实例独立身份）
 * - 加速 token 补充（服务器可请求更多 token）
 * - 扩展密钥认证
 */
(function() {
  'use strict';

  var TAG = '[Arena2API]';
  var api = typeof browser !== 'undefined' ? browser : chrome;

  // ========== 状态 ==========
  var state = {
    proxyUrl: 'http://127.0.0.1:9090',
    profileId: '',        // 由用户配置或自动生成
    extensionSecret: '',  // 可选的推送密钥
    connected: false,
    lastError: '',
    lastPush: 0,

    // tokens
    v3Tokens: [],   // [{token, action, ts}]
    v2Token: null,

    // cookies
    cookies: {},
    authToken: '',
    cfClearance: '',

    // models
    models: null,

    // tab
    tabId: null,

    // 服务器反馈
    serverPoolMax: 30,    // 服务器允许的最大 token 池
    serverNeedTokens: false,

    // 统计
    tokensMinted: 0,
    tokensPushed: 0,
    pushCount: 0,
    errorCount: 0,
  };

  // ========== 生成 Profile ID ==========
  function generateProfileId() {
    // 使用随机 ID，确保每个 Firefox Profile 唯一
    var chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
    var id = 'fp_';
    for (var i = 0; i < 8; i++) {
      id += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return id;
  }

  // ========== 从页面获取 cookies ==========
  async function requestPageCookies() {
    if (!state.tabId) return null;
    try {
      return await new Promise(function(resolve) {
        api.tabs.sendMessage(state.tabId, { type: 'NEED_COOKIES' }, function(resp) {
          if (api.runtime.lastError || !resp || !resp.cookies) {
            resolve(null);
          } else {
            resolve(resp.cookies);
          }
        });
      });
    } catch(e) {
      return null;
    }
  }

  // ========== Cookie 刷新 ==========
  async function refreshCookies() {
    try {
      var byDomain = await api.cookies.getAll({ domain: 'arena.ai' });
      var byDotDomain = await api.cookies.getAll({ domain: '.arena.ai' });
      var byUrl = await api.cookies.getAll({ url: 'https://arena.ai' });

      state.cookies = {};
      byDomain.concat(byDotDomain).concat(byUrl).forEach(function(c) {
        state.cookies[c.name] = c.value;
      });

      // 从页面获取 document.cookie
      var pageCookies = await requestPageCookies();
      if (pageCookies) {
        for (var k in pageCookies) {
          if (!state.cookies[k]) {
            state.cookies[k] = pageCookies[k];
          }
        }
      }

      state.cfClearance = state.cookies['cf_clearance'] || '';

      // auth token 可能分片存储
      var auth = state.cookies['arena-auth-prod-v1'] || '';
      if (!auth) {
        var p0 = state.cookies['arena-auth-prod-v1.0'] || '';
        var p1 = state.cookies['arena-auth-prod-v1.1'] || '';
        if (p0) {
          auth = p0 + (p1 || '');
        }
      }
      state.authToken = auth;
    } catch(e) {
      console.error(TAG, 'Cookie error:', e);
    }
  }

  // ========== Token 管理 ==========
  function addToken(token, action) {
    if (!token || token.length < 20) return;
    if (state.v3Tokens.some(function(t) { return t.token === token; })) return;
    state.v3Tokens.push({ token: token, action: action || 'chat_submit', ts: Date.now() });
    // 使用服务器配置的上限
    while (state.v3Tokens.length > state.serverPoolMax) state.v3Tokens.shift();
    state.tokensMinted++;
    console.log(TAG, 'Token added, pool:', state.v3Tokens.length, '/', state.serverPoolMax);
  }

  function cleanTokens() {
    var now = Date.now();
    state.v3Tokens = state.v3Tokens.filter(function(t) { return now - t.ts < 110000; });
  }

  // ========== 向 content script 请求 token ==========
  async function requestToken() {
    if (!state.tabId) {
      try {
        var tabs = await api.tabs.query({ url: 'https://arena.ai/*' });
        if (tabs.length > 0) state.tabId = tabs[0].id;
        else return;
      } catch(e) { return; }
    }
    try {
      api.tabs.sendMessage(state.tabId, {
        type: 'NEED_TOKEN',
        action: 'chat_submit',
      }, function(resp) {
        if (api.runtime.lastError) {
          state.tabId = null;
          return;
        }
        if (resp && resp.token) {
          addToken(resp.token, resp.action);
          pushToServer();
        }
      });
    } catch(e) {
      state.tabId = null;
    }
  }

  // ========== 批量请求 token（加速补充）==========
  async function requestMultipleTokens(count) {
    count = Math.min(count || 1, 5); // 最多同时请求 5 个
    for (var i = 0; i < count; i++) {
      // 间隔 2 秒请求，避免 reCAPTCHA 限流
      setTimeout(function() {
        requestToken();
      }, i * 2000);
    }
  }

  // ========== 推送到服务器 ==========
  async function pushToServer() {
    if (!state.proxyUrl) return;
    try {
      await refreshCookies();
      cleanTokens();

      var data = {
        profile_id: state.profileId,
        cookies: state.cookies,
        auth_token: state.authToken,
        cf_clearance: state.cfClearance,
        v3_tokens: state.v3Tokens.map(function(t) {
          return { token: t.token, action: t.action, age_ms: Date.now() - t.ts };
        }),
        v2_token: state.v2Token ? {
          token: state.v2Token.token,
          age_ms: Date.now() - state.v2Token.ts,
        } : null,
        models: state.models,
      };

      var url = state.proxyUrl.replace(/\/+$/, '') + '/v1/extension/push';
      var headers = { 'Content-Type': 'application/json' };

      // 添加扩展密钥
      if (state.extensionSecret) {
        headers['X-Extension-Secret'] = state.extensionSecret;
      }

      var resp = await fetch(url, {
        method: 'POST',
        headers: headers,
        body: JSON.stringify(data),
      });

      if (resp.ok) {
        state.connected = true;
        state.lastError = '';
        state.lastPush = Date.now();
        state.pushCount++;
        state.tokensPushed += state.v3Tokens.length;

        var result = await resp.json();

        // 更新服务器配置
        if (result.pool_max) {
          state.serverPoolMax = result.pool_max;
        }
        if (result.profile_id && !state.profileId) {
          // 如果服务器分配了 profile_id，保存它
          state.profileId = result.profile_id;
          api.storage.local.set({ profileId: result.profile_id });
        }

        // 服务器请求更多 token
        state.serverNeedTokens = !!result.need_tokens;
        if (result.need_tokens) {
          var deficit = Math.max(1, Math.ceil((state.serverPoolMax / 2) - state.v3Tokens.length));
          requestMultipleTokens(Math.min(deficit, 3));
        }
      } else {
        state.connected = false;
        state.lastError = 'HTTP ' + resp.status;
        state.errorCount++;

        // 如果是 401，可能是密钥错误
        if (resp.status === 401) {
          state.lastError = 'Auth failed - check extension secret';
        }
      }
    } catch(e) {
      state.connected = false;
      state.lastError = e.message || 'Connection failed';
      state.errorCount++;
    }
  }

  // ========== 消息处理 ==========
  api.runtime.onMessage.addListener(function(msg, sender, sendResponse) {
    switch (msg.type) {
      case 'TAB_READY':
        state.tabId = sender.tab ? sender.tab.id : null;
        console.log(TAG, 'Tab ready:', state.tabId);
        refreshCookies().then(function() { pushToServer(); });
        sendResponse({ ok: true });
        break;

      case 'PAGE_INIT':
        if (msg.models && msg.models.length > 0) {
          state.models = msg.models;
          console.log(TAG, 'Models received:', msg.models.length);
        }
        if (msg.pageCookies) {
          for (var k in msg.pageCookies) {
            if (!state.cookies[k]) {
              state.cookies[k] = msg.pageCookies[k];
            }
          }
          var auth = state.cookies['arena-auth-prod-v1'] || '';
          if (!auth) {
            var p0 = state.cookies['arena-auth-prod-v1.0'] || '';
            var p1 = state.cookies['arena-auth-prod-v1.1'] || '';
            if (p0) {
              auth = p0 + (p1 || '');
              state.authToken = auth;
            }
          }
        }
        pushToServer();
        sendResponse({ ok: true });
        break;

      case 'NEW_TOKEN':
        addToken(msg.token, msg.action);
        pushToServer();
        sendResponse({ ok: true });
        break;

      case 'GET_STATUS':
        cleanTokens();
        refreshCookies().then(function() {
          sendResponse({
            connected: state.connected,
            proxyUrl: state.proxyUrl,
            profileId: state.profileId,
            lastError: state.lastError,
            lastPush: state.lastPush,
            v3Count: state.v3Tokens.length,
            poolMax: state.serverPoolMax,
            hasV2: !!state.v2Token,
            hasAuth: !!state.authToken,
            hasCf: !!state.cfClearance,
            hasModels: !!(state.models && state.models.length),
            modelCount: state.models ? state.models.length : 0,
            tabId: state.tabId,
            tokensMinted: state.tokensMinted,
            tokensPushed: state.tokensPushed,
            pushCount: state.pushCount,
            errorCount: state.errorCount,
            needTokens: state.serverNeedTokens,
          });
        });
        return true;

      case 'SET_PROXY_URL':
        state.proxyUrl = msg.url;
        api.storage.local.set({ proxyUrl: msg.url });
        pushToServer();
        sendResponse({ ok: true });
        break;

      case 'SET_PROFILE_ID':
        state.profileId = msg.profileId;
        api.storage.local.set({ profileId: msg.profileId });
        pushToServer();
        sendResponse({ ok: true });
        break;

      case 'SET_EXTENSION_SECRET':
        state.extensionSecret = msg.secret;
        api.storage.local.set({ extensionSecret: msg.secret });
        pushToServer();
        sendResponse({ ok: true });
        break;

      case 'FORCE_PUSH':
        pushToServer();
        sendResponse({ ok: true });
        break;

      case 'FORCE_TOKEN':
        requestToken();
        sendResponse({ ok: true });
        break;

      case 'FORCE_MULTI_TOKEN':
        requestMultipleTokens(msg.count || 3);
        sendResponse({ ok: true });
        break;

      default:
        sendResponse({ error: 'unknown' });
    }
  });

  // ========== 定时任务 ==========
  // 智能 token 补充：根据池子状态动态调整频率
  setInterval(function() {
    cleanTokens();
    var half = Math.ceil(state.serverPoolMax / 2);
    if (state.v3Tokens.length < half || state.serverNeedTokens) {
      requestToken();
      // 如果池子很空，加速补充
      if (state.v3Tokens.length < Math.ceil(state.serverPoolMax / 4)) {
        setTimeout(function() { requestToken(); }, 3000);
      }
    }
  }, 60000);  // 每 60 秒检查

  // 每 25 秒推送一次（比原来更频繁，确保远程服务器状态新鲜）
  setInterval(function() {
    pushToServer();
  }, 25000);

  // ========== 初始化 ==========
  api.storage.local.get(['proxyUrl', 'profileId', 'extensionSecret'], function(result) {
    if (result.proxyUrl) state.proxyUrl = result.proxyUrl;
    if (result.profileId) {
      state.profileId = result.profileId;
    } else {
      // 自动生成 Profile ID
      state.profileId = generateProfileId();
      api.storage.local.set({ profileId: state.profileId });
    }
    if (result.extensionSecret) state.extensionSecret = result.extensionSecret;

    console.log(TAG, 'Proxy URL:', state.proxyUrl);
    console.log(TAG, 'Profile ID:', state.profileId);

    // 启动后立即推送
    refreshCookies().then(function() { pushToServer(); });
  });

  // 监听 tab 关闭
  api.tabs.onRemoved.addListener(function(tabId) {
    if (tabId === state.tabId) state.tabId = null;
  });

  // 监听 cookie 变化
  api.cookies.onChanged.addListener(function(info) {
    if (info.cookie.domain.indexOf('arena.ai') >= 0) {
      refreshCookies();
    }
  });

  console.log(TAG, 'Background v2.0 started');
})();
