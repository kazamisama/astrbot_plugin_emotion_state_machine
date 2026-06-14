"""
Emotion state engine for astrbot_plugin_emotion_state_machine.

This module is intentionally framework-free. It models bot emotion as a layered
state machine:

- group emotion: shared conversation atmosphere (valence, arousal, stress,
  curiosity)
- user relation: bot's private relation toward a specific user (trust,
  affection, irritation, familiarity)
- combined view: group atmosphere + current sender relation for prompt injection
- decay: both layers slowly move back to baseline over time
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any


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


@dataclass(frozen=True)
class EmotionEvent:
    """Input event consumed by the state machine."""

    signal: str
    intensity: float = 1.0
    reason: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class GroupEmotionSnapshot:
    """Shared emotion atmosphere for one conversation scope."""

    valence: float = GROUP_BASELINE["valence"]
    arousal: float = GROUP_BASELINE["arousal"]
    stress: float = GROUP_BASELINE["stress"]
    curiosity: float = GROUP_BASELINE["curiosity"]
    label: str = "calm"
    last_signal: str = "init"
    last_reason: str = "initialized"
    updated_at: float = field(default_factory=time.time)
    transitions: int = 0
    active_users: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in GROUP_BASELINE:
            data[key] = round(float(data[key]), 4)
        data["active_users"] = {str(k): round(float(v), 3) for k, v in self.active_users.items()}
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GroupEmotionSnapshot":
        kwargs = {name: data[name] for name in cls.__dataclass_fields__ if name in data}
        snapshot = cls(**kwargs)
        snapshot.normalize()
        return snapshot

    def normalize(self) -> None:
        self.valence = clamp(float(self.valence))
        self.arousal = clamp(float(self.arousal))
        self.stress = clamp(float(self.stress))
        self.curiosity = clamp(float(self.curiosity))
        self.transitions = max(0, int(self.transitions))
        self.updated_at = float(self.updated_at or time.time())
        self.active_users = {str(k): float(v) for k, v in (self.active_users or {}).items()}
        self.label = derive_group_label(self)


@dataclass
class UserRelationSnapshot:
    """Private relation state toward one user inside one scope."""

    trust: float = RELATION_BASELINE["trust"]
    affection: float = RELATION_BASELINE["affection"]
    irritation: float = RELATION_BASELINE["irritation"]
    familiarity: float = RELATION_BASELINE["familiarity"]
    label: str = "neutral"
    last_signal: str = "init"
    last_reason: str = "initialized"
    updated_at: float = field(default_factory=time.time)
    transitions: int = 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in RELATION_BASELINE:
            data[key] = round(float(data[key]), 4)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserRelationSnapshot":
        kwargs = {name: data[name] for name in cls.__dataclass_fields__ if name in data}
        snapshot = cls(**kwargs)
        snapshot.normalize()
        return snapshot

    def normalize(self) -> None:
        self.trust = clamp(float(self.trust))
        self.affection = clamp(float(self.affection))
        self.irritation = clamp(float(self.irritation))
        self.familiarity = clamp(float(self.familiarity))
        self.transitions = max(0, int(self.transitions))
        self.updated_at = float(self.updated_at or time.time())
        self.label = derive_relation_label(self)


@dataclass
class CombinedEmotionView:
    """Effective prompt view for a specific sender in a scope."""

    scope: str
    user_id: str
    group: GroupEmotionSnapshot
    relation: UserRelationSnapshot | None = None

    @property
    def label(self) -> str:
        return derive_combined_label(self.group, self.relation)


# Backward-compatible alias for older tests/imports. It represents group state.
EmotionSnapshot = GroupEmotionSnapshot


class EmotionStateMachine:
    """Manages group emotion and per-user relation snapshots."""

    def __init__(
        self,
        *,
        decay_half_life_seconds: float = 900.0,
        active_window_seconds: float = 300.0,
        relation_ttl_seconds: float = 604800.0,
        group_ttl_seconds: float = 2592000.0,
        dilution_exponent: float = 0.5,
    ):
        self.decay_half_life_seconds = max(1.0, float(decay_half_life_seconds))
        self.active_window_seconds = max(1.0, float(active_window_seconds))
        self.relation_ttl_seconds = max(1.0, float(relation_ttl_seconds))
        self.group_ttl_seconds = max(1.0, float(group_ttl_seconds))
        self.dilution_exponent = max(0.0, min(2.0, float(dilution_exponent)))
        self.groups: dict[str, GroupEmotionSnapshot] = {}
        self.relations: dict[str, dict[str, UserRelationSnapshot]] = {}

    @property
    def states(self) -> dict[str, GroupEmotionSnapshot]:
        """Backward-compatible view of group states."""
        return self.groups

    def get(self, scope: str, *, now: float | None = None, apply_decay: bool = True) -> GroupEmotionSnapshot:
        return self.get_group(scope, now=now, apply_decay=apply_decay)

    def get_group(self, scope: str, *, now: float | None = None, apply_decay: bool = True) -> GroupEmotionSnapshot:
        scope = normalize_scope(scope)
        snapshot = self.groups.get(scope)
        if snapshot is None:
            snapshot = GroupEmotionSnapshot()
            self.groups[scope] = snapshot
        if apply_decay:
            self.decay_group(scope, now=now)
        return snapshot

    def get_relation(
        self,
        scope: str,
        user_id: str,
        *,
        now: float | None = None,
        apply_decay: bool = True,
    ) -> UserRelationSnapshot:
        scope = normalize_scope(scope)
        user_id = normalize_user_id(user_id)
        if apply_decay:
            # Lazily prune stale relations to bound memory growth.
            self._prune_relations(scope, now=now)
        bucket = self.relations.setdefault(scope, {})
        snapshot = bucket.get(user_id)
        if snapshot is None:
            snapshot = UserRelationSnapshot()
            bucket[user_id] = snapshot
        if apply_decay:
            self.decay_relation(scope, user_id, now=now)
        return snapshot

    def get_combined(
        self,
        scope: str,
        user_id: str | None = None,
        *,
        now: float | None = None,
        apply_decay: bool = True,
    ) -> CombinedEmotionView:
        if apply_decay:
            # Prune stale relations up-front so combined reads honor TTL
            # even when the caller passes an empty user_id.
            self._prune_relations(scope, now=now)
        group = self.get_group(scope, now=now, apply_decay=apply_decay)
        relation = None
        normalized_user = normalize_user_id(user_id) if user_id else ""
        if normalized_user:
            relation = self.get_relation(scope, normalized_user, now=now, apply_decay=apply_decay)
        return CombinedEmotionView(scope=normalize_scope(scope), user_id=normalized_user, group=group, relation=relation)

    def reset(self, scope: str) -> GroupEmotionSnapshot:
        scope = normalize_scope(scope)
        snapshot = GroupEmotionSnapshot(last_signal="reset", last_reason="manual reset")
        self.groups[scope] = snapshot
        self.relations.pop(scope, None)
        return snapshot

    def decay_group(self, scope: str, *, now: float | None = None) -> GroupEmotionSnapshot:
        scope = normalize_scope(scope)
        snapshot = self.groups.get(scope)
        if snapshot is None:
            snapshot = GroupEmotionSnapshot()
            self.groups[scope] = snapshot
            return snapshot

        current = time.time() if now is None else float(now)
        elapsed = max(0.0, current - snapshot.updated_at)
        if elapsed <= 0:
            return snapshot

        retention = 0.5 ** (elapsed / self.decay_half_life_seconds)
        for key, baseline in GROUP_BASELINE.items():
            value = getattr(snapshot, key)
            setattr(snapshot, key, clamp(baseline + (value - baseline) * retention))
        prune_active_users(snapshot.active_users, current, self.active_window_seconds)
        snapshot.updated_at = current
        snapshot.label = derive_group_label(snapshot)
        return snapshot

    def decay_relation(self, scope: str, user_id: str, *, now: float | None = None) -> UserRelationSnapshot:
        scope = normalize_scope(scope)
        user_id = normalize_user_id(user_id)
        snapshot = self.get_relation(scope, user_id, apply_decay=False)
        current = time.time() if now is None else float(now)
        elapsed = max(0.0, current - snapshot.updated_at)
        if elapsed <= 0:
            return snapshot

        retention = 0.5 ** (elapsed / self.decay_half_life_seconds)
        for key, baseline in RELATION_BASELINE.items():
            value = getattr(snapshot, key)
            setattr(snapshot, key, clamp(baseline + (value - baseline) * retention))
        snapshot.updated_at = current
        snapshot.label = derive_relation_label(snapshot)
        return snapshot

    def _active_user_dilution(self, active_count: int) -> float:
        """Compute the per-signal dilution factor for a group of ``n`` active
        users. Returns ``1 / n ** dilution_exponent``.

        Default exponent ``0.5`` (sqrt curve) gives a gentle decay that
        still lets a single user's signal register in a busy room. Lower
        exponents (``0.0``–``0.3``) make the bot more reactive in crowds;
        higher exponents (``0.7``–``1.0``) make the bot more stoic in
        crowds. ``0.0`` disables dilution entirely.
        """
        n = max(1, int(active_count))
        exponent = self.dilution_exponent
        if exponent <= 0.0:
            return 1.0
        return 1.0 / (n ** exponent)

    def _prune_relations(self, scope: str, *, now: float | None = None) -> None:
        """Remove user relations whose snapshot is older than
        ``relation_ttl_seconds``.

        Runs lazily during ``get_relation`` / ``get_combined`` to bound the
        memory footprint of long-running bots in active groups. Stale
        entries are dropped entirely (not reset to baseline), so a returning
        user starts fresh — which is the intended semantic of a TTL.
        """
        bucket = self.relations.get(scope)
        if not bucket:
            return
        current = time.time() if now is None else float(now)
        cutoff = current - self.relation_ttl_seconds
        stale = [uid for uid, snap in bucket.items() if float(snap.updated_at) < cutoff]
        for uid in stale:
            bucket.pop(uid, None)
        if not bucket:
            self.relations.pop(scope, None)

    def _prune_groups(self, *, now: float | None = None) -> int:
        """Remove group snapshots whose ``updated_at`` is older than
        ``group_ttl_seconds``.

        Returns the number of groups pruned. Called at load time and
        before each save, so cold scopes don't accumulate in the JSON
        file. Stale groups are dropped entirely — a returning scope
        starts at baseline on the next message, same TTL semantic as
        :meth:`_prune_relations`.
        """
        if not self.groups:
            return 0
        current = time.time() if now is None else float(now)
        cutoff = current - self.group_ttl_seconds
        stale = [scope for scope, snap in self.groups.items()
                 if float(snap.updated_at) < cutoff]
        for scope in stale:
            self.groups.pop(scope, None)
            # Also drop the corresponding relations bucket — it would
            # be unreachable from the public API anyway.
            self.relations.pop(scope, None)
        return len(stale)

    def prune_cold_state(self, *, now: float | None = None) -> dict[str, int]:
        """Prune both cold groups and cold relations across all scopes.

        Public maintenance entry point. Returns a count dict:
        ``{"groups_pruned": int, "relations_pruned": int}``. Counts are
        reported as deltas (before - after), so they include relations
        that became unreachable when their parent group was pruned.
        Use this from a scheduled task (e.g. once per day) or to bound
        state size before reading.
        """
        # Count current entries so we can report accurate deltas.
        before_groups = len(self.groups)
        before_relations = sum(len(bucket) for bucket in self.relations.values())

        # 1. Prune cold groups (also drops unreachable relations).
        self._prune_groups(now=now)

        # 2. Walk remaining scopes and prune stale relations.
        current = time.time() if now is None else float(now)
        cutoff = current - self.relation_ttl_seconds
        for scope in list(self.relations.keys()):
            bucket = self.relations[scope]
            stale = [uid for uid, snap in bucket.items()
                     if float(snap.updated_at) < cutoff]
            for uid in stale:
                bucket.pop(uid, None)
            if not bucket:
                self.relations.pop(scope, None)

        after_groups = len(self.groups)
        after_relations = sum(len(bucket) for bucket in self.relations.values())
        return {
            "groups_pruned": before_groups - after_groups,
            "relations_pruned": before_relations - after_relations,
        }

    def decay(self, scope: str, *, now: float | None = None) -> GroupEmotionSnapshot:
        return self.decay_group(scope, now=now)

    def apply(self, scope: str, event: EmotionEvent) -> GroupEmotionSnapshot:
        """Backward-compatible group-only transition."""
        return self.apply_interaction(scope, None, event).group

    def apply_interaction(self, scope: str, user_id: str | None, event: EmotionEvent) -> CombinedEmotionView:
        scope = normalize_scope(scope)
        normalized_user = normalize_user_id(user_id) if user_id else ""
        group = self.get_group(scope, now=event.timestamp, apply_decay=True)
        relation = None
        if normalized_user:
            relation = self.get_relation(scope, normalized_user, now=event.timestamp, apply_decay=True)
            group.active_users[normalized_user] = float(event.timestamp)
            prune_active_users(group.active_users, event.timestamp, self.active_window_seconds)

        group_weight, relation_weight = SIGNAL_LAYER_WEIGHTS.get(event.signal, (0.5, 0.5))
        intensity = clamp(event.intensity, 0.0, 2.0)
        group_multiplier = group_weight * self._active_user_dilution(len(group.active_users) or 1)
        relation_multiplier = relation_weight

        apply_weights(group, GROUP_SIGNAL_WEIGHTS.get(event.signal, {}), intensity * group_multiplier)
        group.last_signal = event.signal
        group.last_reason = event.reason or event.signal
        group.updated_at = float(event.timestamp)
        group.transitions += 1
        group.label = derive_group_label(group)

        if relation is not None:
            apply_weights(relation, RELATION_SIGNAL_WEIGHTS.get(event.signal, {}), intensity * relation_multiplier)
            relation.last_signal = event.signal
            relation.last_reason = event.reason or event.signal
            relation.updated_at = float(event.timestamp)
            relation.transitions += 1
            relation.label = derive_relation_label(relation)

        return CombinedEmotionView(scope=scope, user_id=normalized_user, group=group, relation=relation)

    def observe_text(
        self,
        scope: str,
        text: str,
        *,
        user_id: str | None = None,
        mentioned: bool = False,
        timestamp: float | None = None,
        disabled_signals: set[str] | None = None,
    ) -> CombinedEmotionView:
        """Infer one or more signals from a plain message and apply them.

        ``disabled_signals``, when provided, is a set of lowercased signal
        names that must be filtered out before application. Passing
        ``None`` (the default) means no filtering.
        """
        now = time.time() if timestamp is None else float(timestamp)
        signals = infer_signals(text, mentioned=mentioned)
        if disabled_signals:
            signals = [
                (sig, reason) for sig, reason in signals
                if sig.lower() not in disabled_signals
            ]
        if not signals:
            return self.get_combined(scope, user_id, now=now, apply_decay=True)

        view: CombinedEmotionView | None = None
        for signal, reason in signals:
            view = self.apply_interaction(
                scope,
                user_id,
                EmotionEvent(signal=signal, intensity=1.0, reason=reason, timestamp=now),
            )
        return view if view is not None else self.get_combined(scope, user_id, now=now, apply_decay=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 2,
            "decay_half_life_seconds": self.decay_half_life_seconds,
            "active_window_seconds": self.active_window_seconds,
            "relation_ttl_seconds": self.relation_ttl_seconds,
            "group_ttl_seconds": self.group_ttl_seconds,
            "dilution_exponent": self.dilution_exponent,
            "groups": {scope: snapshot.to_dict() for scope, snapshot in self.groups.items()},
            "relations": {
                scope: {user_id: snapshot.to_dict() for user_id, snapshot in bucket.items()}
                for scope, bucket in self.relations.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, decay_half_life_seconds: float | None = None) -> "EmotionStateMachine":
        machine = cls(
            decay_half_life_seconds=(
                decay_half_life_seconds
                if decay_half_life_seconds is not None
                else float(data.get("decay_half_life_seconds", 900.0) or 900.0)
            ),
            active_window_seconds=float(data.get("active_window_seconds", 300.0) or 300.0),
            relation_ttl_seconds=float(data.get("relation_ttl_seconds", 604800.0) or 604800.0),
            group_ttl_seconds=float(data.get("group_ttl_seconds", 2592000.0) or 2592000.0),
            dilution_exponent=float(data.get("dilution_exponent", 0.5) or 0.5),
        )

        raw_groups = data.get("groups", data.get("states", {}))
        if isinstance(raw_groups, dict):
            for scope, raw in raw_groups.items():
                if isinstance(raw, dict):
                    machine.groups[normalize_scope(str(scope))] = GroupEmotionSnapshot.from_dict(raw)

        raw_relations = data.get("relations", {})
        if isinstance(raw_relations, dict):
            for scope, bucket in raw_relations.items():
                if not isinstance(bucket, dict):
                    continue
                normalized_scope = normalize_scope(str(scope))
                machine.relations[normalized_scope] = {}
                for user_id, raw in bucket.items():
                    if isinstance(raw, dict):
                        machine.relations[normalized_scope][normalize_user_id(str(user_id))] = (
                            UserRelationSnapshot.from_dict(raw)
                        )

        # NOTE: cold-scope pruning is intentionally NOT done here.
        # ``from_dict`` is a pure data loader — tests use synthetic
        # timestamps in the past and rely on the round-trip preserving
        # their fixtures. Production callers (the plugin's ``_load_state``)
        # invoke ``_prune_groups()`` explicitly after load.
        return machine


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def normalize_scope(scope: str) -> str:
    return str(scope or "_default")


def normalize_user_id(user_id: str | None) -> str:
    return str(user_id or "").strip()


def _ends_with_question_mark(text: str) -> bool:
    """True if the message ends with ``?`` or ``？`` (after stripping
    trailing whitespace). Bare ``?`` in the middle of a sentence does
    not count — see :func:`infer_signals` for the rationale.
    """
    stripped = text.rstrip()
    return bool(stripped) and stripped[-1] in ("?", "？")


def _contains_interrogative(text: str) -> bool:
    """True if the message contains a Chinese question word, modal
    question phrase, or the ``吗`` particle near the end.
    """
    if any(indicator in text for indicator in QUESTION_INDICATORS):
        return True
    # "吗" is a suffix; only treat it as a question marker if it sits
    # right before the end of a sentence (followed by optional terminal
    # punctuation). This avoids matching substrings like "马马虎虎".
    stripped = text.rstrip(" \t\n\r.!?。！？,，;；:：")
    return stripped.endswith("吗")


def infer_signals(text: str, *, mentioned: bool = False) -> list[tuple[str, str]]:
    """Infer emotion signals from a plain message.

    The ``question`` signal is fired only when:

    1. The message ends with ``?`` / ``？`` (most common case), **or**
    2. The message contains a Chinese interrogative word, modal question
       phrase, or the ``吗`` particle near the end.

    Bare ``?`` in the middle of a sentence (e.g. "OK? 然后...") is
    intentionally not enough — too noisy for casual group conversation
    where users sprinkle question marks into statements and rhetorical
    tags.
    """
    lowered = (text or "").lower()
    signals: list[tuple[str, str]] = []
    if mentioned:
        signals.append(("mention", "bot mentioned"))
    if _ends_with_question_mark(lowered) or _contains_interrogative(lowered):
        signals.append(("question", "question mark or interrogative"))
    for signal, keywords in KEYWORD_SIGNALS:
        if any(keyword.lower() in lowered for keyword in keywords):
            signals.append((signal, f"keyword:{signal}"))
    return dedupe_signals(signals)


def dedupe_signals(signals: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for signal, reason in signals:
        if signal in seen:
            continue
        seen.add(signal)
        result.append((signal, reason))
    return result[:4]


def prune_active_users(active_users: dict[str, float], now: float, window_seconds: float) -> dict[str, float]:
    """Drop entries older than ``now - window_seconds`` in place.

    Mutates ``active_users`` and returns the same object for backward
    compatibility with prior call sites. The previous implementation
    rebuilt a fresh dict on every invocation, which put allocation
    pressure on hot message paths in busy groups (one O(n) dict
    allocation per message). In-place deletion skips the copy entirely
    and only deletes the actual stale entries.

    The function tolerates non-dict input by returning it unchanged —
    this keeps the helper safe to call defensively.
    """
    if not active_users:
        return active_users
    cutoff = float(now) - float(window_seconds)
    stale = [user for user, ts in active_users.items() if float(ts) < cutoff]
    for user in stale:
        active_users.pop(user, None)
    return active_users


def _legacy_module_dilution(active_count: int) -> float:
    """Kept for backward-compatibility with any external callers that still
    import ``active_user_dilution`` from this module. Delegates to the
    ``EmotionStateMachine`` default (sqrt curve) when called as a free
    function.
    """
    return 1.0 / math.sqrt(max(1, int(active_count)))


# Backward-compatible alias. New callers should use
# ``EmotionStateMachine._active_user_dilution`` to honor the configured
# ``dilution_exponent``.
active_user_dilution = _legacy_module_dilution


def apply_weights(target: Any, weights: dict[str, float], multiplier: float) -> None:
    for key, delta in weights.items():
        current = getattr(target, key)
        setattr(target, key, clamp(current + delta * multiplier))


def _eval_label_condition(
    snapshot: Any,
    thresholds: dict[str, float],
) -> bool:
    """Return True if ``snapshot`` satisfies every entry in ``thresholds``.

    Convention: keys ending in ``_min`` test ``value >= threshold``;
    keys ending in ``_max`` test ``value <= threshold``. Any other key
    is treated as a direct ``value == threshold`` equality (used for
    exact-match labels). Unknown dimension names raise ``AttributeError``
    so misconfiguration fails loudly rather than silently mismatching.
    """
    for key, threshold in thresholds.items():
        value = getattr(snapshot, key.rsplit("_", 1)[0])
        if key.endswith("_min"):
            if not (value >= threshold):
                return False
        elif key.endswith("_max"):
            if not (value <= threshold):
                return False
        else:
            if value != threshold:
                return False
    return True


def derive_group_label(snapshot: GroupEmotionSnapshot) -> str:
    """Map shared group dimensions to one stable atmosphere label.

    Evaluation follows ``GROUP_LABEL_THRESHOLDS`` insertion order; the
    first matching label wins. ``"calm"`` is returned when no label
    matches. To tune the bot's apparent reactivity, edit the threshold
    dict — not the function body.
    """
    for label, thresholds in GROUP_LABEL_THRESHOLDS.items():
        if _eval_label_condition(snapshot, thresholds):
            return label
    return "calm"


def derive_relation_label(snapshot: UserRelationSnapshot) -> str:
    """Map private relation dimensions to one relation label.

    Evaluation follows ``RELATION_LABEL_THRESHOLDS`` insertion order; the
    first matching label wins. ``"neutral"`` is the default fallback.
    """
    for label, thresholds in RELATION_LABEL_THRESHOLDS.items():
        if _eval_label_condition(snapshot, thresholds):
            return label
    return "neutral"


def derive_combined_label(group: GroupEmotionSnapshot, relation: UserRelationSnapshot | None = None) -> str:
    if relation is not None:
        if relation.label in {"guarded", "irritated"}:
            return relation.label
        if relation.label == "attached" and group.stress <= 0.55:
            return "attached"
    return group.label


def derive_label(snapshot: GroupEmotionSnapshot) -> str:
    """Backward-compatible alias for group label derivation."""
    return derive_group_label(snapshot)


def format_snapshot(scope: str, snapshot: GroupEmotionSnapshot) -> str:
    age = max(0.0, time.time() - snapshot.updated_at)
    return (
        f"🧭 Group Emotion | {scope}\n"
        f"- label: {snapshot.label}\n"
        f"- valence: {snapshot.valence:.2f}\n"
        f"- arousal: {snapshot.arousal:.2f}\n"
        f"- stress: {snapshot.stress:.2f}\n"
        f"- curiosity: {snapshot.curiosity:.2f}\n"
        f"- active_users: {len(snapshot.active_users)}\n"
        f"- last_signal: {snapshot.last_signal}\n"
        f"- last_reason: {snapshot.last_reason}\n"
        f"- transitions: {snapshot.transitions}\n"
        f"- updated: {age:.0f}s ago"
    )


def format_relation(scope: str, user_id: str, snapshot: UserRelationSnapshot) -> str:
    age = max(0.0, time.time() - snapshot.updated_at)
    return (
        f"👤 User Relation | {scope} / {user_id}\n"
        f"- label: {snapshot.label}\n"
        f"- trust: {snapshot.trust:.2f}\n"
        f"- affection: {snapshot.affection:.2f}\n"
        f"- irritation: {snapshot.irritation:.2f}\n"
        f"- familiarity: {snapshot.familiarity:.2f}\n"
        f"- last_signal: {snapshot.last_signal}\n"
        f"- last_reason: {snapshot.last_reason}\n"
        f"- transitions: {snapshot.transitions}\n"
        f"- updated: {age:.0f}s ago"
    )


def format_combined_view(view: CombinedEmotionView) -> str:
    text = format_snapshot(view.scope, view.group)
    if view.relation is not None:
        text += "\n\n" + format_relation(view.scope, view.user_id, view.relation)
        text += f"\n\n- combined_label: {view.label}"
    return text


# Sentinel markers wrap the emotion block so the plugin can find and
# replace the block on re-injection (instead of appending a duplicate).
# HTML comments are invisible to all known LLM tokenizers and pass through
# system prompts untouched.
ESM_BLOCK_START = "<!-- esm:emotion-block:start -->"
ESM_BLOCK_END = "<!-- esm:emotion-block:end -->"


def build_prompt_block(scope: str, view_or_snapshot: CombinedEmotionView | GroupEmotionSnapshot) -> str:
    """Build a low-noise prompt block for LLM context injection.

    The returned string is wrapped in ``ESM_BLOCK_START`` / ``ESM_BLOCK_END``
    sentinel markers so the plugin can detect and replace a previous
    injection instead of stacking duplicates. The markers are HTML-style
    comments and are not rendered or interpreted by LLMs.
    """
    if isinstance(view_or_snapshot, CombinedEmotionView):
        view = view_or_snapshot
    else:
        view = CombinedEmotionView(scope=scope, user_id="", group=view_or_snapshot, relation=None)

    style_hint = style_hint_for(view)
    group = view.group
    relation = view.relation
    relation_line = "towards_current_user: unavailable"
    if relation is not None:
        relation_line = (
            f"towards_current_user: label={relation.label}, trust={relation.trust:.2f}, "
            f"affection={relation.affection:.2f}, irritation={relation.irritation:.2f}, "
            f"familiarity={relation.familiarity:.2f}"
        )

    inner = (
        "## Bot Emotion State\n"
        f"scope: {scope}\n"
        f"combined_label: {view.label}\n"
        f"group: label={group.label}, valence={group.valence:.2f}, arousal={group.arousal:.2f}, "
        f"stress={group.stress:.2f}, curiosity={group.curiosity:.2f}, active_users={len(group.active_users)}\n"
        f"{relation_line}\n"
        f"last_signal: group={group.last_signal}"
        + (f", user={relation.last_signal}" if relation is not None else "")
        + "\n"
        f"style_hint: {style_hint}\n"
        "Use this as subtle continuity only. Do not mention numeric scores unless explicitly asked."
    )
    return f"{ESM_BLOCK_START}\n{inner}\n{ESM_BLOCK_END}"


def style_hint_for(view_or_snapshot: CombinedEmotionView | GroupEmotionSnapshot) -> str:
    if isinstance(view_or_snapshot, CombinedEmotionView):
        group = view_or_snapshot.group
        relation = view_or_snapshot.relation
    else:
        group = view_or_snapshot
        relation = None

    if relation is not None:
        if relation.label == "guarded":
            return "be concise and careful with this user; avoid playful escalation"
        if relation.label == "irritated":
            return "keep boundaries, answer plainly, do not intensify conflict"
        if relation.label == "attached" and group.stress <= 0.55:
            return "soft, familiar, naturally attentive to the current user"
        if relation.label == "trusted":
            return "relaxed and cooperative with the current user"

    hints = {
        "annoyed": "slightly sharper and brief, but not hostile",
        "hurt": "quiet, restrained, avoid playful provocation",
        "tense": "efficient, direct, reduce noise",
        "excited": "more energetic and responsive",
        "happy": "warm and relaxed",
        "curious": "ask precise follow-up only when useful",
        "quiet": "low-energy, concise companionship",
        "calm": "balanced and natural",
    }
    return hints.get(group.label, hints["calm"])


def signal_names() -> list[str]:
    return sorted(GROUP_SIGNAL_WEIGHTS.keys())
