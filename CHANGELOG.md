# Changelog

## Unreleased

## v0.10.1 - 2026-06-30

### Added

- **内置 bot 精力模型**（`main.py`）：自给自足的恢复/消耗模型，不再依赖
  外部 `astrbot_plugin_energy_system` 插件。指数恢复曲线（~100s 回满），
  消耗量与 TalkWillingness intensity 挂钩（憋得越狠消耗越多），
  sigmoid 映射（精力耗尽时近乎封停 W 累积）。
- **`get_bot_energy(scope=None)` 公开 API**：其他插件可读取 bot
  当前精力值 [0,1]，按自身逻辑调节主动行为。
- **精力按群聊隔离**：`energy_per_scope` 配置（默认 false）。开启后
  每个群独立维护精力桶——A 群把 bot 说累了不影响 B 群。
  `_energy_factor`、`tick()` 调用链全程透传 scope。
- **Dashboard 精力显示**：第 5 张 stat card，实时百分比 + 进度条 +
  三色状态（绿≥70% / 琥珀 30-69% / 红<30%）。
- **`_PUBLIC_API.md` 全文中文化**：新增 `get_bot_energy()` 条目，
  stability tier 保留。

### Changed

- **`_get_bot_energy`**：移除 `astrbot_plugin_energy_system` 懒导入，
  改为内部自恢复模型。
- **`_energy_factor`**：线性映射 → sigmoid，精力归零时压制更强。
- **`apply_self_reply_signal`**：固定消耗 0.08 → `intensity * 0.6`。

### Internal

- `_bot_energy` / `_bot_energy_last_tick` 从 float 改为 dict。
- `_cleanup_self_reply_tracking` 同步清理精力条目。
- health API 返回 `bot_energy` 字段。

## v0.10.0 - 2026-06-27

### Added

- **`to_text_part(self, scope, user_id="") -> TextPart`**（`main.py`）。
  `build_prompt_block` 的 TextPart 版本，`.mark_as_temp()` 已链。
  供 social_context 等插件直接 `append` 到
  `request.extra_user_content_parts`，避免字符串拼接把多个插件的
  内容块合并到同一个 TextPart。返回内容与 `build_prompt_block`
  字节级一致（含 `emotion_block_template` 配置生效）。

- **`apply_self_reply_signal(self, event) -> bool`**（`main.py`）。
  供 social_context（或任何"主动回复决策者"）在 judge=yes 之后
  调用的公共 async 方法。内部委托给
  :class:`TalkWillingnessState` 决定是否真的打 self_reply signal。
  失败永不抛出——只 `return False`。

- **`TalkWillingnessState` 类**（`main.py` 模块级，pure 逻辑）。
  脑科学启发的"说话欲望"累积状态机：
  - 三因素调制：时间（寂寞蓄力）/ 轮次密度（满足感）/ 自身情绪
  - 阈值反噬：越过 HIGH 后主动衰减，不打 signal（防 affection 饱和）
  - 不应期：刚触发后短时间内抑制
  - 连续上限：MAX_CONSECUTIVE 连续触发后强制回落
  - 用户打断：consecutive_apply 计数重置
  - scope 删除：清理内部 state
  无 I/O、无 plugin 状态依赖，可独立 import 测试。

- **`self_reply` signal**（`emotion_engine/defaults.py` 四张表）：
  群维度 `arousal +0.05, curiosity +0.02`；关系维度为空；
  SIGNAL_LAYER_WEIGHTS `(1.0, 0.0)`（全部走群层）。
  故意不动 affection/trust——切断 social_context 调制主动性的反馈环。

- **`_PUBLIC_API.md`**：完整的跨插件 API 契约文档，含稳定性
  分级（Stable / New Stable / Experimental / Deprecated）、
  版本兼容矩阵（social_context ↔ ESM 升级组合表）。

### Changed

- **`build_prompt_block` 对齐 `on_llm_request`**：之前绕过
  `emotion_block_template` 配置，v0.10.0 起两个入口读同一份
  template，输出字节级一致。

- **`observe_message` 增强**：跟踪每个 scope 的最近用户消息时间戳
  (`_last_user_msg_ts`) 与 5 分钟滑动窗口内的用户轮次
  (`_user_turn_ts`)。这是 TalkWillingnessState 的输入源。

- **`reset_scope` 与 HTTP `POST /<plugin>/delete/<scope>`**：
  删 scope 时同步清理 TalkWillingness 内部状态 + 用户消息时间戳
  字典，防止 dict 无界增长。

