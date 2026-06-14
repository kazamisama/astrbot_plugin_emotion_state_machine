# Changelog

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
