/* ESM Dashboard — vanilla JS */

// The AstrBot Dashboard serves plugin page APIs under the plugin's
// registered prefix. Compute it from the HTML base tag (if set) or
// fall back to a relative path.
var API = location.pathname.replace(/\/pages\/.*$/, '/page');
if (!API.endsWith('/page')) {
  API = '/astrbot_plugin_emotion_state_machine/page';
}

var state = null, activeScope = null, loadCount = 0;

// ---- API helpers ----
async function apiGet(path) {
  var resp = await fetch(API + path);
  if (!resp.ok) throw new Error(path + ': ' + resp.status);
  return resp.json();
}

// ---- Tab switching ----
document.querySelectorAll('.tab').forEach(function(el) {
  el.addEventListener('click', function() {
    document.querySelectorAll('.tab').forEach(function(t) { t.classList.remove('active'); });
    el.classList.add('active');
    document.querySelectorAll('.panel').forEach(function(p) { p.classList.remove('active'); });
    document.querySelector('[data-panel="' + el.dataset.tab + '"]').classList.add('active');
    if (el.dataset.tab === 'groups') renderGroups();
  });
});

// ---- Load ----
async function load() {
  try {
    var h = await apiGet('/health');
    document.getElementById('health').textContent =
      'v' + h.version + ' · ' + h.appraisal_mode + ' · ' + h.scope_count + ' 个群';
    state = await apiGet('/state');
    renderOverview();
    if (document.querySelector('.tab[data-tab="groups"].classList.contains("active"))) renderGroups();
    var hint = document.getElementById('refresh-hint');
    if (hint) hint.textContent = '刷新: ' + new Date().toLocaleTimeString();
    loadCount++;
  } catch(e) {
    loadCount++;
    var health = document.getElementById('health');
    health.textContent = loadCount < 3
      ? '连接中… (第' + loadCount + '次)'
      : '⚠ 连接失败 — 请检查 AstrBot Dashboard 是否已重启, 或查看日志确认 register_web_api 可用';
    health.style.color = '#e65100';
  }
}

// ---- Overview ----
function renderOverview() {
  var cards = document.getElementById('overview-cards');
  if (!state) return;
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

document.getElementById('scope-select').addEventListener('change', function() {
  this.value ? showGroup(this.value) : showEmpty();
});

function showEmpty() {
  document.getElementById('group-card').innerHTML = '';
  document.getElementById('user-card').style.display = 'none';
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

// ---- Group card ----
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
  document.getElementById('group-card').innerHTML =
    '<div class="card">' +
      '<h2>' + esc(s.scope) + ' <span class="label-badge label-' + g.label + '">' + g.label + '</span></h2>' +
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

// ---- User table ----
function renderUserTable(s) {
  document.getElementById('user-card').style.display = 'block';
  filterUsers();
}

function filterUsers() {
  if (!activeScope) return;
  var q = (document.getElementById('user-filter').value || '').toLowerCase();
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
      '<td>' + esc(u.user_id) + '</td>' +
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

function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// ---- Start ----
load();
setInterval(load, 15000);
