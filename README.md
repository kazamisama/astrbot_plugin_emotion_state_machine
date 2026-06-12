# AstrBot Emotion State Machine

模拟 BOT 的分层情绪状态机：从聊天文本和手动信号中提取事件，同时维护**群聊公共情绪**和**当前用户私有关系**，并可在 LLM 请求前注入低噪声状态摘要。

## 设计目标

- **轻量**：不调用额外模型，只用规则信号驱动状态机。
- **分层**：群氛围和用户关系分开，避免某个人的行为直接污染整个群关系。
- **可解释**：每个状态都有数值维度、离散标签和最近触发信号。
- **可衰减**：情绪会按半衰期逐步回到基线，避免永久污染。
- **可复用**：核心逻辑在 `emotion_engine.py`，不依赖 AstrBot，可被其他插件调用或测试。

## 分层机制

### GroupEmotionState：群聊公共情绪

| 维度 | 含义 |
| --- | --- |
| valence | 当前群整体气氛偏正/偏负 |
| arousal | 当前群活跃度/兴奋度 |
| stress | 当前群压力/攻击性/紧张度 |
| curiosity | 当前话题让 bot 感兴趣的程度 |

群公共状态会按活跃人数稀释：

```text
group_delta = raw_delta * signal_group_weight * 1 / sqrt(active_users)
```

这样单个用户的强烈事件会影响群气氛，但不会把整个群状态瞬间打崩。

### UserRelationState：用户私有关系

| 维度 | 含义 |
| --- | --- |
| trust | bot 对这个用户的信任 |
| affection | bot 对这个用户的亲近感 |
| irritation | bot 对这个用户的烦躁/警戒 |
| familiarity | bot 对这个用户的熟悉度 |

用户关系按 `scope + user_id` 独立保存：A 夸 bot 只提高 bot 对 A 的关系，不会直接提高 bot 对 B 的关系。

## Signal 分流

每个 signal 同时作用到 group 和 relation，但权重不同：

| signal | group 倾向 | relation 倾向 |
| --- | --- | --- |
| praise / thanks | 小幅改善群氛围 | 明显提高信任/亲近 |
| insult | 小幅拉高群压力 | 明显提高对该用户的 irritation |
| pressure | 拉高群压力和活跃度 | 小幅降低对该用户的 trust |
| technical / question | 提高话题 curiosity | 少量提高熟悉度或信任 |
| comfort / friendly | 改善群氛围 | 明显提高亲近和信任 |

## 命令

| 命令 | 说明 |
| --- | --- |
| `/emotion_state` | 查看当前会话的 group 状态和当前发送者 relation 状态 |
| `/emotion_signal <signal> [intensity]` | 对当前会话和当前发送者手动施加一个信号，例如 `/emotion_signal praise 1.2` |
| `/emotion_prompt` | 预览将注入 LLM 的 prompt block |
| `/emotion_reset` | 管理员命令，重置当前会话 group 状态并清空该会话下的用户关系 |

可用 signal：`comfort`、`failure`、`friendly`、`insult`、`mention`、`poke`、`praise`、`pressure`、`question`、`silence`、`success`、`technical`、`thanks`。

## Prompt 注入示例

```text
## Bot Emotion State
scope: 123456
combined_label: trusted
group: label=curious, valence=0.56, arousal=0.36, stress=0.20, curiosity=0.70, active_users=4
towards_current_user: label=trusted, trust=0.68, affection=0.57, irritation=0.11, familiarity=0.24
last_signal: group=technical, user=thanks
style_hint: relaxed and cooperative with the current user
Use this as subtle continuity only. Do not mention numeric scores unless explicitly asked.
```

## 配置

主要配置见 `_conf_schema.json`：

- `enabled`：总开关
- `only_group`：是否仅群聊生效
- `inject_enabled`：是否在 LLM 请求前注入情绪摘要
- `persist_state`：是否持久化状态
- `decay_half_life_seconds`：情绪回归基线半衰期
- `active_window_seconds`：活跃用户统计窗口，用于群聊稀释

## 下一步可扩展方向

- 接入 aiocqhttp 戳一戳 notice，将真实戳一戳映射到 `poke` 信号。
- 暴露插件间 API，让 social_context / heartflow 读取状态。
- 支持 LLM judge 对复杂消息做结构化 signal 判定。
- 增加跨群 user-global 长期关系层。