### Configuration

新增 `self_reply_settings` section（`_conf_schema.json`），含 7 项：

| Key | 默认值 | 说明 |
|---|---|---|
| `self_reply_signal_enabled` | `true` | 总开关 |
| `self_reply_signal` | `"self_reply"` | 使用的 signal 名 |
| `self_reply_refractory_seconds` | `30.0` | 不应期秒数（0 = 关闭） |
| `self_reply_threshold_low` | `0.55` | 触发区入口 |
| `self_reply_threshold_high` | `0.85` | 反噬区入口 |
| `self_reply_decay` | `0.92` | 每 tick 自然衰减系数 |
| `self_reply_max_consecutive` | `5` | 连续触发上限 |

### Internal

- `main.py` 模块顶部加 docstring 指向 `_PUBLIC_API.md`，按用途
  分组列出 API 方法名。
- `_cleanup_self_reply_tracking` 用 `getattr` 防御 `__new__` 风格的
  测试 fixture。

### Cross-plugin notes

- social_context v0.8.12+ 应该在 judge=yes 后调
  `esm.apply_self_reply_signal(event)`，并用 `esm.to_text_part()`
  替换之前的字符串拼接注入。
- 完整迁移说明见 `_PUBLIC_API.md` 的"版本兼容矩阵"section。

## v0.9.59 - 2026-06-27

### Fixed

- **情绪块不再持久化到对话历史**（`main.py:on_llm_request`）。
  v0.9.46 迁移到 `extra_user_content_parts.append(TextPart(...))` 时
  漏了 `.mark_as_temp()`，导致情绪块被 AstrBot 持久化到 conversation
  history——下次对话时 user 消息后面会带着上一轮的
  `<!-- esm:emotion-block:... -->`，污染 history。
  修复：append 后链上 `.mark_as_temp()`，与 livingmemory 的
  `memory_recall.py:286` 同样模式。`_no_save=True` 让 AstrBot 的
  internal pipeline 跳过 db 持久化，但 provider 仍把 block 发送给 LLM。

## v0.9.58 - 2026-06-26

### Fixed

- **注释里的版本号漂移**：`emotion_engine/machine.py` 和 `main.py`
  中 4 处 `v0.9.50` 注释（关于 `update_relation` 功能的）实际功能
  在 v0.9.51 才发布，统一为 `v0.9.51`。代码行为不变。

## v0.9.57 - 2026-06-26

### Changed

- **`inject_enabled` 字段的 hint 改写清楚**（`_conf_schema.json`）。
  说明：注入位置（user 消息末尾）、注入内容（群氛围+用户关系+风格提示）、
  注入示例 block、关闭后仍评估但不注入、v0.9.46 之后不再污染 LLM
  prefix cache。之前 hint 是一句「注入内容很短，只作为语气连续性参考」，
  用户看不到实际产出。

## v0.9.56 - 2026-06-26

### Changed

- **情绪块模板配置预填充默认模板**（`_conf_schema.json`）。
  `emotion_block_template` 的 `default` 从空字符串改为
  `DEFAULT_EMOTION_BLOCK_TEMPLATE` 的实际内容。这样在 AstrBot Dashboard
  编辑此配置时，会自动以中文默认模板为起点——直接改就行，不需要
  从空白开始写。hint 也补充说明：修改后只影响后续注入的 block，
  内部 label key 和 `_display_label` 翻译层不受影响。

## v0.9.55 - 2026-06-26

### Refactored

- **撤销之前 v0.9.55 把英文 label key 直接改成中文的过度设计**。
  改用分层架构：内部 key 保持英文（`calm` / `attached` 等稳定），
  显示层通过 `emotion_engine/prompt.py` 里的 `LABEL_DISPLAY` dict
  做英文 → 中文翻译映射。`format_snapshot` / `format_relation` /
  `format_group_chart` / `format_relation_chart` / `format_combined_chart`
  调用 `_display_label(label)` 输出中文。
- 收益：
  - 旧 `emotion_state.json` 向后兼容（label 仍是 `calm` 等英文）
  - 跨模块比较稳定（`if relation.label == "attached"` 仍可用）
  - 未来 i18n 只需扩展 LABEL_DISPLAY
- 翻译表（内部 key → 显示）：
  - calm→平静 annoyed→烦躁 hurt→受伤 tense→紧绷
  - excited→兴奋 happy→开心 curious→好奇 quiet→安静
  - neutral→中性 guarded→戒备 attached→亲近 trusted→信任
  - irritated→易怒 unfamiliar→陌生

