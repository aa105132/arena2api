/**
 * Arena2API - Content Script (ISOLATED world) - Firefox v2.0
 *
 * 负责：
 * 1. 将 injector.js 注入到页面 MAIN world（通过 <script> 标签）
 * 2. 作为消息桥梁：injector.js (MAIN) <-> content.js (ISOLATED) <-> background.js (BG)
 */
(function() {
  'use strict';

  var TAG = '[Arena2API]';
  var api = typeof browser !== 'undefined' ? browser : chrome;
  var rid = 0;
  var pending = {};  // rid -> {resolve, reject, timer}

  // ========== 注入 injector.js 到 MAIN world ==========
  // Firefox MV2 不支持 "world": "MAIN"，需要通过 <script> 标签注入
  function injectMainWorldScript() {
    try {
      var script = document.createElement('script');
      script.src = api.runtime.getURL('injector.js');
      script.onload = function() {
        script.remove();
        console.log(TAG, 'Injector loaded into MAIN world');
      };
      script.onerror = function() {
        console.error(TAG, 'Failed to load injector.js');
        script.remove();
      };
      (document.head || document.documentElement).appendChild(script);
    } catch(e) {
      console.error(TAG, 'Inject error:', e);
    }
  }

  // ========== 向 injector.js 发消息 ==========
  function callInjector(type, data) {
    var id = 'r' + (++rid) + '_' + Date.now();
    return new Promise(function(resolve, reject) {
      var timer = setTimeout(function() {
        delete pending[id];
        reject(new Error('Injector timeout'));
      }, 20000);
      pending[id] = { resolve: resolve, reject: reject, timer: timer };
      var msg = { from: 'arena2api-content', type: type, rid: id };
      if (data) {
        for (var k in data) msg[k] = data[k];
      }
      window.postMessage(msg, '*');
    });
  }

  // ========== 监听 injector.js 响应 ==========
  window.addEventListener('message', function(event) {
    if (event.source !== window) return;
    if (!event.data || event.data.from !== 'arena2api-injector') return;

    var msg = event.data;

    // 带 rid 的响应
    if (msg.rid && pending[msg.rid]) {
      var p = pending[msg.rid];
      clearTimeout(p.timer);
      delete pending[msg.rid];
      if (msg.type.endsWith('_ERR')) {
        p.reject(new Error(msg.error || 'Unknown error'));
      } else {
        p.resolve(msg);
      }
      return;
    }

    // INIT 消息（无 rid）
    if (msg.type === 'INIT') {
      console.log(TAG, 'Injector initialized, models:', msg.models ? msg.models.length : 0);
      api.runtime.sendMessage({
        type: 'PAGE_INIT',
        models: msg.models,
        pageCookies: msg.cookies,
      });
      // 获取初始 token
      setTimeout(function() { fetchAndPushToken(); }, 2000);
    }
  });

  // ========== 获取 token 并推送给 background ==========
  function fetchAndPushToken() {
    callInjector('GET_TOKEN', { action: 'chat_submit' }).then(function(result) {
      if (result.token) {
        console.log(TAG, 'Got V3 token:', result.token.length, 'chars');
        api.runtime.sendMessage({
          type: 'NEW_TOKEN',
          token: result.token,
          action: result.action || 'chat_submit',
        });
      }
    }).catch(function(err) {
      console.warn(TAG, 'Token error:', err.message);
    });
  }

  // ========== 监听 background 请求 ==========
  api.runtime.onMessage.addListener(function(msg, sender, sendResponse) {
    if (msg.type === 'NEED_TOKEN') {
      callInjector('GET_TOKEN', { action: msg.action || 'chat_submit' }).then(function(result) {
        sendResponse({ token: result.token, action: result.action });
      }).catch(function(err) {
        sendResponse({ error: err.message });
      });
      return true;
    }

    if (msg.type === 'NEED_MODELS') {
      callInjector('GET_MODELS').then(function(result) {
        sendResponse({ models: result.models });
      }).catch(function(err) {
        sendResponse({ error: err.message });
      });
      return true;
    }

    if (msg.type === 'NEED_COOKIES') {
      callInjector('GET_COOKIES').then(function(result) {
        sendResponse({ cookies: result.cookies });
      }).catch(function(err) {
        sendResponse({ error: err.message });
      });
      return true;
    }

    if (msg.type === 'CHECK_PAGE') {
      callInjector('CHECK').then(function(result) {
        sendResponse(result);
      }).catch(function(err) {
        sendResponse({ error: err.message });
      });
      return true;
    }
  });

  // ========== 初始化 ==========
  console.log(TAG, 'Content script v2.0 loaded');

  // 注入 injector.js 到 MAIN world
  injectMainWorldScript();

  // 通知 background
  api.runtime.sendMessage({ type: 'TAB_READY' });

  // 定期检查 reCAPTCHA 是否可用
  var checkCount = 0;
  var checker = setInterval(function() {
    checkCount++;
    if (checkCount > 30) {
      clearInterval(checker);
      return;
    }
    callInjector('CHECK').then(function(result) {
      if (result.recaptcha) {
        clearInterval(checker);
        console.log(TAG, 'reCAPTCHA ready, fetching token');
        fetchAndPushToken();
      }
    }).catch(function() {});
  }, 2000);

})();
