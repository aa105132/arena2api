/**
 * Arena2API - Popup v2.0 (Firefox)
 * 支持远程服务器 URL、Profile ID、扩展密钥配置
 */
(function() {
  var $ = function(id) { return document.getElementById(id); };
  var api = typeof browser !== 'undefined' ? browser : chrome;

  function update(s) {
    if (!s) return;

    // Server connection
    if (s.connected) {
      $('srv').innerHTML = '<span class="dot dot-g"></span>Connected';
      $('srv').className = 'val ok';
    } else {
      $('srv').innerHTML = '<span class="dot dot-r"></span>Disconnected';
      $('srv').className = 'val err';
    }

    // Profile ID
    $('pid').textContent = s.profileId || 'N/A';

    // Tab
    if (s.tabId) {
      $('tab').innerHTML = '<span class="dot dot-g"></span>Active';
      $('tab').className = 'val ok';
    } else {
      $('tab').innerHTML = '<span class="dot dot-y"></span>No Tab';
      $('tab').className = 'val warn';
    }

    // Error
    $('err').textContent = s.lastError || 'None';
    $('err').className = 'val ' + (s.lastError ? 'err' : 'ok');

    // V3 Tokens
    var count = s.v3Count || 0;
    var max = s.poolMax || 30;
    $('v3').textContent = count + ' / ' + max;
    var pct = max > 0 ? Math.round((count / max) * 100) : 0;
    var bar = $('v3bar');
    bar.style.width = pct + '%';
    bar.className = 'meter-fill ' + (pct > 50 ? 'meter-g' : pct > 20 ? 'meter-y' : 'meter-r');
    $('v3').className = 'val ' + (count > 0 ? 'ok' : 'warn');

    // Auth
    if (s.hasAuth) {
      $('auth').innerHTML = '<span class="dot dot-g"></span>Yes';
      $('auth').className = 'val ok';
    } else {
      $('auth').innerHTML = '<span class="dot dot-r"></span>No';
      $('auth').className = 'val err';
    }

    // Models
    $('mdl').textContent = s.modelCount || 0;
    $('mdl').className = 'val ' + (s.modelCount > 0 ? 'ok' : 'warn');

    // Server needs tokens
    if (s.needTokens) {
      $('need').innerHTML = '<span class="dot dot-y"></span>Requesting';
      $('need').className = 'val warn';
    } else {
      $('need').innerHTML = '<span class="dot dot-g"></span>Sufficient';
      $('need').className = 'val ok';
    }

    // Stats
    $('minted').textContent = s.tokensMinted || 0;
    $('pushed').textContent = s.tokensPushed || 0;
    $('syncs').textContent = s.pushCount || 0;
    $('errs').textContent = s.errorCount || 0;
    $('errs').className = 'val ' + ((s.errorCount || 0) > 0 ? 'err' : 'ok');
  }

  function refresh() {
    api.runtime.sendMessage({ type: 'GET_STATUS' }, function(s) {
      if (!api.runtime.lastError && s) update(s);
    });
  }

  function flashButton(btn, text, duration) {
    var orig = btn.textContent;
    btn.textContent = text;
    setTimeout(function() { btn.textContent = orig; }, duration || 1000);
  }

  // Save Server URL
  $('saveUrl').onclick = function() {
    var url = $('url').value.trim();
    if (url) {
      api.runtime.sendMessage({ type: 'SET_PROXY_URL', url: url }, function() {
        flashButton($('saveUrl'), 'OK!');
        setTimeout(refresh, 500);
      });
    }
  };

  // Save Profile ID
  $('saveProf').onclick = function() {
    var pid = $('profInput').value.trim();
    if (pid) {
      api.runtime.sendMessage({ type: 'SET_PROFILE_ID', profileId: pid }, function() {
        flashButton($('saveProf'), 'OK!');
        setTimeout(refresh, 500);
      });
    }
  };

  // Save Extension Secret
  $('saveSecret').onclick = function() {
    var secret = $('secretInput').value.trim();
    api.runtime.sendMessage({ type: 'SET_EXTENSION_SECRET', secret: secret }, function() {
      flashButton($('saveSecret'), 'OK!');
      setTimeout(refresh, 500);
    });
  };

  // Open Arena.ai
  $('open').onclick = function() {
    api.tabs.create({ url: 'https://arena.ai/?mode=direct' });
    window.close();
  };

  // Get Token
  $('token').onclick = function() {
    api.runtime.sendMessage({ type: 'FORCE_MULTI_TOKEN', count: 3 }, function() {
      flashButton($('token'), 'Minting...', 2000);
      setTimeout(refresh, 3000);
    });
  };

  // Sync / Push
  $('push').onclick = function() {
    api.runtime.sendMessage({ type: 'FORCE_PUSH' }, function() {
      flashButton($('push'), 'OK!');
      setTimeout(refresh, 500);
    });
  };

  // Init: load saved settings
  api.storage.local.get(['proxyUrl', 'profileId', 'extensionSecret'], function(r) {
    $('url').value = r.proxyUrl || 'http://127.0.0.1:9090';
    $('profInput').value = r.profileId || '';
    // Don't show secret value for security
  });

  refresh();
  setInterval(refresh, 2000);
})();