## v0.9.54 - 2026-06-26

### Changed

- **所有 format 函数中文化**（`emotion_engine/prompt.py`）：
  - `format_snapshot` / `format_relation` / `format_combined_view` /
    `format_group_chart` / `format_relation_chart` / `format_combined_chart`
    全部标签中文化（标签 / 愉悦度 / 唤醒度 / 压力 / 好奇心 / 活跃用户 /
    最近信号 / 最近原因 / 状态迁移 / 更新于 / 对当前用户 / 信任 / 好感 /
    焦虑 / 熟悉度 / 综合标签 / PAD）
  - 顺手修了 UTF-8 emoji 被错误解码为 cp1252 的乱码（`ðŸ§­` → `🧭`、
    `ðŸ‘¤` → `👤`）
  - 顺手把 `.2f` 改成 `.1f`（与 v0.9.51 build_prompt_block 对齐）

## v0.9.53 - 2026-06-26

### Changed

- **默认情绪块模板改为中文版**（`emotion_engine/prompt.py`）。`DEFAULT_EMOTION_BLOCK_TEMPLATE`
  从英文 (`## Bot Emotion State / scope / combined_label / group / ...`) 改为
  中文 (`## 情绪状态 / 作用域 / 综合标签 / 群情绪 / ...`)。`relation_block`
  也跟着中文化：`towards_current_user` → `对当前用户`，`trust` → `信任`，
  `affection` → `好感`，`irritation` → `焦虚`，`familiarity` → `熟悉度`，
  `unavailable` → `无关系数据`。英文标签（calm/attached 等）和
  style_hint（balanced and natural）保持不变——这些是 labels 模块的
  查表结果，需要单独翻译 labels.py 才能全中文化。`_conf_schema.json`
  的 `emotion_block_template` hint 同步标注「默认中文」。

## v0.9.52 - 2026-06-26

### Added

- **情绪块模板可编辑**（参考 social_context 的 `reply_prompt_template` 模式）。
  `emotion_engine/prompt.py` 拆出 `DEFAULT_EMOTION_BLOCK_TEMPLATE` 常量和
  `_build_emotion_block_variables()` 变量构建函数；`build_prompt_block`
  新增 `template` 参数。`_conf_schema.json` 加 `emotion_block_template: text`
  配置项，`main.py:on_llm_request` 从 config 读模板传入。20 个占位符
  全部可替换（scope / combined_label / style_hint / group/relation 字段）。
  留空 → 用默认模板；自定义 → admin 在 AstrBot Dashboard 直接编辑无需改代码。

## v0.9.51 - 2026-06-26

### Added

- **Relation 分流**（`emotion_engine/machine.py` / `main.py:observe_message`）。
  `apply_interaction(scope, user_id, event, *, update_relation=True)` 新增
  `update_relation` 参数；`observe_text` 同步传递。`observe_message` 加
  `apply_to_relation = mentioned or is_private` 判断——非 @bot、非私聊的群聊
  普通消息只更新 group 情绪，不污染 user relation。修复"用户A骂用户B
  让 bot 对 A 的 trust 下降"的语义悖论。后续 v0.9.52+ 可加
  `bot_replied_recently` 触发条件（需 on_llm_response hook 追踪回复时间）。

### Changed

- **数值截断**（`emotion_engine/prompt.py:build_prompt_block`）。11 个数值字段
  从 `.2f` 改成 `.1f`（valence / arousal / stress / curiosity / pad P/A/D /
  trust / affection / irritation / familiarity）。LLM 对 0.56 vs 0.6 无感，
  但 prefix cache 抖动减半——平静期 cache 命中率显著提升。

## v0.9.50 - 2026-06-26

### Fixed

- **Settings 菜单"过滤 webchat"checkbox 状态没同步**。v0.9.49 默认隐藏生效了，
  但 checkbox 显示未勾选——因为 `bindSettingsMenu()` 在文件加载时同步过一次
  checkbox（此时 `settings.filterBot=false`），随后 `load()` 异步拿到 health
  并用 `filter_bot_default=true` 覆盖 settings，但 UI 没刷新。修复：抽
  `syncSettingsCheckboxes()` 函数，load() 覆盖 settings 后重新调一次同步。

## v0.9.49 - 2026-06-26

### Fixed

