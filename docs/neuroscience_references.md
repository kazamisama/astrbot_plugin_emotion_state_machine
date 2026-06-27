# ESM 脑科学参考素材

> v0.10.0+：把已经用上的脑科学概念补齐学术出处，为 v0.11+ 设计储备候选方向。

## 目录

1. [为什么写这份文档](#一为什么写这份文档)
2. [v0.10.0 已经用上的概念（现状映射）](#二v0100-已经用上的概念现状映射)
   - 2.1 [不应期（refractory period）→ `self_reply_refractory_seconds`](#21-不应期refractory-period--self_reply_refractory_seconds)
   - 2.2 [饱和反噬（saturation reversal）→ HIGH 阈值反转](#22-饱和反噬saturation-reversal--high-阈值反转)
   - 2.3 [习惯化（habituation）→ `consecutive_apply` 计数器](#23-习惯化habituation--consecutive_apply-计数器)
   - 2.4 [神经积分器 / 累积器（neural integrator）→ `W` 状态变量](#24-神经积分器--累积器neural-integrator--w-状态变量)
3. [v0.11+ 候选方向](#三v011-候选方向)
   - 3.1 [长期记忆巩固：海马体-皮层对话](#31-长期记忆巩固海马体-皮层对话)
   - 3.2 [情绪衰减：单胺类神经递质半衰期](#32-情绪衰减单胺类神经递质半衰期)
   - 3.3 [关系亲密度：依恋理论 + 催产素通路](#33-关系亲密度依恋理论--催产素通路)
   - 3.4 [跨群泛化：图式（schema）理论](#34-跨群泛化图式schema理论)
   - 3.5 [注意力衰减：抑制性突触可塑性（ISP）](#35-注意力衰减抑制性突触可塑性isp)
4. [参考文献清单](#四参考文献清单)
5. [未来工作](#五未来工作)

---

## 一、为什么写这份文档

`TalkWillingnessState`（v0.10.0 引入）从一开始就不是从零设计的——它的核心机制（不应期、习惯化、饱和反噬、累积器）都是神经科学里早已研究过的现象。本文档做两件事：

1. **追溯**：把 v0.10.0 已经用上的概念补齐出处，方便后续 review 时回答"这个参数为什么是这个值"。
2. **储备**：列出 v0.11+ 可能用到的概念，提前做调研，避免到时候拍脑袋。

需要强调的是：**ESM 不是脑科学仿真器**。它借鉴脑科学概念来设计参数和决策结构，但实现是纯函数的，不涉及真实的神经动力学。下文每个映射都标注了"借鉴强度"：直接借用、隐喻、还是仅作类比。

---

## 二、v0.10.0 已经用上的概念（现状映射）

### 2.1 不应期（refractory period）→ `self_reply_refractory_seconds`

| 项 | 内容 |
|---|---|
| **神经科学概念** | 神经元在动作电位（action potential）后存在**绝对不应期**（~1-2ms，钠通道完全失活，无法再激发）和**相对不应期**（数 ms-数十 ms，需更强刺激才能激发）。 |
| **ESM 实现** | `TalkWillingnessState.tick()` 在 refractory 窗口内（默认 30s）对 W 应用 `× 0.30` 抑制。 |
| **代码位置** | `main.py:TalkWillingnessState.tick`，对应常量 `REFRACTORY_SECONDS = 30.0` |
| **借鉴强度** | **直接借用**。30s 远超神经不应期（ms 级），但功能等价——抑制刚激发过的系统短时间内再次激发。 |
| **参考资料** | Hodgkin & Huxley (1952) "A quantitative description of membrane current and its application to conduction and excitation in nerve"。J. Physiol. 117: 500-544. |

**设计动机**：bot 刚主动回复完，紧接着又主动说话，会让群友觉得"这 bot 上瘾了"。不应期强制最小间隔，模拟神经系统的"冷却时间"。

---

### 2.2 饱和反噬（saturation reversal）→ HIGH 阈值反转

| 项 | 内容 |
|---|---|
| **神经科学概念** | **感觉适应**（sensory adaptation）：持续刺激下感受器反应递减。**Weber-Fechner 定律**：主观强度 ∝ log(物理刺激强度)。如果继续增强刺激超过某阈值，主观体验反而下降（饱和 / 反感）。 |
| **ESM 实现** | `TalkWillingnessState._threshold_decision`：W > HIGH（默认 0.85）时进入反噬区，W 应用 `× 0.65 - 0.05` 主动衰减，**不应用** signal。 |
| **代码位置** | `main.py:TalkWillingnessState._threshold_decision`，常量 `THRESHOLD_HIGH = 0.85` |
| **借鉴强度** | **隐喻**。神经适应是渐进递减，反噬是突变反转——但设计意图相同：阻止系统被推到饱和状态。 |
| **参考资料** | Weber (1834) "De Pulsu, Resorptione, Auditu et Tactu"。Fechner (1860) "Elemente der Psychophysik"。 |

**设计动机**：v0.10.0 之前的设计（草案第 4 节"频次节流"）会让 affection/trust 单调爬升——bot 越主动越喜欢用户，造成反馈环。HIGH 阈值反转强制 bot 在蓄积过强时沉默，等 W 自然回落。

---

### 2.3 习惯化（habituation）→ `consecutive_apply` 计数器

| 项 | 内容 |
|---|---|
| **神经科学概念** | **习惯化**（habituation）：对重复刺激的反应递减，是学习的最基本形式。Rankin 等（2009）"Habituation" 综述把习惯化定义为"对重复刺激反应的渐进、相对持久的下降"。 |
| **ESM 实现** | `TalkWillingnessState` 维护 `state.consecutive_apply`：每次成功 apply signal 加 1；用户消息到达时重置为 0；达到 `MAX_CONSECUTIVE`（默认 5）后强制回落 W 并停止触发。 |
| **代码位置** | `main.py:TalkWillingnessState._threshold_decision` |
| **借鉴强度** | **直接借用**。Rankin 等指出习惯化是"刺激特异性"+"时间依赖"的——ESM 的实现正好对应（scope 维度 + 用户消息重置）。 |
| **参考资料** | Thompson & Spencer (1966) "Habituation: A model phenomenon for the study of neuronal substrates of behavior"。Psychol. Rev. 73(1): 16-43。Rankin et al. (2009) "Habituation revisited: An updated and revised description of the behavioral characteristics of habituation"。Neurobiol. Learn. Mem. 92(2): 135-138。 |

**设计动机**：避免 bot 反复自我反馈（每次主动 → self_reply signal → 蓄积更容易 → 更主动）。习惯化 + 用户打断机制让"主动回复"必须由真实的新输入驱动。

---

### 2.4 神经积分器 / 累积器（neural integrator）→ `W` 状态变量

| 项 | 内容 |
|---|---|
| **神经科学概念** | **神经积分器**（neural integrator）：神经回路将输入信号累积成输出，最经典的例子是**眼动神经积分器**（oculomotor neural integrator），它把速度信号积分成位置信号以维持注视。**决策阈值模型**（decision-threshold / drift-diffusion model）也用类似的累积变量表示证据积累。 |
| **ESM 实现** | `TalkWillingnessState._TalkWillingness.W` 是一个 float（0..1.20），每 tick 接收三因素 charge，按 decay 衰减，再被阈值机制（蓄积 / 触发 / 反噬）分段处理。 |
| **代码位置** | `main.py:TalkWillingnessState.tick` + `_TalkWillingness` dataclass |
| **借鉴强度** | **隐喻**。神经积分器是连续时间动力系统，ESM 是离散 tick 状态机；但抽象结构相同：累积 → 阈值 → 输出。 |
| **参考资料** | Robinson (1975) "Oculomotor unit behavior in the monkey"。J. Neurophysiol. 38(2): 393-404。Goldman et al. (2014) "Bridging neural and computational viewpoints on perceptual decision-making"。Curr. Opin. Neurobiol. 25: 1-9。Ratcliff & McKoon (2008) "The diffusion decision model: theory and data"。Neural Comput. 20(4): 873-922。 |

**设计动机**：W 的存在让"是否说话"这个决策不是瞬时的（看当前消息）而是累积的（看历史趋势）。这跟人类的"我刚被冷落了一会所以更想说话"的认知模式一致——累积变量捕捉了这种时间依赖的偏好。

---

## 三、v0.11+ 候选方向

以下五个方向是 v0.11+ 可能借鉴的概念，**目前仅作储备**，没有承诺实施。

### 3.1 长期记忆巩固：海马体-皮层对话

| 项 | 内容 |
|---|---|
| **神经科学概念** | 记忆从海马体（hippocampus）逐步转移到皮层（cortex），这个过程叫**系统巩固**（systems consolidation）。睡眠期间的**尖波涟漪**（sharp-wave ripples, SWR）触发海马体回放（replay），强化皮层中的相应表征。**主动遗忘**（active forgetting）由抑制性中间神经元介导。 |
| **可能的 ESM 应用** | v0.9.22+ 已经在做情绪状态持久化（`emotion_state.json`）。v0.11+ 可以借鉴 SWR：定期"回放"历史情绪，识别反复出现的模式，把它们"提升"到更稳定的长期维度（如 trust_baseline）。 |
| **借鉴强度** | **隐喻**。AI 持久化是显式文件读写，SWR 是隐式的神经重放——但"哪些维度需要从短期变长期"的决策机制可以借鉴。 |
| **参考资料** | Buzsáki (1989) "Two-stage model of memory trace formation: A role for 'noisy' brain states"。Neuroscience 31(3): 551-570。Wilson & McNaughton (1994) "Reactivation of hippocampal ensemble memories during sleep"。Science 265(5172): 676-679。Frankland & Bontempi (2005) "The organization of recent and remote memories"。Nat. Rev. Neurosci. 6(2): 119-130。 |

**设计草图**：
```
每 N 小时的 idle 期触发一次 "consolidation sweep"：
  - 扫描所有 scope 的 emotion_state.json
  - 对每个 scope，提取 trust_baseline（5th percentile of all historical trust）
  - 把 trust_baseline 持久化为单独的 "long_term_baselines.json"
  - 后续 get_relation_state 时，trust 实际值向 baseline 缓慢收敛
```

---

### 3.2 情绪衰减：单胺类神经递质半衰期

| 项 | 内容 |
|---|---|
| **神经科学概念** | 多巴胺、血清素、去甲肾上腺素等单胺类神经递质的**突触半衰期**差异显著：多巴胺 ~2h（短期奖励），血清素 ~hours（情绪调节），去甲肾上腺素 ~minutes（警觉）。情绪不应"一刀切"衰减——每种维度应有各自的生理半衰期。 |
| **可能的 ESM 应用** | 当前所有情绪维度（valence / arousal / stress / curiosity / trust / affection / irritation / familiarity）共用一个 `decay_half_life_seconds`。v0.11+ 可以按维度拆开，参考神经递质半衰期赋初值。 |
| **借鉴强度** | **直接借用**（参数值）。结构上 ESM 已经有 `decay_half_life_seconds`，只是没按维度拆。 |
| **参考资料** | Grace (2016) "Dysregulation of the dopamine system in the pathophysiology of schizophrenia and depression"。Nat. Rev. Neurosci. 17(8): 524-532。Yates (2019) "Serotonin and the regulation of mammalian reproduction"。Annu. Rev. Physiol. 81: 1-23。 |

**当前问题**：所有 8 个维度用同一个 900s 半衰期。但 valence（情绪基调）应该比 stress（应激）持久得多——一个好的早晨不应该是"15 分钟就消退"的。

**设计草图**：
```python
DECAY_HALF_LIVES = {
    "valence": 3600.0,     # 1h — 情绪基调
    "arousal": 600.0,      # 10min — 唤醒度衰减快
    "stress": 300.0,       # 5min — 应激应快速恢复
    "curiosity": 1800.0,   # 30min — 兴趣中等持久
    "trust": 86400.0,      # 24h — 信任是慢变量
    "affection": 43200.0,  # 12h
    "irritation": 600.0,   # 10min — 易怒应快速消退
    "familiarity": 604800.0,  # 7d — 熟悉度极慢
}
```

---

### 3.3 关系亲密度：依恋理论 + 催产素通路

| 项 | 内容 |
|---|---|
| **神经科学概念** | **依恋理论**（attachment theory, Bowlby 1969）区分安全型 / 焦虑型 / 回避型依恋。**催产素**（oxytocin）在亲密互动中释放，强化社会记忆。多巴胺 + 催产素的协同调节"奖励-联结"回路，决定"我想再见到这个人"的强度。 |
| **可能的 ESM 应用** | 当前 ESM 把 affection 当作单一连续维度。v0.11+ 可以把依恋类型建模为元数据（per-user），影响新信号的处理方式：焦虑型用户更敏感（小信号放大），回避型用户反应更慢（信号吸收率降低）。 |
| **借鉴强度** | **隐喻**。依恋理论原本解释亲子关系，迁移到 bot-user 关系是类比，不是因果。但概念结构有用。 |
| **参考资料** | Bowlby (1969) "Attachment and Loss, Vol. 1: Attachment"。Basic Books。Feldman (2012) "Oxytocin and social affiliation in humans"。Horm. Behav. 61(3): 380-391。 |

**风险警告**：在 LLM bot 上模拟"依恋类型"容易让用户误以为 bot 真有情感依附。建议 v0.11+ 只用作"信号处理参数化"（如 weight × 1.2 for anxious），不暴露 user-visible 的"依恋类型"标签。

---

### 3.4 跨群泛化：图式（schema）理论

| 项 | 内容 |
|---|---|
| **神经科学概念** | **图式**（schema）是组织记忆和感知的高层结构。Piaget（1926）最早提出，Bartlett（1932）实验证明人用图式重构记忆。van Kesteren 等（2012）综述：图式激活促进新信息与已有知识的整合。 |
| **可能的 ESM 应用** | 当前 ESM 的 scope 是"群 + 人格"维度的，不跨群。v0.11+ 如果要做"跨群长期关系层"（todo.md §5 已归档），可以借鉴图式：把多个群里的同一个用户聚合到一个"person schema"，聚合规则基于信号相似度（不是简单平均）。 |
| **借鉴强度** | **类比**。图式是认知结构，ESM 是状态向量——抽象结构类似，但实现细节需要重新设计。 |
| **参考资料** | Bartlett (1932) "Remembering: A Study in Experimental and Social Psychology"。Cambridge University Press。van Kesteren, Ruiter, Fernández (2012) "How schema and novelty augment memory formation"。Trends Neurosci. 35(4): 211-219。Gilboa & Marlatte (2017) "Neurobiology of Schemas and Schema-Mediated Memory"。Trends Cogn. Sci. 21(8): 618-631。 |

**设计草图**（草案，未实现）：
```python
class PersonSchema:
    user_id: str
    person_global_id: str  # 跨群的同一用户标识
    trust_baseline: float
    affection_baseline: float
    last_active_in_any_scope: float
    schema_strength: float  # 0..1，随信号一致性累积
```

---

### 3.5 注意力衰减：抑制性突触可塑性（ISP）

| 项 | 内容 |
|---|---|
| **神经科学概念** | **抑制性突触可塑性**（inhibitory synaptic plasticity, ISP）：抑制性突触的强度也随活动调整。**注意力衰减**：对持续无新意的刺激，注意力逐渐降低（habituation 的认知层面）。 |
| **可能的 ESM 应用** | 当前 `disabled_signals` 是全局禁用。v0.11+ 可以加"信号疲劳度"——同一类信号短时间内重复，weight 衰减。避免 bot 在某个信号模式上下注过深。 |
| **借鉴强度** | **类比**。 |
| **参考资料** | Vogels et al. (2013) "Inhibitory synaptic plasticity: spike timing-dependence and putative network function"。Front. Neural Circuits 7: 119。 |

**风险警告**：过度应用 habituation 到所有信号会让 bot "反应迟钝"，需要 per-signal-class 的衰减曲线，而不是全局应用。

---

## 四、参考文献清单

### 教科书 / 综述
- Kandel, Schwartz, Jessell (2012) "Principles of Neural Science", 5th ed. McGraw-Hill.
- Dayan & Abbott (2001) "Theoretical Neuroscience". MIT Press.

### v0.10.0 直接借鉴
- Hodgkin & Huxley (1952) — 神经元不应期
- Thompson & Spencer (1966) — 习惯化范式
- Rankin et al. (2009) — 习惯化综述
- Robinson (1975) — 神经积分器（眼动）
- Weber (1834) / Fechner (1860) — 感觉适应 / Weber-Fechner

### v0.11+ 候选
- Buzsáki (1989) / Wilson & McNaughton (1994) — 系统巩固 / SWR
- Frankland & Bontempi (2005) — 长期记忆巩固综述
- Grace (2016) / Yates (2019) — 单胺类神经递质
- Bowlby (1969) / Feldman (2012) — 依恋理论 / 催产素
- Bartlett (1932) / van Kesteren et al. (2012) / Gilboa & Marlatte (2017) — 图式理论
- Vogels et al. (2013) — 抑制性突触可塑性

### 决策 / 累积模型（背景）
- Ratcliff & McKoon (2008) — 漂移扩散模型
- Goldman et al. (2014) — 感知决策的神经与计算视角

---

## 五、未来工作

- [ ] **v0.11+ 优先级排序**：先确定 v0.11+ 实际做哪几个候选方向。本文档列出的 5 个都是 "interesting"，但工程上不可能全做。需要按"对当前用户痛点的覆盖度 × 实现复杂度"做排序。
- [ ] **per-维度 decay_half_lives 实施**（3.2）：纯配置变更，~30 行代码，风险低，建议 v0.11.0 第一个做。
- [ ] **person schema 概念验证**（3.4）：需要先有跨群用户标识体系，可能要等社交账号系统稳定。
- [ ] **SWR-style consolidation sweep**（3.1）：性能开销 + 状态迁移复杂，需要设计实验验证是否真的能改善"长期关系"的用户感知。

---

**维护说明**：
- v0.10.0 设计时凭直觉用了这些概念，没做学术调研。本文档是回溯性质。
- 不要把这当成"ESM 用了脑科学"的证据——这只是"借鉴了概念"，实现细节是工程判断。
- 如果读者发现某个映射不准或有更好的参考资料，欢迎提 PR 更新。