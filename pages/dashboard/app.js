/* ESM Dashboard — uses AstrBotPluginPage bridge SDK.
 *
 * The bridge is injected by AstrBot AFTER this <script> runs, so we
 * wait for it on init (retry every 50ms up to 5s). The bridge prefixes
 * the plugin name to relative paths like "page/health", so we never
 * hardcode the full URL.
 */
(function() {
  function getBridge() { return window.AstrBotPluginPage || null; }

  function waitForBridge(timeoutMs) {
    return new Promise(function(resolve) {
      var b = getBridge();
      if (b) { resolve(b); return; }
      var waited = 0, step = 50;
      var timer = setInterval(function() {
        var bb = getBridge();
        if (bb || waited >= timeoutMs) { clearInterval(timer); resolve(bb || null); }
        waited += step;
      }, step);
    });
  }

  // v0.8.20: per the official AstrBot plugin-pages guide the
  // endpoint passed to bridge.apiGet() should NOT include any extra
  // prefix — the bridge adds the plugin-name prefix. The backend
  // handlers are registered at /{PLUGIN_NAME}/<endpoint>, so we pass
  // "health", "state", etc. (no "page/" prefix). The previous code
  // added a "page/" prefix that the backend never used after the
  // v0.8.18 path-format switch, which is what caused "未找到该路由".
  function endpoint(path) {
    return String(path).replace(/^\/+/, '');
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }

  var state = null, activeScope = null, loadCount = 0;

  async function apiGet(path) {
    var b = getBridge();
    if (!b) throw new Error('bridge not available');
    var resp = await b.apiGet(endpoint(path), {});
    if (resp && resp.status === 'error') throw new Error(resp.message || 'API error');
    return resp && 'data' in resp ? resp.data : resp;
  }

  // ---- Tab switching ----
  function initTabs() {
    var tabs = document.querySelectorAll('.tab');
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].addEventListener('click', function() {
        var self = this;
        var allTabs = document.querySelectorAll('.tab');
        for (var j = 0; j < allTabs.length; j++) allTabs[j].classList.remove('active');
        self.classList.add('active');
        var panels = document.querySelectorAll('.panel');
        for (var k = 0; k < panels.length; k++) panels[k].classList.remove('active');
        var target = document.querySelector('[data-panel="' + self.dataset.tab + '"]');
        if (target) target.classList.add('active');
        if (self.dataset.tab === 'groups') renderGroups();
      });
    }
  }

  // ---- Load ----
  async function load() {
    var b = getBridge();
    if (!b) {
      var h = document.getElementById('health');
      if (h) { h.textContent = '⚠ AstrBot 插件桥不可用 — 请在 AstrBot 后台打开本页面'; h.style.color = '#c62828'; }
      return;
    }
    try {
      var health = await apiGet('/health');
      var h2 = document.getElementById('health');
      if (h2) h2.textContent = 'v' + health.version + ' · ' + health.appraisal_mode + ' · ' + health.scope_count + ' 个群';
      state = await apiGet('/state');
      renderOverview();
      var activeTab = document.querySelector('.tab.active');
      if (activeTab && activeTab.dataset.tab === 'groups') renderGroups();
      var hint = document.getElementById('refresh-hint');
      if (hint) hint.textContent = '刷新: ' + new Date().toLocaleTimeString();
      loadCount++;
    } catch(e) {
      var h3 = document.getElementById('health');
      if (h3) {
        h3.textContent = '⚠ ' + (e.message || e);
        h3.style.color = '#c62828';
      }
    }
  }

  // ---- Overview ----
  function renderOverview() {
    var cards = document.getElementById('overview-cards');
    if (!cards || !state) return;
    var totalUsers = 0;
    for (var i = 0; i < state.scopes.length; i++) totalUsers += state.scopes[i].users.length;
    cards.innerHTML =
      '<div class="card stat-card"><div class="num" style="color:var(--blue)">' + state.scopes.length + '</div><div class="label">活跃群聊</div></div>' +
      '<div class="card stat-card"><div class="num" style="color:var(--green)">' + state.signal_count + '</div><div class="label">信号类型</div></div>' +
      '<div class="card stat-card"><div class="num" style="color:var(--purple)">' + state.appraisal_mode + '</div><div class="label">评价模式</div></div>' +
      '<div class="card stat-card"><div class="num" style="color:var(--orange)">' + totalUsers + '</div><div class="label">用户关系数</div></div>';
  }

  // ---- Groups panel ----
  function renderGroups() {
    var sel = document.getElementById('scope-select');
    if (!sel) return;
    var prev = sel.value;
    sel.innerHTML = '<option value="">-- 选择群聊 --</option>';
    if (state) {
      for (var i = 0; i < state.scopes.length; i++) {
        var s = state.scopes[i];
        var opt = document.createElement('option');
        opt.value = s.scope;
        opt.textContent = s.scope + ' (' + s.users.length + '人, ' + s.group.label + ')';
        sel.appendChild(opt);
      }
    }
    var found = false;
    for (var j = 0; j < sel.options.length; j++) {
      if (sel.options[j].value === prev) { found = true; break; }
    }
    if (found) sel.value = prev;
    if (prev) showGroup(prev); else showEmpty();
  }

  function bindScopeSelect() {
    var sel = document.getElementById('scope-select');
    if (!sel || sel._esmBound) return;
    sel._esmBound = true;
    sel.addEventListener('change', function() {
      this.value ? showGroup(this.value) : showEmpty();
    });
  }

  function showEmpty() {
    var gc = document.getElementById('group-card');
    if (gc) gc.innerHTML = '';
    var uc = document.getElementById('user-card');
    if (uc) uc.style.display = 'none';
    activeScope = null;
  }

  function showGroup(scopeName) {
    var found = null;
    if (state) {
      for (var i = 0; i < state.scopes.length; i++) {
        if (state.scopes[i].scope === scopeName) { found = state.scopes[i]; break; }
      }
    }
    if (!found) return showEmpty();
    activeScope = found;
    renderGroupCard(found);
    renderUserTable(found);
  }

  function barColor(dim, val) {
    if (dim === 'stress' || dim === 'irritation') return val > 0.6 ? 'var(--red)' : 'var(--orange)';
    if (dim === 'valence' || dim === 'trust' || dim === 'affection') return val > 0.5 ? 'var(--green)' : 'var(--orange)';
    if (dim === 'curiosity' || dim === 'familiarity') return val > 0.5 ? 'var(--blue)' : '#777';
    return 'var(--blue)';
  }

  function dimBar(label, val) {
    return '<div class="dim-row">' +
      '<span class="dim-label">' + label + '</span>' +
      '<div class="dim-bar-bg"><div class="dim-bar-fg" style="width:' + (val*100|0) + '%;background:' + barColor(label,val) + '"></div></div>' +
      '<span class="dim-val">' + val.toFixed(2) + '</span>' +
    '</div>';
  }

  function renderGroupCard(s) {
    var g = s.group, p = g.pad;
    var gc = document.getElementById('group-card');
    if (!gc) return;
    gc.innerHTML =
      '<div class="card">' +
        '<h2>' + escapeHtml(s.scope) + ' <span class="label-badge label-' + g.label + '">' + g.label + '</span></h2>' +
        dimBar('valence', g.valence) +
        dimBar('arousal', g.arousal) +
        dimBar('stress', g.stress) +
        dimBar('curiosity', g.curiosity) +
        '<div class="pad-badge">' +
          '<span>P=' + p.P.toFixed(2) + '</span>' +
          '<span>A=' + p.A.toFixed(2) + '</span>' +
          '<span>D=' + p.D.toFixed(2) + '</span>' +
        '</div>' +
        '<div class="group-meta">active: ' + g.active_users + ' 人 | signal: ' + g.last_signal + ' | transitions: ' + g.transitions + '</div>' +
      '</div>';
  }

  function renderUserTable(s) {
    var uc = document.getElementById('user-card');
    if (uc) uc.style.display = 'block';
    filterUsers();
  }

  function filterUsers() {
    if (!activeScope) return;
    var input = document.getElementById('user-filter');
    var q = (input && input.value ? input.value : '').toLowerCase();
    var users = [];
    for (var i = 0; i < activeScope.users.length; i++) {
      if (activeScope.users[i].user_id.toLowerCase().indexOf(q) !== -1) users.push(activeScope.users[i]);
    }
    var html = '<table><thead><tr>' +
      '<th>用户 ID</th><th>trust</th><th>affection</th><th>irritation</th><th>familiarity</th><th>label</th><th>last</th>' +
    '</tr></thead><tbody>';
    for (var j = 0; j < users.length; j++) {
      var u = users[j];
      html += '<tr>' +
        '<td>' + escapeHtml(u.user_id) + '</td>' +
        '<td>' + dimCell('trust', u.trust) + '</td>' +
        '<td>' + dimCell('affection', u.affection) + '</td>' +
        '<td>' + dimCell('irritation', u.irritation) + '</td>' +
        '<td>' + dimCell('familiarity', u.familiarity) + '</td>' +
        '<td><span class="label-badge label-' + u.label + '">' + u.label + '</span></td>' +
        '<td style="font-size:0.75rem;color:var(--text-2)">' + u.last_signal + '</td>' +
      '</tr>';
    }
    html += '</tbody></table>';
    if (!users.length) html = '<div class="empty">无匹配用户</div>';
    var tb = document.getElementById('user-table');
    if (tb) tb.innerHTML = html;
  }

  function dimCell(dim, val) {
    return '<div class="dim-row" style="margin:0">' +
      '<div class="dim-bar-bg" style="height:12px"><div class="dim-bar-fg" style="width:' + (val*100|0) + '%;background:' + barColor(dim,val) + ';height:12px"></div></div>' +
      '<span class="dim-val" style="width:38px;font-size:0.75rem">' + val.toFixed(2) + '</span>' +
    '</div>';
  }

  function bindUserFilter() {
    var input = document.getElementById('user-filter');
    if (!input || input._esmBound) return;
    input._esmBound = true;
    input.addEventListener('input', filterUsers);
  }

  // ---- Init ----
  initTabs();
  bindScopeSelect();
  bindUserFilter();

  // Wait for AstrBot bridge (injected after this script runs).
  waitForBridge(5000).then(function(b) {
    if (b) {
      load();
      setInterval(load, 15000);
    } else {
      var h = document.getElementById('health');
      if (h) { h.textContent = '⚠ 等待 AstrBot 插件桥超时 — 请在 AstrBot 后台打开本页面'; h.style.color = '#c62828'; }
    }
  });
})();