- **隐藏默认值终于生效**。`hidden_user_ids` / `hidden_scope_patterns` 这两个 schema
  默认值（`webchat` / `webchat:`）以前永远不生效——因为 `shouldShowGroup` /
  `shouldShowUser` 把 hidden 列表的过滤逻辑 gate 在 `settings.filterBot` 之后，
  而 `settings.filterBot` 默认 false。新增 `filter_bot_default: bool`（默认 true）
  配置项，后端 `/health` 把它推给前端；前端在**首次访问**（localStorage 没有
  `_esm_initialized` 标记）时用后端默认值覆盖 `settings.filterBot`。之后用户的
  toggle 选择会被记住并保留——避免每次刷新都被覆盖。

## v0.9.48 - 2026-06-26

### Changed

- **人格隔离改为开关**（`_conf_schema.json` / `main.py:_scope_id`）。
  之前用字符串配置 `persona_stamp`（填「default」= 隔离，填空 = 不隔离），
  语义模糊。新增 `persona_isolation_enabled: bool`，默认 **true**，配 `persona_stamp`
  仍保留作为 persona_manager / 会话配置都查不到时的兜底。`_scope_id` 加开关判断：
  关掉 → 不拼 persona stamp，所有人格共享 namespace（与 v0.9.22 之前行为一致）；
  开 → 沿用 `_resolve_event_persona` 三级 fallback（session_service_config →
  conversation_manager → persona_manager → persona_stamp 兜底）。`/emotion_state`
  配置快照同时显示开关和兜底值，便于诊断。

## v0.9.47 - 2026-06-26

### Changed

- **配置页重构为分框布局**（`_conf_schema.json`）。参考 engram 的 `xxx_settings` 模式，把原来的扁平 17 项配置按主题分到 5 个框：
  - **基础开关**：enabled / only_group / persist_state
  - **Prompt 注入**：inject_enabled / appraisal_mode / persona_stamp
  - **状态机调参**：decay_half_life_seconds / active_window_seconds / dilution_exponent
  - **持久化与 TTL**：state_path / save_interval_seconds / relation_ttl_seconds / group_ttl_seconds
  - **信号与可见性**：disabled_signals / hidden_user_ids / hidden_scope_patterns
- `appraisal_mode` 选项字段从 `choices` 改成 `options`（与 engram 对齐，AstrBot Dashboard 两种都识别）。

### Fixed

- **schema 重复 key**：`active_window_seconds` 之前在 schema 中重复出现两次（默认值 300 / 1800），实际 main.py 只用 300。合并为唯一项，默认 300。

## v0.9.46 - 2026-06-26

### Changed

- **情绪块注入迁移到 `extra_user_content_parts`**（`main.py:on_llm_request`）。
  之前 ESM 把情绪块拼到 `request.system_prompt` 末尾，导致每条消息的动态数值（V/A/S/C + T/Aff/Irr/Fam）污染 LLM prefix cache（OpenAI 自动缓存、Anthropic cache_control、DeepSeek/vLLM prefix cache 全军覆没）。新方案把块作为 `TextPart` 追加到 `request.extra_user_content_parts`——这是 AstrBot 官方推荐机制（参见 `astr_main_agent._append_image_caption`），块落在 user 消息之后，**不污染 prefix cache**，且 LLM 在生成前最后看到状态（近因效应）。旧版 AstrBot 没暴露 `extra_user_content_parts` 时自动 fallback 到原 system_prompt 路径。
- **用户表加"人格"列**（`app.js:showUserTable`）。从 `splitScope(scope)[1]` 取 persona stamp，无 stamp 时显示 `(无)`。CSS grid 从 7 列改 8 列。
- **群聊卡片顶部色条改用 `border-top`**（`index.html:.group-card`）。删 `::before` 伪元素——直角矩形与卡片圆角视觉脱节；`border` 天然跟 `border-radius` 协调。

## v0.8.2 - 2026-06-24

### Changed

- `metadata.yaml` 移除 `repo:` 字段。AstrBot Dashboard 自身在插件市场里调 `cloud.astrbot.app/api/v1/github/repo-info` 会被云端 CORS 拒掉（origin = `http://127.0.0.1:6185`），日志里一片 `Failed to load resource: net::ERR_FAILED`。移除后 Dashboard 不会再为 ESM 触发这个请求。其他插件仍可能有同样问题——等 AstrBot 云端修 CORS 后再加回。

## v0.8.1 - 2026-06-24

### Added

