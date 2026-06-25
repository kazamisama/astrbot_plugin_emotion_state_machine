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

  function barColor(dim, val) {
    if (dim === "stress" || dim === "irritation")
      return val > 0.6 ? "var(--red)" : "var(--amber)";
    if (dim === "valence" || dim === "trust" || dim === "affection")
      return val > 0.5 ? "var(--green)" : "var(--amber)";
    if (dim === "curiosity" || dim === "familiarity")
      return val > 0.5 ? "var(--accent)" : "var(--text-3)";
    return "var(--accent)";
  }

  function dimBarHTML(label, val) {
    return '<div class="dim-row">' +
      '<span class="dim-label">' + esc(label) + '</span>' +
      '<div class="dim-bar-bg"><div class="dim-bar-fg" style="width:' + (val*100|0) + '%;background:' + barColor(label, val) + '"></div></div>' +
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
    for (var i = 0; i < state.scopes.length; i++) {
      var s = state.scopes[i];
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
            '<span>最新: ' + esc(s.group.last_signal || "—") + '</span>' +
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
    var scopes = activeScope ? [activeScope] : state.scopes;
    var html = '<div class="users-row head">' +
      '<div>用户 ID</div><div>trust</div><div>affection</div>' +
      '<div>irritation</div><div>familiarity</div><div>label</div><div>最近信号</div>' +
      '</div>';
    var totalUsers = 0;
    var q = filterQ.toLowerCase();
    for (var i = 0; i < scopes.length; i++) {
      var s = scopes[i];
      for (var j = 0; j < s.users.length; j++) {
        var u = s.users[j];
        if (q && u.user_id.toLowerCase().indexOf(q) === -1) continue;
        var m = moodStyle(u.label);
        html += '<div class="users-row">' +
          '<div class="user-id" title="' + esc(u.user_id) + '">' + esc(u.user_id) + '</div>' +
          '<div>' + dimCellHTML("trust", u.trust) + '</div>' +
          '<div>' + dimCellHTML("affection", u.affection) + '</div>' +
          '<div>' + dimCellHTML("irritation", u.irritation) + '</div>' +
          '<div>' + dimCellHTML("familiarity", u.familiarity) + '</div>' +
          '<div><span class="label-badge" style="background: ' + m.color + '">' + esc(u.label) + '</span></div>' +
          '<div style="font-size:0.75rem;color:var(--text-2)">' + esc(u.last_signal || "—") + '</div>' +
        '</div>';
        totalUsers++;
      }
    }
    if (totalUsers === 0) {
      html += '<div class="users-empty">没有匹配的用户</div>';
    }
    var tableEl = document.getElementById("users-table");
    tableEl.innerHTML = html;
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
    var t = chip.querySelector(".status-text");
    if (chip) chip.setAttribute("data-state", state_);
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
      state = await apiGet("state");
      setStatus("ok", "已连接");
      renderStats(h, state);
      // Hero = first scope (or null)
      renderHero(state.scopes[0] || null);
      renderGroups();
      showUserTable();
      var upd = document.getElementById("last-update");
      if (upd) upd.textContent = "更新于 " + new Date().toLocaleTimeString();
    } catch (e) {
      setError(e.message || String(e));
    }
  }

  // ---- Wire events ----
  function bindEvents() {
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

  // ---- Init ----
  bindEvents();
  waitForBridge(5000).then(function(b) {
    if (b) {
      load();
      setInterval(load, 20000);
    } else {
      setStatus("error", "等待桥超时");
    }
  });
})();
