# Changelog

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
