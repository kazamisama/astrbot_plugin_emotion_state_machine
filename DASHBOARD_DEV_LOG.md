# AstrBot Dashboard 开发日志与踩坑总结

> **作者**: webchat (橘雪莉) — 在 chiriu 的指导下完成
> **范围**: `astrbot_plugin_emotion_state_machine` WebUI 集成 (v0.8.0 → v0.9.6)
> **目标环境**: AstrBot v4.25.5

本文档记录 ESM 插件接入 AstrBot Dashboard 全过程踩过的坑与最终稳定方案，供后续插件开发者参考。

> **最新稳定版本**: v0.9.43
> **覆盖范围**: v0.8.0 → v0.9.43

---

## TL;DR — 四个关键发现

1. **路由路径**：`register_web_api("/<plugin>/<endpoint>", ...)`，**不要**带 `/page` 前缀（即使目录叫 `pages/`）
2. **stdout 不可见**：AstrBot v4.25 捕获所有 stdout，**只能用 `logger.warning()` 调试**
3. **inline critical CSS**：iframe 里外部 CSS 链路太长，**关键样式必须 inline 在 `<style>` 标签里**
4. **`window.confirm()` 被沙箱屏蔽**：AstrBot 的 iframe 没有 `allow-modals`，confirm/alert/prompt **全部静默丢弃**，必须用 DOM 弹窗或双击确认

---

## 关键三大坑（耗时最长）

### 坑 1：路由路径格式错误

