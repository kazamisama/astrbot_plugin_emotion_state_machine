# ESM 公开 API 参考

> v0.10.0+ — 跨插件互操作契约文档。
>
> 其他插件通过 `context.get_registered_star("astrbot_plugin_emotion_state_machine")` 获取 ESM 实例，然后调用本文档列出的方法。**未列出的方法均为内部实现细节**，可能随时变动。

## 稳定性分级

| 等级 | 含义 |
|---|---|
| **稳定（Stable）** | v0.x 期间向后兼容。可增参数，不改签名。 |
| **新增稳定（New Stable）** | v0.10.0 新增，后续保持同样稳定性承诺。 |
| **实验性（Experimental）** | 可能在小版本间变动。若非必要勿依赖，除非你的插件也随 ESM 同步升级。 |
| **已废弃（Deprecated）** | v0.11+ 移除。请尽快迁移。 |

---

## 读取状态

### `get_scope(event) -> str` — 稳定

根据 AstrBot 事件计算规范化的 scope 键。**务必使用此方法**——其他插件不得通过 `event.get_group_id()` 自行推导 scope，否则可能与内置 observer 落点不一致。

```python
machine = self.context.get_registered_star("astrbot_plugin_emotion_state_machine")
scope = machine.get_scope(event)
```

### `get_combined_state(scope, user_id="", *, apply_decay=True) -> CombinedEmotionView` — 稳定

完整快照：群氛围 + 用户关系 + 综合标签。**这是主要的读取 API**。其他插件应优先使用此方法，而非低层辅助方法。

### `get_group_state(scope, *, apply_decay=True) -> GroupEmotionSnapshot` — 稳定

仅群氛围（valence / arousal / stress / curiosity / PAD）。不需要用户关系时使用。

### `get_relation_state(scope, user_id, *, apply_decay=True) -> UserRelationSnapshot` — 稳定

仅用户关系（trust / affection / irritation / familiarity）。

### `get_bot_energy() -> float` — 新增稳定（v0.10.x+）

返回 bot 当前的精力值，范围 `[0.0, 1.0]`。精力以 ~0.01/秒 的速率缓慢恢复（100 秒回满），每次 self_reply signal 实际触发消耗 0.08。

其他插件可以读取此值来按 bot 疲劳程度调节自己的主动行为决策。

```python
esm = self.context.get_registered_star("astrbot_plugin_emotion_state_machine")
if esm and hasattr(esm, "get_bot_energy"):
    stamina = esm.get_bot_energy()
    # stamina == 1.0 → 精力充沛，可以活跃
    # stamina < 0.3  → 疲劳，建议少说话
```

### `render_state_text(scope, user_id="") -> str` — 稳定

人类可读的渲染输出，与 `/emotion_state` 命令输出一致。适用于调试/日志行。

### `list_signals() -> list[str]` — 稳定

所有合法的 signal 名称。用于在 `apply_signal` 调用前做校验。

### `is_signal_enabled(signal) -> bool` — 稳定

大小写不敏感的检查（是否在 `disabled_signals` 配置中）。对未知 signal 返回 `False`（防御性默认）。

### `list_disabled_signals() -> list[str]` — 稳定

当前被禁用的 signal 名称列表（已小写排序）。

---

## 构建 prompt 块

### `build_prompt_block(scope, user_id="") -> str` — 稳定

情绪块的原始字符串。遵循 `emotion_block_template` 配置（v0.10.0 与 `on_llm_request` 对齐）。返回内容与 `on_llm_request` 注入内容一致。

### `to_text_part(scope, user_id="") -> TextPart` — 新增稳定（v0.10.0+）

情绪块的 `TextPart` 版本（已链式调用 `.mark_as_temp()`）。供其他自行构建 `request.extra_user_content_parts` 列表的插件使用。每个插件的 block 作为独立 TextPart 落位，而非字符串拼接合并。

```python
# social_context judge 通道
extra_parts.append(esm.to_text_part(scope, user_id))
```

---

## 写入状态

### `observe_text(scope, text, *, user_id="", mentioned=False, update_relation=True) -> CombinedEmotionView` — 稳定

从原始文本推断 signal 并应用。与 `observe_message` 使用同一引擎。

### `apply_signal(scope, user_id, signal, *, intensity=1.0, reason="external") -> CombinedEmotionView` — 稳定

严格变体——未知 signal 会抛出 `ValueError`。成功后状态持久化。

### `try_apply_signal(scope, user_id, signal, *, intensity=1.0, reason="external") -> CombinedEmotionView | None` — 稳定

安全变体——`ValueError` / `TypeError` 返回 `None` 而非抛出。适用于频率较高的调用路径。

### `apply_self_reply_signal(event) -> bool` — 新增稳定（v0.10.0+）

由 `social_context`（或其他主动回复决策者）在 bot 决定回复后立即调用。内部委托给 `TalkWillingnessState` 累积模型决定是否实际应用 `self_reply` signal。

