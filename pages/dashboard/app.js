/* ESM Dashboard — v0.9.0
 *
 * Uses window.AstrBotPluginPage bridge (injected by AstrBot after this
 * file loads). Backend routes are at /{PLUGIN_NAME}/<endpoint>; the
 * bridge adds the plugin-name prefix, so we pass relative paths
 * (no leading slash, no extra "page/" prefix).
 */
(function() {
  "use strict";

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

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function(c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }

  // v0.9.13: signal name → 中文 label
  var SIGNAL_ZH = {
    praise:    "收到表扬",
    thanks:    "感谢",
    friendly:  "友善互动",
    mention:   "被@提及",
    poke:      "被戳",
    technical: "技术讨论",
    question:  "提问",
    comfort:   "被安慰",
    insult:    "被辱骂",
    pressure:  "被施压",
    silence:   "沉默",
    success:   "成功",
    failure:   "失败",
    init:      "初始化",
    reset:     "重置",
  };
  function signalZh(s) { return SIGNAL_ZH[s] || s; }

  // v0.9.13: relation label → 中文
  var RELATION_ZH = {
    trusted:    "信任",
    familiar:   "熟悉",
    guarded:    "戒备",
    irritated:  "烦躁",
    attached:   "亲近",
    neutral:    "中性",
    unfamiliar: "陌生",
  };
  function relationZh(l) { return RELATION_ZH[l] || l; }

  // v0.9.13: relative time formatter ("5 分钟前" 等)
  function timeAgo(ts) {
    if (!ts || ts <= 0) return "从未";
    var sec = Math.max(0, Date.now() / 1000 - ts);
    if (sec < 30) return "刚刚";
    if (sec < 60) return Math.floor(sec) + " 秒前";
    if (sec < 3600) return Math.floor(sec / 60) + " 分钟前";
    if (sec < 86400) return Math.floor(sec / 3600) + " 小时前";
    if (sec < 86400 * 30) return Math.floor(sec / 86400) + " 天前";
    if (sec < 86400 * 365) return Math.floor(sec / (86400 * 30)) + " 个月前";
    return Math.floor(sec / (86400 * 365)) + " 年前";
  }

  // Mood → color/emoji mapping
  var MOOD_STYLE = {
    calm:      { color: "#0ea5e9", emoji: "🌊", desc: "心绪平稳，没有强烈起伏" },
    happy:     { color: "#10b981", emoji: "😊", desc: "整体氛围愉快、轻松" },
    excited:   { color: "#f59e0b", emoji: "🤩", desc: "情绪高涨，活跃度高" },
    curious:   { color: "#8b5cf6", emoji: "🤔", desc: "对话充满好奇与探索" },
    tense:     { color: "#ef4444", emoji: "😟", desc: "气氛紧绷，警戒中" },
    annoyed:   { color: "#f97316", emoji: "😤", desc: "略显不耐烦或烦躁" },
    irritated: { color: "#dc2626", emoji: "😠", desc: "明显受挫或生气" },
    quiet:     { color: "#6b7280", emoji: "😶", desc: "低活跃度，情绪平稳" },
    hurt:      { color: "#ec4899", emoji: "🥺", desc: "对话中带伤感的色彩" },
    trusted:   { color: "#10b981", emoji: "🤝", desc: "信任与亲近" },
    guarded:   { color: "#f59e0b", emoji: "🛡️", desc: "保持距离、保留意见" },
    attached:  { color: "#a855f7", emoji: "💞", desc: "情感连接紧密" },
    unfamiliar:{ color: "#94a3b8", emoji: "👋", desc: "初次接触、互相试探" },
    neutral:   { color: "#94a3b8", emoji: "😐", desc: "中性状态" },
    irritated: { color: "#dc2626", emoji: "😠", desc: "明显受挫或生气" },
  };
  function moodStyle(label) {
    if (!label) return MOOD_STYLE.neutral;
    return MOOD_STYLE[String(label).toLowerCase()] || MOOD_STYLE.neutral;
  }

  // v0.9.5: Chinese labels for emotion dimensions
  var DIM_LABEL = {
    valence:    "愉悦",
    arousal:    "唤醒",
    stress:     "压力",
    curiosity:  "好奇",
    trust:      "信任",
    affection:  "好感",
    irritation: "烦躁",
    familiarity:"熟悉",
  };
  function dimLabel(key) { return DIM_LABEL[key] || key; }

  function barColor(dim, val) {
    if (dim === "stress" || dim === "irritation")
      return val > 0.6 ? "var(--red)" : "var(--amber)";
    if (dim === "valence" || dim === "trust" || dim === "affection")
      return val > 0.5 ? "var(--green)" : "var(--amber)";
    if (dim === "curiosity" || dim === "familiarity")
      return val > 0.5 ? "var(--accent)" : "var(--text-3)";
    return "var(--accent)";
  }

  function dimBarHTML(key, val) {
    return '<div class="dim-row">' +
      '<span class="dim-label">' + esc(dimLabel(key)) + '</span>' +
      '<div class="dim-bar-bg"><div class="dim-bar-fg" style="width:' + (val*100|0) + '%;background:' + barColor(key, val) + '"></div></div>' +
      '<span class="dim-val">' + val.toFixed(2) + '</span>' +
    '</div>';
  }

  function dimCellHTML(dim, val) {
    return '<div class="dim-row">' +
      '<div class="dim-bar-bg"><div class="dim-bar-fg" style="width:' + (val*100|0) + '%;background:' + barColor(dim, val) + '"></div></div>' +
      '<span class="dim-val">' + val.toFixed(2) + '</span>' +
    '</div>';
  }

  // ---- API helpers ----
  var state = null, activeScope = null, filterQ = "";
  async function apiGet(path) {
    var b = getBridge();
    if (!b) throw new Error("bridge unavailable");
    var resp = await b.apiGet(path, {});
    if (resp && resp.status === "error") throw new Error(resp.message || "API error");
    return resp && "data" in resp ? resp.data : resp;
  }

  // ---- Render: hero ----
  function renderHero(scope) {
    var label = scope ? scope.group.label : "neutral";
    var m = moodStyle(label);
    var moodEl = document.getElementById("mood-emoji");
    var labelEl = document.getElementById("mood-label");
    var descEl = document.getElementById("mood-desc");
    var orb = document.getElementById("mood-orb");
    if (moodEl) moodEl.textContent = m.emoji;
    if (labelEl) labelEl.textContent = label;
    if (descEl) descEl.textContent = scope ? m.desc : "等待数据…";
    if (orb) {
      orb.style.setProperty("--orb-bg", "linear-gradient(135deg, " + m.color + " 0%, " + m.color + "99 100%)");
      orb.style.setProperty("--orb-shadow", m.color + "55");
    }
    var root = document.documentElement;
    if (root) root.style.setProperty("--orb-glow", m.color + "20");
    var pad = scope ? scope.group.pad : { P: 0, A: 0, D: 0 };
    var pP = document.getElementById("pad-p");
    var pA = document.getElementById("pad-a");
    var pD = document.getElementById("pad-d");
    if (pP) pP.style.width = (Math.max(0, Math.min(1, (pad.P + 1) / 2)) * 100) + "%";
    if (pA) pA.style.width = (Math.max(0, Math.min(1, (pad.A + 1) / 2)) * 100) + "%";
    if (pD) pD.style.width = (Math.max(0, Math.min(1, (pad.D + 1) / 2)) * 100) + "%";
    var pPv = document.getElementById("pad-p-v");
    var pAv = document.getElementById("pad-a-v");
    var pDv = document.getElementById("pad-d-v");
    if (pPv) pPv.textContent = pad.P.toFixed(2);
    if (pAv) pAv.textContent = pad.A.toFixed(2);
    if (pDv) pDv.textContent = pad.D.toFixed(2);
    var active = scope ? scope.group.active_users : 0;
    var trans = scope ? scope.group.transitions : 0;
    var activeEl = document.getElementById("side-active");
    var transEl = document.getElementById("side-transitions");
    if (activeEl) activeEl.textContent = active;
    if (transEl) transEl.textContent = trans;
  }

  // ---- Render: stat cards ----
  function renderStats(h, fullState) {
    var scopes = (fullState && fullState.scopes) || [];
    var totalUsers = 0;
    for (var i = 0; i < scopes.length; i++) totalUsers += scopes[i].users.length;
    var set = function(id, val) { var el = document.getElementById(id); if (el) el.textContent = val; };
    set("stat-scopes", scopes.length);
    set("stat-users", totalUsers);
    set("stat-signals", (h && h.signal_count) || "—");
    set("stat-version", (h && h.version) || "—");
    var modeEl = document.getElementById("side-mode");
    if (modeEl) modeEl.textContent = (h && h.appraisal_mode) || "—";
    var subEl = document.getElementById("brand-sub");
    if (subEl && h && h.version) subEl.textContent = "情绪状态机 · v" + h.version;
  }

  // ---- Render: groups grid ----
  function renderGroups() {
    var grid = document.getElementById("groups-grid");
    var sel = document.getElementById("scope-select");
    var visibleScopes = state && state.scopes ? state.scopes.filter(shouldShowGroup) : [];
    if (!state || !state.scopes || !state.scopes.length) {
      grid.innerHTML =
        '<div class="empty">' +
          '<div class="empty-illustration">🌱</div>' +
          '<div class="empty-title">还没有群聊活动</div>' +
          '<div class="empty-desc">机器人接收消息后，这里会显示各群的情绪快照</div>' +
        '</div>';
      if (sel) sel.innerHTML = '<option value="">所有群聊</option>';
      return;
    }
    var html = "";
    for (var i = 0; i < visibleScopes.length; i++) {
      var s = visibleScopes[i];
      var m = moodStyle(s.group.label);
      var active = (activeScope && activeScope.scope === s.scope) ? " active" : "";
      html +=
        '<div class="group-card' + active + '" data-scope="' + esc(s.scope) + '" style="--mood-color: ' + m.color + '">' +
          '<div class="group-head">' +
            '<div class="group-name" title="' + esc(s.scope) + '">' + esc(s.scope) + '</div>' +
            '<span class="label-badge" style="background: ' + m.color + '">' + esc(s.group.label) + '</span>' +
          '</div>' +
          dimBarHTML("valence", s.group.valence) +
          dimBarHTML("arousal", s.group.arousal) +
          dimBarHTML("stress", s.group.stress) +
          dimBarHTML("curiosity", s.group.curiosity) +
          '<div class="group-meta">' +
            '<span class="group-users-count">' + s.users.length + ' 用户</span>' +
            '<span title="最后被动更新 " + timeAgo(s.group.last_signal_at) + '">' + esc(signalZh(s.group.last_signal) || "—") + ' · ' + esc(timeAgo(s.group.last_signal_at)) + '</span>' +
          '</div>' +
        '</div>';
    }
    grid.innerHTML = html;

    // wire card click
    var cards = grid.querySelectorAll(".group-card");
    for (var j = 0; j < cards.length; j++) {
      (function(card) {
        card.addEventListener("click", function() {
          var sc = card.getAttribute("data-scope");
          if (sel) sel.value = sc;
          showGroup(sc);
        });
      })(cards[j]);
    }

    // select options
    var prev = sel.value;
    sel.innerHTML = '<option value="">所有群聊</option>';
    for (var k = 0; k < state.scopes.length; k++) {
      var ss = state.scopes[k];
      var opt = document.createElement("option");
      opt.value = ss.scope;
      opt.textContent = ss.scope + " · " + ss.users.length + "人";
      sel.appendChild(opt);
    }
    if (prev) sel.value = prev;
  }

  // ---- User table ----
  function showUserTable() {
    var sec = document.getElementById("user-section");
    if (!state || !state.scopes || !state.scopes.length) {
      sec.style.display = "none";
      return;
    }
    sec.style.display = "";
    // v0.9.5: filter always searches across ALL groups (ignore
    // activeScope), so a search like "alice" finds her regardless of
    // which group card the user previously clicked.
    var scopes = state.scopes;
    var html = '<div class="users-row head">' +
      '<div>用户 ID</div><div>' + esc(dimLabel("trust")) + '</div>' +
      '<div>' + esc(dimLabel("affection")) + '</div>' +
      '<div>' + esc(dimLabel("irritation")) + '</div>' +
      '<div>' + esc(dimLabel("familiarity")) + '</div>' +
      '<div>关系</div><div>最近信号 · 何时</div>' +
      '</div>';
    var totalUsers = 0;
    var q = (filterQ || "").toLowerCase().trim();
    for (var i = 0; i < scopes.length; i++) {
      var s = scopes[i];
      // If a specific group is active, optionally narrow the scope
      // when the filter is empty. With a filter, search everywhere.
      if (activeScope && !q && s.scope !== activeScope.scope) continue;
      for (var j = 0; j < s.users.length; j++) {
        var u = s.users[j];
        if (q && u.user_id.toLowerCase().indexOf(q) === -1) continue;
        if (!shouldShowUser(u)) continue;
        var m = moodStyle(u.label);
        var scopeTag = activeScope ? "" : (' <span style="color:var(--text-3);font-size:0.7rem">@' + esc(s.scope) + '</span>');
        html += '<div class="users-row">' +
          '<div class="user-id" title="' + esc(u.user_id) + '">' + esc(u.user_id) + scopeTag + '</div>' +
          '<div>' + dimCellHTML("trust", u.trust) + '</div>' +
          '<div>' + dimCellHTML("affection", u.affection) + '</div>' +
          '<div>' + dimCellHTML("irritation", u.irritation) + '</div>' +
          '<div>' + dimCellHTML("familiarity", u.familiarity) + '</div>' +
          '<div><span class="label-badge" style="background: ' + m.color + '" title="' + esc(u.label) + '">' + esc(relationZh(u.label)) + '</span></div>' +
          '<div style="font-size:0.75rem;color:var(--text-2)" title="最后被动更新 ' + esc(timeAgo(u.last_signal_at)) + '">' + esc(signalZh(u.last_signal) || "—") + ' · ' + esc(timeAgo(u.last_signal_at)) + '</div>' +
        '</div>';
        totalUsers++;
      }
    }
    if (totalUsers === 0) {
      html += '<div class="users-empty">没有匹配的用户' + (q ? '（关键字: ' + esc(filterQ) + '）' : '') + '</div>';
    }
    var tableEl = document.getElementById("users-table");
    if (tableEl) tableEl.innerHTML = html;
    var countEl = document.getElementById("user-count");
    if (countEl) countEl.textContent = totalUsers + " / " +
      scopes.reduce(function(a, s) { return a + s.users.length; }, 0) + " 个用户";
  }

  function showGroup(scopeName) {
    if (!state) return;
    var found = null;
    for (var i = 0; i < state.scopes.length; i++) {
      if (state.scopes[i].scope === scopeName) { found = state.scopes[i]; break; }
    }
    if (!found) {
      activeScope = null;
      renderHero(null);
    } else {
      activeScope = found;
      renderHero(found);
    }
    renderGroups();
    showUserTable();
  }

  // ---- Status / errors ----
  function setStatus(state_, text) {
    var chip = document.getElementById("status-chip");
    if (!chip) return;
    var t = chip.querySelector(".status-text");
    chip.setAttribute("data-state", state_);
    if (t) t.textContent = text;
  }

  function setError(msg) {
    setStatus("error", "连接失败");
    var grid = document.getElementById("groups-grid");
    if (grid) {
      grid.innerHTML =
        '<div class="empty">' +
          '<div class="empty-illustration">⚠️</div>' +
          '<div class="empty-title">加载失败</div>' +
          '<div class="empty-desc">' + esc(msg) + '</div>' +
        '</div>';
    }
  }

  // ---- Main load ----
  async function load() {
    var b = getBridge();
    if (!b) {
      setStatus("error", "插件桥不可用");
      return;
    }
    try {
      var h = await apiGet("health");
      if (h && Array.isArray(h.hidden_user_ids) && h.hidden_user_ids.length) {
        hiddenUserIds = h.hidden_user_ids;
      }
      if (h && Array.isArray(h.hidden_scope_patterns) && h.hidden_scope_patterns.length) {
        hiddenScopePatterns = h.hidden_scope_patterns;
      }
      if (h && h.active_window_seconds) {
        activeWindowSeconds = h.active_window_seconds;
      }
      state = await apiGet("state");
      setStatus("ok", "已连接");
      renderStats(h, state);
      // Hero = first scope (or null)
      renderHero(state.scopes[0] || null);
      renderGroups();
      showUserTable();
      var upd = document.getElementById("last-update");
      if (upd) upd.textContent = "更新于 " + new Date().toLocaleTimeString() +
        " · 活跃窗口 " + Math.round(activeWindowSeconds / 60) + " 分钟";
    } catch (e) {
      setError(e.message || String(e));
    }
  }

  // ---- Wire events ----
  function bindEvents() {
    bindSettingsMenu();
    var sel = document.getElementById("scope-select");
    if (sel) sel.addEventListener("change", function() {
      var v = sel.value;
      if (v) showGroup(v);
      else { activeScope = null; renderHero(null); renderGroups(); showUserTable(); }
    });
    var filter = document.getElementById("user-filter");
    if (filter) filter.addEventListener("input", function() {
      filterQ = filter.value || "";
      showUserTable();
    });
    var btn = document.getElementById("refresh-btn");
    if (btn) btn.addEventListener("click", function() {
      btn.classList.add("spinning");
      load().finally(function() { setTimeout(function() {
        btn.classList.remove("spinning");
      }, 600); });
    });
    // Re-render mood colors when theme changes
    var root = document.documentElement;
    if (root && window.MutationObserver) {
      new MutationObserver(function() {
        if (state) renderGroups();
      }).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
    }
  }

  // ---- Settings (v0.9.8) ----
  var SETTINGS_KEY = "esm_dashboard_settings_v1";
  var settings = {
    hideVersion: false,
    activeOnly: false,
    compact: false,
    nonemptyOnly: false,
    filterBot: false,
  };
  var hiddenUserIds = ["webchat"];  // populated from /health
  var hiddenScopePatterns = ["webchat:"];  // populated from /health
  var activeWindowSeconds = 300;  // populated from /health (用于「活跃」判断)
  function loadSettings() {
    try {
      var raw = localStorage.getItem(SETTINGS_KEY);
      if (raw) {
        var s = JSON.parse(raw);
        for (var k in settings) if (k in s) settings[k] = s[k];
      }
    } catch (e) {}
  }
  function saveSettings() {
    try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch (e) {}
  }
  function applySettingsToBody() {
    var b = document.body;
    b.classList.toggle("opt-hide-version", !!settings.hideVersion);
    b.classList.toggle("opt-compact", !!settings.compact);
  }
  function bindSettingsMenu() {
    var btn = document.getElementById("settings-btn");
    var menu = document.getElementById("settings-menu");
    var wrap = document.getElementById("settings-wrap");
    if (!btn || !menu) return;

    // Sync checkboxes with current settings
    var map = {
      "opt-hide-version": "hideVersion",
      "opt-active-only": "activeOnly",
      "opt-compact": "compact",
      "opt-nonempty-only": "nonemptyOnly",
      "opt-filter-bot": "filterBot",
    };
    Object.keys(map).forEach(function(id) {
      var cb = document.getElementById(id);
      if (cb) cb.checked = !!settings[map[id]];
    });

    btn.addEventListener("click", function(e) {
      e.stopPropagation();
      menu.hidden = !menu.hidden;
    });
    document.addEventListener("click", function(e) {
      if (wrap && !wrap.contains(e.target)) menu.hidden = true;
    });

    Object.keys(map).forEach(function(id) {
      var cb = document.getElementById(id);
      if (!cb) return;
      cb.addEventListener("change", function() {
        settings[map[id]] = cb.checked;
        applySettingsToBody();
        saveSettings();
        // Re-render to apply filter
        renderGroups();
        showUserTable();
      });
    });
    applySettingsToBody();
  }

  // Apply active-only / nonempty-only / webchat filters to scopes
  // "Active" = has at least one of: active_users > 0, last_signal set,
  // transitions > 0. OR logic (any one counts).
  function shouldShowGroup(s) {
    if (settings.filterBot && hiddenScopePatterns.length) {
      var sid = (s.scope || "").toLowerCase();
      for (var i = 0; i < hiddenScopePatterns.length; i++) {
        if (sid.indexOf(hiddenScopePatterns[i]) !== -1) return false;
      }
    }
    if (settings.nonemptyOnly) {
      var g = s.group || {};
      var hasActive = g.active_users > 0;
      var hasSignal = g.last_signal && g.last_signal !== "—" && g.last_signal !== "";
      var hasTransition = (g.transitions || 0) > 0;
      // Hide if NONE of these are true
      if (!hasActive && !hasSignal && !hasTransition) return false;
    }
    return true;
  }
  function shouldShowUser(u) {
    if (settings.activeOnly) {
      if (!u.last_signal || u.last_signal === "—") return false;
    }
    if (settings.filterBot && hiddenUserIds.length) {
      var uid = (u.user_id || "").toLowerCase();
      for (var i = 0; i < hiddenUserIds.length; i++) {
        if (uid === hiddenUserIds[i] || uid.indexOf(hiddenUserIds[i]) !== -1) return false;
      }
    }
    return true;
  }

  // ---- Init ----
  bindEvents();
  loadSettings();

  // Show a fallback error in the groups grid if the bridge is
  // missing, so the user sees a clear message instead of a blank
  // page.
  function showFatal(msg) {
    setStatus("error", "连接失败");
    var grid = document.getElementById("groups-grid");
    if (grid) grid.innerHTML =
      '<div class="empty">' +
        '<div class="empty-illustration">⚠️</div>' +
        '<div class="empty-title">无法连接到插件</div>' +
        '<div class="empty-desc">' + esc(msg) + '<br>请在 AstrBot 后台 → 插件详情页打开本页面</div>' +
      '</div>';
  }

  waitForBridge(5000).then(function(b) {
    if (b) {
      load();
      setInterval(load, 20000);
    } else {
      showFatal("等待 AstrBot 插件桥超时（>5秒）。请刷新页面或在 AstrBot 后台重新进入。");
    }
  });
})();