| | |
|---|---|
| **症状** | WebUI 一直显示 "未找到该路由" |
| **错误猜测** | 怀疑 class name 命名、Context 实例、registered_web_apis 列表 |
| **真相** | 后端注册 `/{plugin}/page/<x>`，前端 `apiGet("page/health")`，桥加 plugin 名后变成 `/{plugin}/page/page/health`（**多了一层 `/page`**） |
| **官方文档** | 路由应是 `/{plugin}/<endpoint>`（没有 `/page` 前缀） |
| **修复** | 1) 后端改为 `/{plugin}/<x>`；2) 前端去掉 `endpoint()` 里的 `page/` 拼接 |
| **教训** | **先看官方文档再写代码**（[en-dev-star-guides-plugin-pages](https://github.com/AstrBotDevs/AstrBot/wiki/en-dev-star-guides-plugin-pages)） |

### 坑 2：500 NameError

| | |
|---|---|
| **症状** | 路由通了，handler 报 500 |
| **原因** | 在 `__init__` 内联的 handler 引用了 `__version__`，但 `main.py` 没导入 |
| **修复** | `from .emotion_engine import __version__ as _ESM_VERSION` |

### 坑 3：CSS 在 iframe 里不生效

| | |
|---|---|
| **症状** | HTML 是新的（`v0.9.2` 可见），但样式全是浏览器默认 |
| **错误猜测** | 浏览器缓存、asset_token 过期、CSP 拦截 |
| **真相** | iframe 沙箱下 `backdrop-filter`、`position: sticky` 等特性不稳定；外部 CSS 链路 (HTML → 改写 → token 验证 → 服务) 任何一环断了就白屏 |
| **修复** | **inline critical CSS** 进 `<style>` 标签，外部 CSS 作为渐进增强 |
| **教训** | iframe 项目的样式必须有 inline fallback |

---

## 次要坑（按时间顺序）

### `print()` 看不见
- **症状**：所有 `print()` 调试日志全没显示
- **真相**：AstrBot v4.25 捕获 stdout 并过滤第三方 logger
- **修复**：所有诊断改用 `logger.warning()`（不捕获，能到 console）

### `astrbot.api.web` 不存在
- **症状**：`ModuleNotFoundError: No module named 'astrbot.api.web'`
- **真相**：官方文档示例用的 `from astrbot.api.web import json_response` 是 v4.25+ 才有，v4.25.5 还没合入
- **修复**：handler 返回 plain dict，Quart 自动 JSON 编码

### `PLUGIN_NAME` NameError
- **症状**：诊断代码 `NameError: name 'PLUGIN_NAME' is not defined'`
- **真相**：`PLUGIN_NAME` 定义在 `page_api.py`，但 main.py 里诊断代码引用不到
- **修复**：诊断时用本地变量 `_PLUGIN_NAME = "astrbot_plugin_emotion_state_machine"`

### Class name 误判
- **症状**：怀疑 `<Name>Star` 命名约定（engram 用的 `HippocampusStar`）
- **修复**：改成 `EmotionStateMachineStar`（**后来证实不是问题**，`__init_subclass__` 机制不依赖名字）
- **保留**：旧名 `EmotionStateMachinePlugin` 作为 alias 保留向后兼容

### 路由 4 种 prefix 变体无效
- **症状**：注册了 12 条路径变体都进 list，但 Dashboard 还是找不到
- **真相**：问题不在路径格式，在**前端 endpoint()** 加了 `page/` 前缀
- **教训**：与其加更多变体，不如先看后端到底需要什么

### Bridge SDK 等待
- **症状**：页面加载后 `b.apiGet` 立刻调用失败
- **真相**：bridge SDK 是 AstrBot 在 HTML 返回前注入的，page script 跑得比它早
- **修复**：`waitForBridge(5000)` 等最多 5 秒

### Asset Token JWT (60s TTL)
- **症状**：页面打开 1+ 分钟后样式崩
- **真相**：AstrBot 用 JWT 保护 plugin asset，TTL 60 秒
- **修复**：cache-bust（`?v=0.9.2`）+ inline critical CSS 双保险

### Cache 缓存
- **症状**：新 HTML 推送后用户看到旧设计
- **真相**：浏览器 iframe 缓存了旧 HTML 和 CSS
- **修复**：
  1. URL 加 cache-bust query（`?v=0.9.2`）
  2. inline CSS（终极保险）
  3. 让用户 `Ctrl+Shift+R`

### 筛选不工作
- **症状**：选了某个群卡片后，搜索框只搜该群
- **真相**：`activeScope` 锁住了搜索范围
- **修复**：有筛选词 → 搜全部群；无 → 只显示当前群。多群结果加 `@群名` 标签

### PAD 标签是 P/A/D
- **症状**：用户问 P/A/D 是什么
- **修复**：改成 `愉悦/唤醒/支配` 中文 + hover tooltip 显示英文术语

---

## 关键诊断手段（按价值排序）

### 1. 官方文档（**最大价值**）
[en-dev-star-guides-plugin-pages.md](https://github.com/AstrBotDevs/AstrBot/wiki/en-dev-star-guides-plugin-pages) 给出完整正确模式：
- 路由 `/{plugin}/<endpoint>`（**不要**带 `/page`）
- 路由在 Star 的 `__init__` 里直接调 `context.register_web_api`
- 用 `astrbot.api.web.json_response` 返回（v4.25+ 才有，v4.25.5 还没有）

### 2. `Context.registered_web_apis` dump
```python
apis = self.context.registered_web_apis
ours = [r[0] for r in apis if PLUGIN_NAME in r[0]]
logger.warning(f"total={len(apis)}, our_routes={len(ours)}")
```
- `our_routes=3` → 路由进了 list，问题在 Dashboard 端
- `our_routes=0` → 路由没进 list，问题在注册侧

### 3. `logger.warning` 多通道
`print()` 在 AstrBot 里被屏蔽。所有诊断必须用 `logger.warning()`。同时保留 `print()` 给老 Debug 习惯的人。

### 4. 版本号可见徽章
在页面顶部加显眼的版本号：
```html
<span style="color: var(--accent); font-weight: 600;">v0.9.6</span>
```
这样用户能立即区分"新 HTML 加载"还是"缓存问题"。

### 5. inline critical CSS
终极 fallback：把关键样式 (`:root` 变量、布局、颜色) inline 进 HTML `<style>` 标签，外部 CSS 作为渐进增强。

---

## 最终稳定模式

### 后端 `main.py`

```python
from .emotion_engine import __version__ as _ESM_VERSION  # 必须导入
from .emotion_engine.api import get_full_state

class EmotionStateMachineStar(Star):  # 或 EmotionStateMachinePlugin（向后兼容）
    def __init__(self, context, config):
        super().__init__(context)
        # ... 业务初始化 ...
        # 直接在 __init__ 里注册 Web API（不走 page_api.py 中间层）
        self._register_official_page_api_if_available()

    def _register_official_page_api_if_available(self):
        if not hasattr(self.context, "register_web_api"):
            return
        _PLUGIN_NAME = "astrbot_plugin_emotion_state_machine"

        async def health():
            machine = self.machine
            return {
                "version": _ESM_VERSION,
                "appraisal_mode": machine.appraisal_mode,
                "scope_count": len(machine.groups),
            }

        async def full_state():
            return get_full_state(self.machine)

        try:
            self.context.register_web_api(
                f"/{_PLUGIN_NAME}/health", health, ["GET"], "ESM health",
            )
            self.context.register_web_api(
                f"/{_PLUGIN_NAME}/state", full_state, ["GET"], "ESM state",
            )
        except Exception as e:
            logger.warning(f"register_web_api raised: {e!r}")
```

### 前端 `app.js`

```javascript
// 关键：endpoint() 不要加任何前缀
function endpoint(path) {
  return String(path).replace(/^\/+/, '');
}

async function apiGet(path) {
  var b = getBridge();
  if (!b) throw new Error('bridge unavailable');
  var resp = await b.apiGet(endpoint(path), {});  // "health" 不是 "page/health"
  if (resp && resp.status === "error") throw new Error(resp.message);
  return resp && "data" in resp ? resp.data : resp;
}
```

### 前端 `index.html`

```html
<!-- critical CSS inline 在 head -->
<style>
  :root { --accent: #6366f1; /* ... */ }
  body { background: var(--bg-grad); }
  .hero { display: grid; /* ... */ }
</style>

<!-- external CSS 作为渐进增强 -->
<link rel="stylesheet" href="./styles.css?v=0.9.6">

<!-- JS -->
<script src="./app.js?v=0.9.6"></script>
```

---

## v0.9.x 新增踩坑

### 坑 4：`window.confirm()` 被 iframe 沙箱静默丢弃（重点）

| | |
|---|---|
| **症状** | 删除按钮的 `confirm("确定删除？")` 从不弹窗，也不报错，直接跳过 |
| **真相** | AstrBot 的 plugin-page iframe 没有 `allow-modals` 权限，浏览器**静默丢弃**所有 confirm/alert/prompt 调用 |
| **发现** | engram 的 v1.55 changelog 明确写了 `BUG-14: window.confirm() is suppressed by the AstrBot sandboxed iframe` |
| **尝试 1** | 用 engram 的 `_confirmInline`（40 行 DOM 弹窗）→ **页面卡加载** |
| **尝试 2** | 简化版 DOM 弹窗（innerHTML）→ **同样卡加载** |
| **尝试 3** | 直接删除（无确认）→ ✅ 工作 |
| **尝试 4** | 双击确认（点 × 变红→再点删除，3 秒超时）→ ✅ 工作 |
| **最终方案** | **双击确认**——纯 JS，零 DOM 创建，零 innerHTML，零函数调用 |
| **教训** | **任何复杂的 DOM 操作在 iframe 里都可能崩，用最简单的方案** |

#### 「正在加载 / 连接中」的终极诊断

页面卡在「加载中 / 连接中」的状态判断只需要两步：

**症状 A：topbar 正常显示了，但数据区一直 loading**
→ JS 正常加载，CSS 正常，桥连上了，是 `load()` 没完成。
→ 看 Console 有没有红色报错：`apiGet` 返回了啥。

**症状 B：整个数据区是白屏（只有 topbar），状态一直是「连接中」**
→ **JS 有语法错误**，整个脚本崩了。没有 `try/catch` 能兜住解析阶段错误。
→ 检查最后一个改动：`{}` `()` 是否平衡、`"` 转义是否正确、是否有未闭合字符串。

**症状 C：连 topbar 样式都没有，纯文本**
→ CSS 没加载。检查 `styles.css` 是否被 AstrBot 的 asset_token 机制拦截（401/404）。

**本次开发中最常见的触发模式**：
- ❌ 在 IIFE 里定义 `_confirmInline` 函数（40 行 DOM 创建）→ 页面加载循环中某处抛异常 → 症状 B
- ❌ 在 delete click handler 里写 `innerHTML = "<div style="...">"` → 转义问题 → 症状 B
- ✅ 纯 JS 的双击确认（改按钮文字和样式，零 DOM 创建）→ 正常工作

> **核心教训**: iframe 沙箱环境下，**任何 `createElement` / `innerHTML` / `appendChild` 都放在 click 事件里**，不要放在 IIFE 顶层或函数定义中。解析阶段崩溃没有 try/catch。

### 坑 5：cache-bust 忘记更新（JS 缓存永远不刷新）

| | |
|---|---|
| **症状** | v0.9.16–v0.9.30 期间所有前端改动「都不生效」 |
| **原因** | `index.html` 里 `src="./app.js?v=0.9.15"` 从 v0.9.16 开始就没变过 |
| **影响** | 三维筛选、删除按钮、中文化标签、数值左移、紧凑模式——全部被浏览器缓存屏蔽 |
| **发现** | v0.9.31 时才手动检查发现 |
| **修复** | 每次改前端先 bump `?v=` 到当前版本号 |
| **教训** | **改 App.js 必须同步改 cache-bust query param** |

### 坑 6：`_scope_id` 是固定不变的

| | |
|---|---|
| **症状** | 改了 persona 配置后老 scope 不跟着变 |
| **原因** | scope_id 是**创建时**生成的（`unified_msg_origin:persona`），后续不会自动重命名 |
| **后果** | v0.9.23 migration 把所有老 scope 永久烙印成 `:mortis` |
| **修复** | migration 改成 dedup 双 stamp（`:sherri:mortis` → `:sherri`），老 scope 保持原样 |
| **教训** | scope_id 一旦创建就固定，migration 要慎重——不要盲目追加 stamp |

### 坑 7：后端路由 `<scope>` vs `<path:scope>`

| | |
|---|---|
| **症状** | `POST /delete/<scope>` 不匹配带 `:` 的 scope 名 |
| **修复** | 改成 `<path:scope>`（Werkzeug 匹配多段路径） |
| **影响** | delete 和 state detail 两个端点 |

### 坑 8：signal_count 显示群数

| | |
|---|---|
| **症状** | 卡片「信号类型」一直显示 27（群数），实际应该是 13 |
| **原因** | `/health` 里 `signal_count = len(machine.groups)` 写成了群数 |
| **修复** | `signal_count = len(signal_names())` |

### 坑 9：活跃群判定太严格

| | |
|---|---|
| **症状** | 正常聊天但无信号的群被判为不活跃 |
| **原因** | 旧 `observe_text` 无信号时直接 return，不更新 `active_users` |
| **修复** | 每条消息都更新 `active_users` |
| **默认窗口** | 从 5 分钟延长到 30 分钟（可配置 `active_window_seconds`） |

### 坑 10：人格下拉显示会话类型

| | |
|---|---|
| **症状** | 人格下拉出现 `GroupMessageSession`、`FriendMessageSession` |
| **原因** | `splitScope` 用 `lastIndexOf(":")` 切老 scope（无 stamp 的），把 session 类型当 persona |
| **修复** | v0.9.23 migration 给所有老 scope 加 stamp |

### 坑 11：删除按钮事件绑定时常丢失

| | |
|---|---|
| **症状** | 点了 × 没反应 |
| **原因** | 多次回滚/编辑时，delBtns 的 `addEventListener` 被覆盖丢了（HTML 模板在，JS 绑定不在） |
| **修复** | 每次改 app.js 后手动确认 delBtns 绑定还存在 |

### 坑 12：persona 查找用了错误 API

| | |
|---|---|
| **症状** | 所有 scope 都是 mortis，没人用 sherri |
| **原因 1** | v0.9.24 用的 `persona_manager.default_persona` 只拿全局默认 |
| **原因 2** | v0.9.25 用的 `resolve_selected_persona(conversation_persona_id=event.conversation.persona_id)` 但 event 上不挂 conversation |
| **修复** | 照搬 engram 的 3-tier：`sp.get_async` → `conversation_manager.get_conversation(umo, cid)` → `get_default_persona_v3(umo)` |


## 核心架构回顾

```
Dashboard 父窗口
  └── /api/plug/<subpath>  (Quart 路由)
        └── _match_registered_web_api(Context.registered_web_apis, subpath)
              ↓ 命中
            await view_handler(**path_vars)
              ↓
            返回 dict → 自动 JSON 编码

plugin iframe (我们的页面)
  ├── HTML (含 critical CSS)
  ├── <script src="/api/plugin/page/bridge-sdk.js">
  │     └── 注入 window.AstrBotPluginPage
  ├── app.js
  │     └── bridge.apiGet("health") 
  │           → postMessage 给 parent
  │           → parent 调 /api/plug/<plugin_name>/health
  │           → 返回数据
```

---

## 关键教训

1. **官方文档 > 源码猜测** — 看了 30 分钟源码没看出来的问题，文档一句话点破
2. **inline CSS 是 iframe 项目的安全网** — 外部 CSS 加载链路太长
3. **`logger.warning` 是 AstrBot 唯一可靠的诊断通道** — stdout 全被屏蔽
4. **路由是 plugin-name-prefixed** — 不是 `/{plugin}/page/<x>`，是 `/{plugin}/<x>`
5. **bridge 是 postMessage**，不是直接 fetch
6. **JS handler 必须 async**（就算不 await 也要 `async def`）
7. **inline critical CSS** 即使是单页应用也要做（iframe 沙箱环境）

---

## 版本历程

| 版本 | 关键变更 |
|---|---|
| v0.8.0 | 引入 WebUI 集成 |
| v0.8.1 | persona_stamp 隔离（config 选项） |
| v0.8.7 | 4 种 prefix 变体（**无效**，但帮定位了问题） |
| v0.8.15 | 改用 `logger.warning`（发现 print 被屏蔽） |
| v0.8.17 | dump `registered_web_apis` 确认路由进了 list |
| v0.8.18 | 路由改成 `/{plugin}/<x>`（按文档） |
| v0.8.19 | 去掉 `astrbot.api.web` import（v4.25.5 没有） |
| v0.8.20 | app.js 去掉 `page/` 前缀 |
| v0.8.21 | 修 `__version__` NameError |
| v0.9.0 | UI 重设计（hero + 卡片网格） |
| v0.9.1 | 去掉 iframe 不兼容 CSS（backdrop-filter / sticky） |
| v0.9.3 | cache-bust query（`?v=0.9.3`） |
| v0.9.4 | **inline critical CSS** |
| v0.9.5 | 中文维度标签 + 跨群筛选 |
| v0.9.6 | PAD 标签中文（愉悦/唤醒/支配） |

---

## 适用场景

- 任何要给 AstrBot Dashboard 写 plugin page 的开发者
- 任何用 iframe 嵌入 web UI 的项目
- 任何 AstrBot 调试踩坑的人

---

**最后一句**：**看官方文档**。
