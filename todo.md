# ESM v0.10.0 TODO

> 来源：`Desktop/esm_api.txt` 草案 + 对话讨论
> 状态：设计阶段，未开工

## 0. 设计争议状态

- [x] **API 命名**：锁 `to_text_part` —— 表达返回类型，避开和 `build_prompt_block` 同词根
- [x] **self-reply 触发方式**：锁 公共方法 `apply_self_reply_signal(event)` + social_context 显式调用
  - 不用钩子 → 争议 2 自动归零
  - 不用读 extra → 争议 3 自动归零
  - 不用关心 priority → 争议 4 自动归零
- [x] **TextPart 边界**：锁 `to_text_part` 放 main.py，`emotion_engine` 保持纯逻辑
- [🔄] **self-reply 累积算法**：替换为 TalkWillingness 模型（详见 §6）

## 1. 阶段一：ESM 新增 API（独立 PR → v0.10.0）

- [ ] 新增 `to_text_part(self, scope, user_id="") -> TextPart`
  - 包装 `build_prompt_block` 结果 + `.mark_as_temp()`
  - 测试：空 scope / 有 scope / user_id 空 / 非空 / template 自定义
- [ ] 新增 `TalkWillingnessState` 内部状态机（详见 §6）
  - 类实现 ~150 行 + 测试覆盖
  - 替代原"节流 + 软上限"草案设计
  - 三因素调制：时间 / 轮次 / 自身情绪
  - 阈值反噬：越过 HIGH 主动衰减
  - 不应期：刚触发后短时间内抑制
  - 连续上限：MAX_CONSECUTIVE 保护
- [ ] 新增 `apply_self_reply_signal(event)` 公共方法
  - 读取 TalkWillingness 状态决定是否打
  - 调用 ESM 自身的 `try_apply_signal`
  - 默认 signal 类型：`self_reply`（仅影响 arousal/curiosity，不动 affection/trust）
- [ ] 新增 `self_reply` signal（`emotion_engine/signals.py`）
  - 群维度：`arousal +0.05, curiosity +0.02`
  - 个人维度：（空）
- [ ] `_conf_schema.json` 新增 8 个配置项 + hint
- [ ] （可选）精力维度调制：依赖精力系统存在
- [ ] （可选）群规模调制：`crowd_factor = 1/sqrt(active_users)`
- [ ] （v0.11+）历史准确率反馈：接入 self-reply 用户响应统计
- [ ] `main.py` 顶部 docstring 列公开 API 边界
- [ ] 新建 `_PUBLIC_API.md` 单独文件
  - 稳定 / 新增稳定 / 实验性 / 已废弃 四档
- [ ] 测试覆盖
  - 单测：TalkWillingness 三因素公式
  - 单测：阈值反噬曲线（HIGH 越过行为）
  - 单测：不应期窗口抑制
  - 单测：连续上限触发回落
  - 单测：用户发言打断 consecutive_apply 计数
  - 单测：scope 删除后 TalkWillingness 同步清理
  - 集成测：social_context 缺失 / 存在 / 禁用 三态
  - 集成测：精力度调制（mock 精力系统）
  - 集成测：群规模调制

## 2. 阶段二：social_context 迁移（独立 PR → v0.8.12）

- [ ] 升级对 ESM `to_text_part` 的依赖
- [ ] 删除接入点 3（self-reply signal）
  - 删 `_apply_emotion_self_reply_signal`（`mixins/emotion_bridge.py:171-240`，约 70 行）
  - 删 4 个配置项 + `_conf_schema.json` hint
  - 删 `self._emotion_signal_last` / `self._emotion_disabled_warn_last`
  - 删 `tests/test_emotion_bridge.py` 约 30 行
  - 简化 `_get_emotion_plugin` 检查列表
- [ ] judge=yes 后显式调用 `esm.apply_self_reply_signal(event)`（4 行）
- [ ] 接入点 2 改造：字符串拼接 → `to_text_part` 独立 TextPart
- [ ] README 加一条"如果同时用 emotion 注入，强烈建议安装 ESM v0.10.0+"

## 3. 阶段三：彻底清理（可选，推迟）

- [ ] social_context 完全移除 `emotion_bridge.py`
- [ ] "观察用户消息 → ESM" 逻辑并入主流程
- [ ] 重新设计 `_feed_emotion_observation` 的接入位置
- ⚠ 大改，**先观察阶段 1+2 稳定后再决定**

## 4. 文档与兼容性

- [ ] 更新 `CHANGELOG.md`：v0.10.0 段落
- [ ] 更新 `README.md`：公开 API 列表指 `_PUBLIC_API.md`
- [ ] 兼容性矩阵在文档里已写，**漏一行**：
  - 新 social_context v0.8.12 + 没装 ESM → emotion 注入全部 no-op
  - 在 social_context README 加"ESM v0.10.0+ 强依赖"声明
- [ ] engram 脑科学参考素材收集（用户当日目标）
  - 来源：脑科学 / 神经科学公开资料
  - 用途：v0.10.0 之后阶段（v0.11+）的情绪模型参考
  - 当前阶段不动手，仅素材整理

## 5. 已知的非本版本工作（标记归档）

- `page_api.py` 是死代码，未被实例化，下次顺手清
- WebUI 时序端点 `/state/<scope>/history`（v0.11+）
- aiocqhttp 戳一戳 → `poke` signal（v0.11+）
- LLM judge signal 判定（v0.11+）
- 跨群 user-global 长期关系层（v0.12+）

## 6. TalkWillingness 详细设计

> 替代原"频次节流"模型：从定时器 → 内部累积状态
> 参考：脑科学 habituation / sensitization / refractory period

### 6.1 模型定义

`TalkWillingness` 是每个 scope 一份的内部状态：