- **人格隔离**（`persona_stamp` 配置项）。留空时同群所有人格共享情绪状态（与 v0.8.0 行为一致）；
  填入字符串后 scope key 变为 `"<group_id>:<stamp>"`，不同人格独立维护 group + relation。
- `_render_config_snapshot` 显示当前 `persona_stamp`（空时显示 `(none — shared)`）。
- `_conf_schema.json` 新增 `persona_stamp` 配置项。

### Changed

- `_scope_id` 逻辑增加 stamp 拼接，空 stamp 时零行为变化。

### Fixed

- Dashboard CSS 改为浅色主题（`#f5f5f7` 底 + 白色卡片），修复深色模式在 AstrBot 浅色主题下不可见的问题。
- `app.js` API 路径从 `location.pathname` 推导（替代硬编码绝对路径），修复子路径部署时连接失败的问题。
- `app.js` 改用 `var` 语法（兼容老版 WebView），加 XSS 防护 `esc()`，加重试提示。

## v0.8.0 - 2026-06-24

### Added

- **AstrBot Dashboard 集成**。通过 `context.register_web_api` 注册 API 路由，
  前端拆为 `pages/dashboard/index.html` + `app.js` + `styles.css`，AstrBot
  自动托管 `pages/` 目录。
  - 两个标签页：总览（群聊数/信号数/模式/用户数统计卡片）和群聊状态（scope 选择器 +
    群情绪卡片 + 用户关系表格 + 搜索过滤）。
  - API 端点：`/page/health`、`/page/state`、`/page/state/<scope>`。
- `emotion_engine.__version__` 常量（`"0.8.0"`）。
- 旧 WebUI（`/esm/` + `render_webui_page()`）完整保留，与新 Dashboard 互不冲突。

### Changed

- `main.py`: `__init__` 末尾调用 `_register_official_page_api_if_available()`。
- `page_api.py`: 已预置，`PluginPageApi.register_routes()` 注册 3 个端点。

## v0.7.0 - 2026-06-24

### Added

- **WebUI 仪表板**。自包含 HTML 单页应用（无 CDN，无 npm），按群聊 scope 切换查看 group 情绪 + 用户关系。CSS bar chart + PAD 徽章 + 用户搜索过滤 + 15s 自动刷新。
- 路由 `/esm/` + `/esm/api/state`；公开 API `get_webui_page()` / `get_state_json()` / `register_web_routes(router)`。
- 新模块 `api.py`（`get_full_state`）+ `webui.py`（页面生成）。
- 命令 `/emotion_web`。

### Changed

- `main.py` 新增 web 路由注册和公开 WebUI 方法。

## v0.6.0 - 2026-06-24

### Added

- **PAD 模型对齐**（Mehrabian & Russell, 1974）。`compute_pad(snapshot)` 从群维度映射到 PAD 三维（Pleasure=valence, Arousal=arousal, Dominance=1-stress）。不改变内部存储，作为衍生视图加入 prompt block 和 chart 输出。
- **ASCII bar chart 可视化**。新增 `/emotion_chart` 命令，输出横条图 + PAD 值，比 `/emotion_state` 更直观。对应函数 `format_group_chart` / `format_relation_chart` / `format_combined_chart` 全部导出为公共 API。
- **Prompt block 新增 PAD 行**。`pad: P=0.78 A=0.55 D=0.70` 注入到 LLM 请求，供下游模型利用 PAD 维度调节对话策略。

### Changed

- `prompt.py` 新增 `compute_pad` / `_bar` / `format_group_chart` / `format_relation_chart` / `format_combined_chart`。
- `build_prompt_block` 输出中新增 `pad:` 行。
- `__init__.py` 重导出 6 个新公共符号。
- `main.py` 新增 `/emotion_chart` 命令处理函数。

## v0.5.0 - 2026-06-24

### Added

- **OCC 评价层**（Ortony, Clore & Collins 1988）。引入了 `appraisal_mode` 配置项，三种策略：
  - `"direct"`（默认）— v0.4.0 直接查表，零行为变化。
  - `"occ_static"` — OCC 两层查表：signal → appraisal profile → dimension delta。比 `"direct"` 多一层「评价变量」语义，调参更细、可解释性更高。
  - `"occ_heuristic"` — OCC + 6 个纯函数启发式，零 LLM，基于文本特征 / 群状态 / 用户关系微调（详见下表）。

