# AstrBot Emotion State Machine

模拟 BOT 的分层情绪状态机：从聊天文本和手动信号中提取事件，同时维护**群聊公共情绪**和**当前用户私有关系**，并可在 LLM 请求前注入低噪声状态摘要。

## 设计目标

- **轻量**：不调用额外模型，只用规则信号驱动状态机。
- **分层**：群氛围和用户关系分开，避免某个人的行为直接污染整个群关系。
- **可解释**：每个状态都有数值维度、离散标签和最近触发信号。
- **可衰减**：情绪会按半衰期逐步回到基线，避免永久污染。
- **可复用**：核心逻辑在 `emotion_engine/` 包（9 个子模块，详见 [模块结构](#模块结构)），不依赖 AstrBot，可被其他插件调用或测试。

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
| `/emotion_chart` | v0.6.0+。与 `/emotion_state` 相同数据，但以 ASCII 横条图 + PAD 三维值呈现 |
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

- `appraisal_mode`：v0.5.0 新增。情绪评价模式（`"direct"` / `"occ_static"` / `"occ_heuristic"`）。默认 `"direct"` 与 v0.4.0 行为一致；`"occ_static"` 走静态 OCC 评价变量转维度；`"occ_heuristic"` 额外启用文本/群状态/用户关系启发式。详见 [OCC 评价层](#occ-评价层-v050)。
- `enabled`：总开关
- `only_group`：是否仅群聊生效
- `inject_enabled`：是否在 LLM 请求前注入情绪摘要
- `persist_state`：是否持久化状态
- `decay_half_life_seconds`：情绪回归基线半衰期
- `active_window_seconds`：活跃用户统计窗口，用于群聊稀释

## OCC 评价层（v0.5.0）

v0.5.0 引入了三种情绪评价模式（通过 `appraisal_mode` 配置切换）：

| 模式 | 算法 | 行为变化 |
|---|---|---|
| `direct` | 信号 → 维度权重表（v0.4.0 行为） | 零变化，bit-identical |
| `occ_static` | 信号 → 10 个 OCC 评价变量 → 维度 delta | 方向一致，量级略高（+50% 以内），调参更细 |
| `occ_heuristic` | OCC + 6 个纯函数启发式 | 同 occ_static，额外从文本特征微调 |

三者的核心区别在"评价变量"层——`direct` 直接给信号分配维度权重（`praise → {valence: +0.10, ...}`），OCC 模式先给信号分配一组心理学的**评价变量**（如 praiseworthiness, desirability, blameworthiness, novelty），再通过这些变量影响维度。

评价变量 → 维度映射表（`APPRAISAL_TO_DIMENSION_GROUP / _RELATION`）是跨信号共享的——"praiseworthiness"对所有 signal 的影响系数相同，调一次生效所有信号。

### 启发式列表（仅 `occ_heuristic` 启用）

| 启发式 | 影响的变量 | 条件 |
|---|---|---|
| 文本标点 / 重复 / 长度 | arousal | 感叹号、问号、叠字、长文 |
| emoji 极性 | desirability / undesirability | 正向/负向 emoji |
| 用户信任度 | praiseworthiness | 信任用户×1.1，吵架用户×0.5 |
| 群紧张水平 | arousal | 紧张群 ×1.2，冷淡群 ×0.8 |
| 同类信号习惯化 | expectedness | 2 分钟内同类信号重复 |
| 被 @ 触发 | arousal +0.1, expectedness ×0.5 | `mentioned=True` |

所有启发式为纯函数，零 LLM，零网络调用。详见 `emotion_engine/appraisal_heuristics.py`。

### 配置示例

```json
{
  "appraisal_mode": "occ_heuristic"
}
```

运行时切换（其他插件调用）：
```python
plugin = self.context.get_registered_star("astrbot_plugin_emotion_state_machine")
plugin.set_appraisal_mode("occ_static")
```

## 下一步可扩展方向

- 接入 aiocqhttp 戳一戳 notice，将真实戳一戳映射到 `poke` 信号。
- 支持 LLM judge 对复杂消息做结构化 signal 判定。
- 增加跨群 user-global 长期关系层。

## 模块结构

`emotion_engine.py` 在 v0.4.0 起拆为 `emotion_engine/` 包，9 个子模块各司其职：

```
emotion_engine/
├── __init__.py          # 公共 API 完整重导出（向后兼容）
├── utils.py             # clamp / normalize_* / prune_active_users
├── defaults.py          # 所有出厂默认常量（baselines / weights / thresholds / keywords）
├── state.py             # Snapshot dataclass + EmotionEvent
├── signals.py           # signal_names() + 权重表重导出
├── signals_classify.py  # 文本 → signal 推断（关键词 + 疑问句判定）
├── appraisal.py         # 直接评价模式（apply_weights）
├── labels.py            # 离散标签派生（derive_*_label）
├── machine.py           # EmotionStateMachine 编排器
└── prompt.py            # 提示块 + 哨兵 + 人类可读渲染
```

新代码建议从子模块精确导入（如 `from emotion_engine.labels import derive_group_label`），老代码的 `from emotion_engine import X` 风格继续可用。

## Public API for other plugins

其他 AstrBot 插件可以通过 `context.get_registered_star("astrbot_plugin_emotion_state_machine")` 获取本插件实例，并调用下面的方法读写情绪状态。**所有方法都会对 `scope` / `user_id` 做内部归一化**，外部不需要预先 trim。

### 关键约定

- **Scope 必须复用**。其他插件要读写情绪时，请用 `get_scope(event)` 拿 scope key，不要自己根据 `group_id` 拼字符串——否则会落到和内置 observer 不同的 scope。
- **写入会触发持久化**。读取不会。
- **未知 signal 抛 `ValueError`**，调用前可用 `list_signals()` 校验。

### 读取状态

| 方法 | 用途 |
| --- | --- |
| `get_scope(event)` | 从 AstrBot event 计算 scope key。 |
| `get_combined_state(scope, user_id="", *, apply_decay=True)` | 合成视图（group + relation + label）。 |
| `get_group_state(scope, *, apply_decay=True)` | 仅群公共情绪快照。 |
| `get_relation_state(scope, user_id, *, apply_decay=True)` | 仅用户私有关系快照。 |
| `list_signals()` | 返回支持的 signal 名称列表。 |
| `render_state_text(scope, user_id="")` | 与 `/emotion_state` 一致的人类可读文本。 |
| `build_prompt_block(scope, user_id="")` | 与内置 LLM 注入一致的 prompt block。 |

### 写入状态

| 方法 | 用途 |
| --- | --- |
| `observe_text(scope, text, *, user_id="", mentioned=False)` | 从原始文本推断信号并应用。 |
| `apply_signal(scope, user_id, signal, *, intensity=1.0, reason="external")` | 手动施加一个已知 signal。未知 signal 抛 `ValueError`。 |
| `reset_scope(scope)` | 重置整个 scope（group + 所有 relations，行为同 `/emotion_reset`）。 |
| `force_decay(scope, *, now=None)` | 立即对群公共情绪执行一次衰减，可选传 `now` 推进时钟。 |

### 使用示例

```python
# 在另一个插件里
machine = self.context.get_registered_star("astrbot_plugin_emotion_state_machine")
if machine is None:
    return  # 情绪状态机插件未加载

# 写入
machine.apply_signal(
    scope=machine.get_scope(event),
    user_id=str(event.get_sender_id()),
    signal="praise",
    intensity=1.0,
    reason="auto-detected thanks",
)

# 读取
view = machine.get_combined_state(scope="group-123", user_id="user-a")
print(view.label, view.group.valence, view.relation.trust)
```