| 字段 | 类型 | 含义 |
|---|---|---|
| `W` | float | 当前蓄积值（0..1.20） |
| `last_tick_ts` | float | 上次 tick 时间戳 |
| `consecutive_apply` | int | 连续触发计数 |
| `last_apply_ts` | float | 上次实际触发时间戳 |

### 6.2 三因素公式

```
W(t+1) = W(t) × decay + net_charge(time, turns, emotion) - drain
```

#### 时间因子（寂寞蓄力）

| 沉默时长 | time_charge |
|---|---|
| < 30s | 0 |
| 30s..10min | `min(0.15, (elapsed-30) / 600 × 0.15)` |
| ≥ 10min | 0.15（封顶） |

#### 轮次因子（满足感）

| 5min 内轮次 | turn_charge |
|---|---|
| ≥ 3 | -0.10 |
| == 0 且 elapsed < 60s | -0.05 |
| 其它 | 0 |

#### 自身情绪因子

| 状态 | emotion_charge |
|---|---|
| valence < 0.35 | -0.08 |
| 其它 | `(arousal - 0.5) × 0.10 + (curiosity - 0.5) × 0.10` |

**关键**：读的是 `group snapshot`，**完全不读 affection/trust**——切断循环引用。

### 6.3 阈值反噬

```
THRESHOLD_LOW  = 0.55   # 触发区入口
THRESHOLD_HIGH = 0.85   # 反噬区入口
HARD_CAP       = 1.20
```

| W 区间 | 行为 |
|---|---|
| `[0, LOW]` | 蓄力，不打 signal |
| `(LOW, HIGH]` | 触发：应用 signal + 重置 W 到 45% |
| `(HIGH, HARD_CAP]` | 反噬：W × 0.65 - 0.05，主动衰减，不打 |

**三段曲线**：

```
W
↑
1.0 ┤        ╭─╮          反噬区
    │       ╱   ╲         (越过 HIGH 后主动衰减)
0.85┤──────╱─────╲─────── THRESHOLD_HIGH
    │     ╱       ╲
0.55 ┤────╱─────────╲──── THRESHOLD_LOW (触发线)
    │   ╱           ╲
0.0 ┤──╱─────────────╲── baseline
    └──────────────────────→ t
       蓄力    触发    衰减
```

### 6.4 配套机制

| 机制 | 触发条件 | 效果 |
|---|---|---|
| **不应期** | `now - last_apply_ts < 30s` | W × 0.30 |
| **连续上限** | `consecutive_apply >= 5` | 强制回落 W × 0.5，不触发 |
| **用户打断** | 用户消息到达 | `consecutive_apply` 重置为 0 |
| **scope 删除** | scope 清理时 | `TalkWillingnessState.pop(scope)` |

### 6.5 信号语义（关键）

self-reply 默认 signal 类型：`self_reply`（**新增**）

```python
# emotion_engine/signals.py
SIGNAL_WEIGHTS["self_reply"] = {
    "group":    {"arousal": +0.05, "curiosity": +0.02},
    "relation": {},  # 完全不动
}
```

**理由**：彻底切断 social_context 调制循环（affection/trust 不被 self-reply 修改 → social_context 无法基于此放大主动性）。

### 6.6 配置项（8 个）

```json
{
  "self_reply_signal_enabled": true,
  "self_reply_signal": "self_reply",
  "self_reply_min_interval_seconds": 30,
  "self_reply_threshold_low": 0.55,
  "self_reply_threshold_high": 0.85,
  "self_reply_decay": 0.92,
  "self_reply_max_consecutive": 5,
  "self_reply_refractory_seconds": 30
}
```

**内部常量**（不暴露给用户）：
- 时间因子：30s 阈值、600s 窗口、0.15 封顶
- 情绪因子：valence 0.35 红线
- 强度映射：`intensity = 0.05 + ratio × 0.20`，ratio ∈ [0, 1]

### 6.7 扩展项（值得加）

#### A. 精力维度调制（v0.10 可选）

```python
energy = bot_energy or 0.5  # 假设精力系统存在
W_new *= (0.5 + energy * 0.5)  # 精力 1.0 → ×1.0；精力 0 → ×0.5
```

**依赖**：精力系统存在（v0.10 阶段未必到位，可加 try/except 兜底）

#### B. 群规模调制（v0.10 可选，无依赖）

```python
crowd_factor = 1.0 / math.sqrt(max(1, active_users))
net_charge *= crowd_factor
```

效果：
| 群规模 | crowd_factor |
|---|---|
| 1 人 | 1.00 |
| 5 人 | 0.45 |
| 25 人 | 0.20 |
| 100 人 | 0.10 |

直觉：小群里 bot 主动很正常，大群里频繁主动显得突兀。

#### C. 历史准确率反馈（v0.11+）

```python
positive_ratio = recent_self_reply_feedback.get(scope, 0.5)
W_new *= (0.5 + positive_ratio * 1.0)
```

**依赖**：self-reply 用户反馈统计机制（v0.11+ 才考虑）

让 TalkWillingness 从纯被动系统变成**有学习的反馈系统**。

### 6.8 改动量评估

| 文件 | 改动 |
|---|---|
| `emotion_engine/signals.py` | +5 行（新增 `self_reply` signal） |
| `main.py` | +150 行（`TalkWillingnessState` 类 + `apply_self_reply_signal` 方法） |
| `_conf_schema.json` | +8 项配置 + hint |
| 测试 | +100 行（覆盖三因素、反噬、不应期、连续上限、用户打断、scope 删除） |
| `social_context` 侧 | +4 行（judge=yes 后调用）+ 删除 ~70 行旧 self-reply 代码 |

---

**当前阻塞**：§0 争议 1-5 已锁，争议 6（TalkWillingness）设计已写完待用户确认后开工。