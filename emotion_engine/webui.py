"""WebUI page generation for the emotion state machine.

Produces a single self-contained HTML page (no CDN, no npm, vanilla JS +
inline CSS) that can be served by any web route handler. The page calls
``/esm/api/state`` on load and renders all active scopes with group
emotion charts and per-user relation tables.

The page supports:
- Scope selection via dropdown
- Group emotion card with CSS bar chart + PAD badge
- User table with 5 columns (ID, trust, affection, irritation, familiarity)
- Client-side text filter for user IDs
- Auto-refresh every 15 seconds
"""

from __future__ import annotations

import json

_WEBUI_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ESM 情绪状态面板</title>
<style>
  :root {
    --bg: #1a1a2e; --card-bg: #16213e; --text: #e0e0e0;
    --accent: #0f3460; --border: #0f3460; --green: #4caf50;
    --red: #f44336; --orange: #ff9800; --blue: #42a5f5;
    --dim-positive: #4caf50; --dim-negative: #f44336;
    --dim-neutral: #ff9800;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: var(--bg); color: var(--text); padding: 24px; }
  h1 { font-size: 1.4rem; margin-bottom: 4px; }
  .meta { color: #888; font-size: 0.85rem; margin-bottom: 16px; }
  .toolbar { display: flex; gap: 12px; margin-bottom: 20px; align-items: center; }
  select, input { background: var(--card-bg); color: var(--text); border: 1px solid var(--border); padding: 8px 12px; border-radius: 6px; font-size: 0.9rem; }
  select { min-width: 200px; }
  input { flex: 1; max-width: 300px; }

  .card { background: var(--card-bg); border-radius: 10px; padding: 20px; margin-bottom: 16px; }
  .card h2 { font-size: 1.1rem; margin-bottom: 12px; color: var(--blue); }
  .dim-row { display: flex; align-items: center; margin: 8px 0; gap: 10px; }
  .dim-label { width: 80px; font-size: 0.85rem; text-align: right; color: #aaa; }
  .dim-bar-bg { flex: 1; height: 18px; background: #0d0d1a; border-radius: 9px; overflow: hidden; }
  .dim-bar-fg { height: 100%; border-radius: 9px; transition: width 0.3s; }
  .dim-val { width: 44px; font-size: 0.85rem; text-align: left; }
  .pad-badge { display: inline-flex; gap: 8px; margin-top: 12px; }
  .pad-badge span { padding: 4px 10px; border-radius: 6px; font-size: 0.8rem; background: var(--accent); }
  .group-meta { font-size: 0.8rem; color: #777; margin-top: 8px; }

  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; margin-top: 12px; }
  th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--border); }
  th { color: #888; font-weight: 500; }
  .label-badge { padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; }
  .label-calm { background: #37474f; } .label-happy { background: #2e7d32; }
  .label-tense { background: #c62828; } .label-annoyed { background: #e65100; }
  .label-curious { background: #1565c0; } .label-excited { background: #6a1b9a; }
  .label-quiet { background: #455a64; } .label-hurt { background: #880e4f; }
  .label-trusted { background: #2e7d32; } .label-guarded { background: #e65100; }
  .label-irritated { background: #c62828; } .label-attached { background: #6a1b9a; }
  .label-unfamiliar { background: #546e7a; } .label-neutral { background: #37474f; }

  .empty { color: #666; font-style: italic; padding: 40px; text-align: center; }
  .refresh { color: #888; font-size: 0.8rem; }
  @media (max-width: 768px) {
    .toolbar { flex-direction: column; align-items: stretch; }
    select, input { max-width: 100%; }
  }
</style>
</head>
<body>
<h1>🧭 Emotion State Machine</h1>
<div class="meta">情绪状态面板 — PAD 维度 | OCC 评价 | 群聊 + 用户双层关系</div>
<div class="toolbar">
  <select id="scope-select" onchange="renderScope()">
    <option value="">-- 选择一个群聊 --</option>
  </select>
  <input id="user-filter" type="text" placeholder="搜索用户 ID..." oninput="filterUsers()">
  <span class="refresh" id="refresh-hint"></span>
</div>
<div id="content"><div class="empty">加载中...</div></div>

<script>
let state = null;
let currentScope = null;

async function load() {
  try {
    const resp = await fetch('/esm/api/state');
    state = await resp.json();
    populateScopes();
    renderScope();
    document.getElementById('refresh-hint').textContent =
      `最后更新: ${new Date().toLocaleTimeString()}`;
  } catch(e) {
    document.getElementById('content').innerHTML =
      '<div class="empty">加载失败 — 请检查插件状态</div>';
  }
}

function populateScopes() {
  const sel = document.getElementById('scope-select');
  const prev = sel.value;
  sel.innerHTML = '<option value="">-- 选择一个群聊 --</option>';
  for (const s of state.scopes) {
    const opt = document.createElement('option');
    opt.value = s.scope;
    opt.textContent = `${s.scope} (${s.users.length} 人, ${s.group.label})`;
    sel.appendChild(opt);
  }
  if ([...sel.options].some(o => o.value === prev)) sel.value = prev;
}

function renderScope() {
  const scopeName = document.getElementById('scope-select').value;
  const scope = state.scopes.find(s => s.scope === scopeName);
  currentScope = scope || null;
  if (!scope) {
    document.getElementById('content').innerHTML =
      '<div class="empty">请选择一个群聊查看详情</div>';
    return;
  }
  renderGroup(scope);
  renderUsers(scope);
}

function barColor(dim, val) {
  if (dim === 'stress' || dim === 'irritation') return val > 0.6 ? 'var(--red)' : 'var(--orange)';
  if (dim === 'valence' || dim === 'trust' || dim === 'affection') return val > 0.5 ? 'var(--green)' : 'var(--orange)';
  if (dim === 'curiosity' || dim === 'familiarity') return val > 0.5 ? 'var(--blue)' : '#777';
  return 'var(--blue)';
}

function dimBar(label, val) {
  return `<div class="dim-row">
    <span class="dim-label">${label}</span>
    <div class="dim-bar-bg"><div class="dim-bar-fg" style="width:${(val*100)|0}%;background:${barColor(label,val)}"></div></div>
    <span class="dim-val">${val.toFixed(2)}</span>
  </div>`;
}

function renderGroup(scope) {
  const g = scope.group;
  const p = g.pad;
  document.getElementById('content').innerHTML = `
    <div class="card">
      <h2>🧭 ${scope.scope} <span class="label-badge label-${g.label}">${g.label}</span></h2>
      ${dimBar('valence', g.valence)}
      ${dimBar('arousal', g.arousal)}
      ${dimBar('stress', g.stress)}
      ${dimBar('curiosity', g.curiosity)}
      <div class="pad-badge">
        <span>P=${p.P.toFixed(2)}</span><span>A=${p.A.toFixed(2)}</span><span>D=${p.D.toFixed(2)}</span>
      </div>
      <div class="group-meta">
        active: ${g.active_users} 人 | signal: ${g.last_signal} | reason: ${g.last_reason} | transitions: ${g.transitions}
      </div>
    </div>
    <div class="card">
      <h2>👥 用户关系 (${scope.users.length} 人)</h2>
      <div id="user-table"></div>
    </div>`;
  filterUsers();
}

function filterUsers() {
  if (!currentScope) return;
  const q = (document.getElementById('user-filter').value || '').toLowerCase();
  const users = currentScope.users.filter(u => u.user_id.toLowerCase().includes(q));
  let html = `<table><thead><tr>
    <th>用户 ID</th><th>trust</th><th>affection</th><th>irritation</th><th>familiarity</th><th>label</th><th>last</th>
  </tr></thead><tbody>`;
  for (const u of users) {
    html += `<tr>
      <td>${u.user_id}</td>
      <td>${dimBarCell('trust', u.trust)}</td>
      <td>${dimBarCell('affection', u.affection)}</td>
      <td>${dimBarCell('irritation', u.irritation)}</td>
      <td>${dimBarCell('familiarity', u.familiarity)}</td>
      <td><span class="label-badge label-${u.label}">${u.label}</span></td>
      <td style="font-size:0.75rem;color:#888">${u.last_signal}</td>
    </tr>`;
  }
  html += '</tbody></table>';
  if (users.length === 0) html = '<div class="empty">无匹配用户</div>';
  const container = document.getElementById('user-table');
  if (container) container.innerHTML = html;
}

function dimBarCell(dim, val) {
  return `<div class="dim-row" style="margin:0">
    <div class="dim-bar-bg" style="height:12px"><div class="dim-bar-fg" style="width:${(val*100)|0}%;background:${barColor(dim,val)};height:12px"></div></div>
    <span class="dim-val" style="width:38px;font-size:0.75rem">${val.toFixed(2)}</span>
  </div>`;
}

load();
setInterval(load, 15000);
</script>
</body>
</html>"""


def render_webui_page() -> str:
    """Return the complete HTML page for the ESM WebUI.

    Default dark theme, auto-refreshes every 15 seconds, fetches data
    from ``/esm/api/state``. Use this as the response body in a web
    route handler.
    """
    return _WEBUI_HTML


def render_state_json(machine) -> str:
    """Return the full state as a compact JSON string.

    Imported here rather than from :mod:`.api` to minimize the web
    handler's import graph (the web module doesn't need to know the
    internal structure of :class:`~emotion_engine.EmotionStateMachine`).
    """
    from .api import get_full_state

    return json.dumps(get_full_state(machine), ensure_ascii=False, separators=(",", ":"))