- **AppraisalContext 启发式**（仅 `occ_heuristic` 启用）：
  - 文本标点 / 字符重复 → 提高 arousal
  - 正向/负向 emoji → 调整 desirability / undesirability
  - 用户信任度 → 朋友夸更重、吵架用户夸打折
  - 群紧张水平 → 所有 appraisal 放大/缩小
  - 同类信号短期重复 → 习惯化（habituation）
  - 被 @ 触发 → arousal +0.10，expectedness ×0.5

- **"direct" 模式向后兼容校验**：全部 15 个 CP2 集成测试通过。`DirectEstimator` 与 v0.4.0 的 `GROUP_SIGNAL_WEIGHTS` / `RELATION_SIGNAL_WEIGHTS` 返回值 bit-identical。

- **新增公开 API**（通过 `main.py` 暴露给其他插件）：
  - `set_appraisal_mode(mode)` — 运行时切换 estimator，立刻生效

- **JSON v3 序列化**：`to_dict` 版本升至 3，多字段 `appraisal_mode` + `recent_signals`。v2 JSON 自动以 `"direct"` 模式加载，完全向后兼容。

- **配置项**：`appraisal_mode`，类型 `string`，可选值 `"direct"` / `"occ_static"` / `"occ_heuristic"`。默认 `"direct"`。

### Changed

- `EmotionStateMachine.__init__` 新增 `appraisal_mode="direct"` 参数。
- `EmotionStateMachine` 新增 `set_appraisal_mode(mode)` / `_append_recent_signal` / `_build_appraisal_context` 方法。
- `EmotionEvent` 新增可选字段 `text: str` 和 `mentioned: bool`，供 heuristic estimator 使用。
- `_render_config_snapshot` 在 `/emotion_state` 输出里新增 `appraisal_mode` 行。
- 新增子模块 `appraisal_heuristics.py`（6 个纯函数），重写 `appraisal.py`（3 个 estimator + 工厂）。

### Fixed

- `_desirability_from_emoji` 的 base=0 导致负 emoji 方向丢失的 bug（修复后正/负 emoji 都能正确双向修正 desirability 和 undesirability）。

## v0.4.0 - 2026-06-24

### Changed

- **`emotion_engine.py` 拆为 `emotion_engine/` 包**（P0：文件分层重构）。
  单文件 902 行拆成 9 个职责单一的子模块，公共 API 完全向后兼容：

  | 子模块 | 职责 |
  | --- | --- |
  | `emotion_engine.utils` | `clamp` / `normalize_scope` / `normalize_user_id` / `prune_active_users` / `active_user_dilution` 等纯工具。 |
  | `emotion_engine.defaults` | 所有出厂默认常量：`GROUP_BASELINE` / `RELATION_BASELINE` / `*_SIGNAL_WEIGHTS` / `SIGNAL_LAYER_WEIGHTS` / `KEYWORD_SIGNALS` / `QUESTION_INDICATORS` / `*_LABEL_THRESHOLDS`。 |
  | `emotion_engine.state` | `GroupEmotionSnapshot` / `UserRelationSnapshot` / `CombinedEmotionView` / `EmotionEvent` 四个 dataclass，及其 `to_dict` / `from_dict` / `normalize` 方法。 |
  | `emotion_engine.signals` | `signal_names()` + `*_SIGNAL_WEIGHTS` 重导出。 |
  | `emotion_engine.signals_classify` | 文本 → signal 推断（`infer_signals` / `dedupe_signals` + 疑问句判定）。 |
  | `emotion_engine.appraisal` | `apply_weights` —— 直接评价模式（`appraisal_mode == "direct"`）的维度 delta 应用。 |
  | `emotion_engine.labels` | `derive_group_label` / `derive_relation_label` / `derive_combined_label` / `_eval_label_condition` —— 离散标签派生。 |
  | `emotion_engine.machine` | `EmotionStateMachine` —— 编排器（get / decay / apply / observe / prune / serialize）。 |
  | `emotion_engine.prompt` | `build_prompt_block` / `style_hint_for` / `format_*` / `ESM_BLOCK_*` —— 提示块 + 哨兵 + 人类可读渲染。 |

  所有原 `from emotion_engine import X` 导入（包括测试文件和外部插件）继续工作，因为 `emotion_engine/__init__.py` 完整重导出 39 个公共符号。

### Notes

- 这一版**没有任何行为变化**。所有 smoke test（11/11）和 main.py 集成 smoke test（4/4）通过。原有 100+ 单元测试不需要修改一行代码就能继续运行。
- 分层后下一步可以独立演进每一层（比如 `appraisal.py` 之后会引入 OCC 评价变量层；`signals_classify.py` 之后会支持 `register_classifier(fn)` 钩子）。
- `defaults.py` 集中管理所有"魔法常量"，调参不再需要进 `machine.py`。

