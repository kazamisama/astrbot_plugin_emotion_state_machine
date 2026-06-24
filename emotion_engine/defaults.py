"""Factory-default constants for the emotion state machine.

All numeric baselines, weight tables, keyword sets, and label
thresholds live here. Per-instance overrides are passed to
``EmotionStateMachine(...)`` and per-snapshot overrides can be applied
at runtime; the tables in this module are the canonical "shipped
defaults" and are exported at the package level for backward compat
with ``from emotion_engine import GROUP_BASELINE``-style imports.

Tuning the bot's apparent personality means editing the tables here,
not the code that consumes them. ``derive_*_label`` reads from
``GROUP_LABEL_THRESHOLDS`` / ``RELATION_LABEL_THRESHOLDS``; the
state-machine dispatcher reads from ``GROUP_SIGNAL_WEIGHTS`` /
``RELATION_SIGNAL_WEIGHTS`` / ``SIGNAL_LAYER_WEIGHTS``.
"""

from __future__ import annotations


GROUP_BASELINE = {
    "valence": 0.56,
    "arousal": 0.32,
    "stress": 0.18,
    "curiosity": 0.38,
}

RELATION_BASELINE = {
    "trust": 0.55,
    "affection": 0.46,
    "irritation": 0.16,
    "familiarity": 0.10,
}

GROUP_SIGNAL_WEIGHTS: dict[str, dict[str, float]] = {
    "praise": {"valence": 0.10, "stress": -0.04},
    "thanks": {"valence": 0.07, "stress": -0.03},
    "friendly": {"valence": 0.05},
    "mention": {"arousal": 0.05, "curiosity": 0.04},
    "poke": {"arousal": 0.11, "curiosity": 0.03, "valence": 0.02},
    "technical": {"curiosity": 0.12, "arousal": 0.04, "stress": 0.02},
    "question": {"curiosity": 0.08, "arousal": 0.02},
    "comfort": {"valence": 0.04, "stress": -0.08},
    "insult": {"valence": -0.13, "stress": 0.11, "arousal": 0.06},
    "pressure": {"stress": 0.10, "arousal": 0.05, "valence": -0.05},
    "silence": {"arousal": -0.04, "curiosity": -0.03},
    "success": {"valence": 0.09, "arousal": 0.04, "stress": -0.05},
    "failure": {"valence": -0.08, "stress": 0.08, "curiosity": 0.04},
}

RELATION_SIGNAL_WEIGHTS: dict[str, dict[str, float]] = {
    "praise": {"trust": 0.04, "affection": 0.06, "irritation": -0.03, "familiarity": 0.02},
    "thanks": {"trust": 0.05, "affection": 0.04, "irritation": -0.02, "familiarity": 0.02},
    "friendly": {"trust": 0.03, "affection": 0.05, "irritation": -0.02, "familiarity": 0.03},
    "mention": {"affection": 0.02, "familiarity": 0.01},
    "poke": {"affection": 0.04, "irritation": 0.02, "familiarity": 0.02},
    "technical": {"trust": 0.02, "familiarity": 0.02},
    "question": {"familiarity": 0.01},
    "comfort": {"trust": 0.06, "affection": 0.07, "irritation": -0.06, "familiarity": 0.02},
    "insult": {"trust": -0.07, "affection": -0.04, "irritation": 0.12, "familiarity": 0.01},
    "pressure": {"trust": -0.04, "irritation": 0.08, "familiarity": 0.01},
    "silence": {},
    "success": {"trust": 0.03, "affection": 0.02, "irritation": -0.02},
    "failure": {"trust": -0.02, "irritation": 0.03},
}

SIGNAL_LAYER_WEIGHTS: dict[str, tuple[float, float]] = {
    "praise": (0.35, 0.80),
    "thanks": (0.30, 0.75),
    "friendly": (0.25, 0.70),
    "mention": (0.40, 0.45),
    "poke": (0.30, 0.75),
    "technical": (0.70, 0.30),
    "question": (0.45, 0.25),
    "comfort": (0.35, 0.85),
    "insult": (0.45, 0.90),
    "pressure": (0.55, 0.65),
    "silence": (0.50, 0.00),
    "success": (0.55, 0.45),
    "failure": (0.55, 0.45),
}

KEYWORD_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("praise", ("好厉害", "厉害", "靠谱", "天才", "做得好", "不错", "优秀")),
    ("thanks", ("谢谢", "谢了", "感谢", "辛苦", "帮大忙")),
    ("friendly", ("早", "晚安", "摸摸", "抱", "可爱", "雪莉")),
    ("insult", ("笨蛋", "傻", "人机", "废物", "坏", "欠揍")),
    ("pressure", ("快点", "赶紧", "急", "立刻", "马上", "怎么还没")),
    ("technical", ("代码", "插件", "bug", "报错", "日志", "配置", "函数", "接口", "状态机")),
    ("comfort", ("别急", "没事", "休息", "慢慢来", "不怪你")),
    ("success", ("成功", "通过", "搞定", "修好了", "可以了")),
    ("failure", ("失败", "炸了", "不行", "错了", "崩了")),
)


# Interrogative words / modal question phrases. Presence of any of these
# in a message is treated as a strong question signal — even without a
# trailing "?". This covers "行不行" / "能不能" / "怎么修" / "什么是 X"
# style questions that don't end with a question mark.
QUESTION_INDICATORS: tuple[str, ...] = (
    # Standard question words
    "怎么", "什么", "为什么", "为啥", "哪", "谁", "几", "多少", "如何", "干嘛",
    # Modal / yes-no question phrases
    "是不是", "能不能", "会不会", "可不可以", "要不要", "好不好", "行不行",
    "对不对", "有没有",
)


# Group emotion label thresholds.
#
# Tuning these changes the bot's apparent personality: lower thresholds
# make labels fire more readily (more reactive), higher thresholds make
# the bot appear more stoic. The order in this dict is meaningful —
# ``derive_group_label`` evaluates conditions in insertion order and
# returns the first match. ``"calm"`` is the implicit default when no
# condition matches and is intentionally absent from the table.
#
# Convention: ``<dim>_min`` means the snapshot value must be **>=** the
# threshold; ``<dim>_max`` means it must be **<=** the threshold.
GROUP_LABEL_THRESHOLDS: dict[str, dict[str, float]] = {
    "annoyed":  {"stress_min": 0.68, "valence_max": 0.42},
    "hurt":     {"valence_max": 0.34, "stress_min": 0.42},
    "tense":    {"stress_min": 0.62, "arousal_min": 0.55},
    "excited":  {"valence_min": 0.72, "arousal_min": 0.62},
    "happy":    {"valence_min": 0.66, "stress_max": 0.34},
    "curious":  {"curiosity_min": 0.66, "stress_max": 0.55},
    "quiet":    {"arousal_max": 0.22, "stress_max": 0.28},
}


# Per-user relation label thresholds. Same tuning contract as
# ``GROUP_LABEL_THRESHOLDS``: lower → more reactive labels, higher →
# more stoic. Insertion order = evaluation order. ``"neutral"`` is the
# default fallback and is absent from the table.
RELATION_LABEL_THRESHOLDS: dict[str, dict[str, float]] = {
    "guarded":     {"irritation_min": 0.68, "trust_max": 0.42},
    "attached":    {"affection_min": 0.66, "trust_min": 0.62, "irritation_max": 0.35},
    "trusted":     {"trust_min": 0.66, "irritation_max": 0.32},
    "irritated":   {"irritation_min": 0.55},
    "unfamiliar":  {"familiarity_max": 0.18},
}