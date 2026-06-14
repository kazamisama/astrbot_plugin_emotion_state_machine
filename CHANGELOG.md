# Changelog

## Unreleased

> 下一个版本的待发布变更。

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