## v0.3.1 - 2026-06-14

### Fixed

- **`_cfg_float` NaN/inf hardens**:
  Hand-edited `config.json` (or programmatic writes) can carry the
  literal strings `"NaN"` / `"Infinity"` / `"-Infinity"`.
  Python `float()` accepts all three without raising, so a value
  like `half_life = NaN` was silently propagating into
  `EmotionStateMachine` and poisoning every decay_factor
  (`math.pow(2.0, -delta / NaN) = NaN`), while `half_life = inf`
  froze the entire state machine. `_cfg_float` now rejects
  non-finite values via `math.isfinite`, logs a WARNING, and
  falls back to the default. Aligns with `social_context` v0.8.4
  and `proactive_reply` v0.6.1.

### Added

- 6 new unit tests in `tests/test_plugin_api.py` covering the
  NaN / +inf / -inf string + numeric cases and a regression guard
  for normal value passthrough (including `min_value` clamp).

## v0.3.0 - 2026-06-14

### Added

- **配置项**：
  - `relation_ttl_seconds`（默认 7 天）—— 用户关系快照的最大保留时间；过期后自动从内存和 JSON 状态文件里清除。
  - `dilution_exponent`（默认 0.5，范围 0.0–2.0）—— 群聊活跃人数稀释曲线的指数。0.0=不稀释，0.5=sqrt（温和），1.0=线性（激进）。
  - `group_ttl_seconds`（默认 30 天）—— 群公共情绪快照的最大保留时间。
  - `disabled_signals`（默认 `[]`）—— 禁用的 signal 名列表。`infer_signals` 推断后过滤；`apply_signal` / `try_apply_signal` 拒绝；`/emotion_signal` 命令拒绝。
- **公开方法**：
  - `try_apply_signal(scope, user_id, signal, *, intensity, reason)` —— `apply_signal` 的安全变体，失败（未知 signal / 禁用 / intensity 非法）时返回 `None` 并记 WARNING，适合热路径调用。
  - `prune_cold_state()` —— 一次性清掉所有冷 scope + 冷 relations，返回 `{"groups_pruned": int, "relations_pruned": int}`，有剪才落盘。
  - `is_signal_enabled(signal)` —— 检查 signal 是否被 `disabled_signals` 禁用（大小写不敏感）。
  - `list_disabled_signals()` —— 返回当前禁用的 signal 名列表（已排序）。
- **模块常量导出**（`emotion_engine`）：`QUESTION_INDICATORS`、`GROUP_LABEL_THRESHOLDS`、`RELATION_LABEL_THRESHOLDS`、`ESM_BLOCK_START`、`ESM_BLOCK_END`。
- **`/emotion_state` 命令尾部**新增 `⚙ Config snapshot` 块：显示当前生效配置（含 TTL 秒数 + 天数换算、disabled_signals 列表等），方便管理员排查。
- **`build_prompt_block` 哨兵包裹**：输出用 `<!-- esm:emotion-block:start -->` / `<!-- esm:emotion-block:end -->` HTML 注释包起来，LLM 不可见但代码可定位；`_inject_emotion_block` 负责去重 / 追加 / 尾换行归一化。
- **私有 helper**：`_eval_label_condition`（label 阈值统一判定）、`_active_user_dilution`（可配置稀释曲线）、`_prune_groups`（冷 scope 剪枝）、`_prune_relations`（冷 relation 剪枝）、`_render_config_snapshot`（`/emotion_state` 配置快照）、`_ends_with_question_mark` / `_contains_interrogative`（疑问句判定）。
- **测试**：从 27 → 101+（破百），全绿。

### Changed

- **`infer_signals` 疑问句判定收紧**：不再对句中裸 `?` 触发 `question` 信号；只在末尾 `?` / `？` 或包含中文疑问词 / 语气短语 / 句末 `吗` 时触发。
- **`prune_active_users` 原地变更**：不再 `return {**}` 重建 dict；空 dict 走 fast path；调用方持有的 dict 对象身份不变。
- **`derive_group_label` / `derive_relation_label` 阈值外置**：魔数 0.68 / 0.42 / 0.55 / 0.66 等抽到模块级 `GROUP_LABEL_THRESHOLDS` / `RELATION_LABEL_THRESHOLDS` dict，命名约定 `<dim>_min` / `<dim>_max`。
- **`from_dict` 不再自动调 `_prune_groups`**：保持纯数据加载语义；生产调用方（插件 `_load_state`）显式触发。
- **`/emotion_state` 命令输出格式**：末尾追加 config snapshot（见上）。
- **CHANGELOG 维护习惯**：引入 `## Unreleased` 段，未版本化的开发中变更挂在这里。