**调用约定**：
- 返回 `True` 当且仅当 `self_reply` signal 确实被应用到 bot 的状态机
- 以下情况静默返回 `False`（不抛异常）：配置禁用、用户 @ 触发、非触发区间、反噬区间、连续触发上限、signal 被禁用、scope 不存在、内部错误
- 永不打断调用方流程——异常被捕获并记入 `debug` 日志

应用的 signal 为 `self_reply`（仅影响群氛围 `arousal` 和 `curiosity`；**不碰关系层维度**——这是有意为之，目的在于切断 social_context ↔ ESM 反馈环）。

```python
# social_context 侧
esm = self.context.get_registered_star("astrbot_plugin_emotion_state_machine")
if esm and hasattr(esm, "apply_self_reply_signal"):
    await esm.apply_self_reply_signal(event)
```

### `decay(scope, *, now=None) -> GroupEmotionSnapshot` — 实验性

手动推进衰减时钟。适用于测试和时间旅行回放。

### `reset_scope(scope) -> GroupEmotionSnapshot` — 稳定

完全重置一个 scope（群组 + 所有关系）。持久化。效果等同于 `/emotion_reset` 命令。v0.10.0+ 同时清除该 scope 的 TalkWillingness 状态。

### `force_decay(scope, *, now=None) -> GroupEmotionSnapshot` — 稳定

强制执行衰减 + 持久化。与 `decay` 相同但总是落盘。

### `prune_cold_state() -> dict[str, int]` — 稳定

清理超时的群组与关系。返回 `{"groups_pruned": int, "relations_pruned": int}`。仅在实际清理了数据时才持久化。

### `set_appraisal_mode(mode) -> None` — 稳定

运行时切换评价模式。`mode` ∈ `"direct"` / `"occ_static"` / `"occ_heuristic"`。

---

## 类级别（非实例绑定）

### `TalkWillingnessState`（模块级） — 新增稳定（v0.10.0+）

纯逻辑的 self-reply 累积状态机。可直接 import 而不需要启动插件实例。完整接口见类的 docstring。

```python
from main import TalkWillingnessState
tw = TalkWillingnessState()
W, should_apply, intensity = tw.tick(...)
```

---

## 已移除 / 不对外暴露

以下为内部实现细节。请勿从其他插件调用：

- `_inject_emotion_block`（供 `on_llm_request` 使用的模块级辅助方法）
- `_cfg_str` / `_cfg_float` / `_cfg_bool` / `_cfg_int` / `_cfg_list`（配置类型转换）
- `_resolve_event_persona` / `_scope_id` / `_bot_persona_name`（scope 推导）
- `_save_state` / `_load_state` / `_migrate_scope_ids_if_needed`（持久化）
- `_register_official_page_api_if_available`（路由注册）
- `_cleanup_self_reply_tracking`（内部清理）
- 所有 `_` 前缀（单下划线）的方法

---

## 版本兼容矩阵

| 插件组合 | 情绪注入 | Self-reply signal |
|---|---|---|
| social_context v0.8.11 + ESM v0.9.x | ✅（字符串拼接） | ❌ 不可用 |
| social_context v0.8.11 + ESM v0.10.0+ | ✅（字符串拼接） | ✅（social_context 未调用，但 API 就绪） |
| social_context v0.8.12 + ESM v0.9.x | **❌ 损坏**（无 `to_text_part`） | ❌ 不可用 |
| social_context v0.8.12 + ESM v0.10.0+ | ✅（`to_text_part`） | ✅（social_context 调用 `apply_self_reply_signal`） |

**强烈建议**：先安装 ESM v0.10.0+，再安装 social_context v0.8.12+。

---

## v0.10.0 变更摘要

### 新增
- `to_text_part(scope, user_id) -> TextPart` — `build_prompt_block` 的 TextPart 版本，可直接注入
- `apply_self_reply_signal(event) -> bool` — 供主动回复决策者调用
- `TalkWillingnessState` 模块级类 — 纯逻辑的脑科学启发累积器
- `get_bot_energy() -> float` — 公开精力查询接口，自给自足模型，无需外部插件
- `self_reply` signal — 仅影响群 `arousal`/`curiosity`，不碰关系层
- `_PUBLIC_API.md`（本文档） — 跨插件 API 契约

### 对齐
- `build_prompt_block` 现在遵循 `emotion_block_template` 配置（之前绕过）

### 内部
- `observe_message` 跟踪每个 scope 的用户消息时间戳，供 TalkWillingness 使用
- `reset_scope` 和 HTTP `POST /delete/<scope>` 清除 TalkWillingness 状态
- 新增 `self_reply_settings` 配置 section，含 7 项可调阈值
- 内置 bot 精力模型：自动恢复 + self_reply 消耗，不含外部插件依赖