### Fixed

- **`relations` 字典无界增长** → 冷用户关系按 `relation_ttl_seconds` 自动清理；过期 relation 丢弃后用户重新出现会从 baseline 起步。
- **`active_user_dilution` 硬编码 `1/sqrt(n)`** → 改为 `1/n^dilution_exponent`，用户可调。
- **`apply_signal` 抛 `ValueError` / `TypeError` 在消息路径上可能炸** → 新增 `try_apply_signal` 安全变体；并加 `intensity` 校验（非数字 → `TypeError`、NaN → `ValueError`、越界 clamp）。
- **`infer_signals` 对 `?` 过度敏感** → 末尾 `?` 或疑问词才触发（见 Changed）。
- **冷 scope 在 JSON 状态文件里持续膨胀** → 加载时 + 写盘前 + `prune_cold_state()` 三处剪枝。
- **`_cfg_bool` 中文字符串分支** → 加 docstring 说明存在目的（手编 `config.json` 中文用户友好兜底）。
- **`build_prompt_block` 多次注入产生重复 block** → 哨兵 + `_inject_emotion_block` 去重，invariant "system_prompt 里恰好一个 emotion block"。

## v0.2.0 - 2026-06-14

### Added

- 公开插件 API：其他插件可以通过 `context.get_registered_star("astrbot_plugin_emotion_state_machine")` 获取本插件实例，并调用以下方法：
  - `get_scope(event)` — 从 AstrBot event 计算状态 scope key（其他插件必须用这个方法以保持 scope 一致）。
  - `get_combined_state(scope, user_id, *, apply_decay=True)` — 读取合成情绪视图（group + relation + label）。
  - `get_group_state(scope, *, apply_decay=True)` — 仅读取群公共情绪快照。
  - `get_relation_state(scope, user_id, *, apply_decay=True)` — 仅读取用户私有关系快照。
  - `observe_text(scope, text, *, user_id, mentioned)` — 从原始文本推断信号并应用。
  - `apply_signal(scope, user_id, signal, *, intensity, reason)` — 手动施加已知 signal（未知名称抛 `ValueError`）。
  - `reset_scope(scope)` — 重置整个 scope（group + 所有 relations，行为与 `/emotion_reset` 一致）。
  - `force_decay(scope, *, now=None)` — 立即对群公共情绪执行一次衰减，`now` 可选。
  - `build_prompt_block(scope, user_id)` — 生成与内置 LLM 注入相同的 prompt block。
  - `render_state_text(scope, user_id)` — 人类可读状态文本（与 `/emotion_state` 一致）。
  - `list_signals()` — 返回所有支持的 signal 名称。
- 所有 public API 内部统一调用 `normalize_scope` / `normalize_user_id`，外部传入的字符串无需自行 trim。
- 新增 `tests/conftest.py`：在测试环境注入轻量 `astrbot.api` 桩模块，使 `main.py` 可被纯 pytest 加载。
- 新增 `tests/test_plugin_api.py`：14 个测试覆盖 public API 的 scope 计算、读写、prompt 生成、信号校验、衰减与重置。
- README 增加"Public API for other plugins"章节。

## v0.1.0 - 2026-06-13

### Added

- 新增分层情绪状态机核心：`GroupEmotionSnapshot` + `UserRelationSnapshot`。
- 群聊公共情绪维度：`valence`、`arousal`、`stress`、`curiosity`。
- 用户私有关系维度：`trust`、`affection`、`irritation`、`familiarity`。
- Signal 分流权重：同一事件同时作用于 group 和 relation，但权重不同。
- 群聊稀释机制：群公共情绪按 `1 / sqrt(active_users)` 稀释，降低单个用户对整体群状态的污染。
- LLM 请求前注入低噪声合成状态块：group state + current user relation。
- 命令：`/emotion_state`、`/emotion_signal`、`/emotion_prompt`、`/emotion_reset`。
- JSON 持久化，支持从 v1 单层 `states` 迁移到 v2 `groups + relations`。
- 单元测试覆盖 signal 推断、分层更新、活跃用户稀释、用户关系隔离、衰减、序列化和 prompt 生成。
