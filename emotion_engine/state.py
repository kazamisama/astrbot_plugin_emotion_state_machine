"""Snapshot dataclasses and event types.

Three layers of state, each modeled as a mutable dataclass:

- :class:`GroupEmotionSnapshot` — shared conversation atmosphere
  (``valence``, ``arousal``, ``stress``, ``curiosity``).
- :class:`UserRelationSnapshot` — bot's private relation toward one
  user in one scope (``trust``, ``affection``, ``irritation``,
  ``familiarity``).
- :class:`CombinedEmotionView` — a read-only join used by the prompt
  injector and the ``/emotion_state`` command.

:class:`EmotionEvent` is the input event type consumed by the state
machine's dispatcher.

The snapshots are intentionally mutable: ``apply_interaction`` mutates
them in-place to keep the hot path allocation-free. Each class carries
its own ``to_dict`` / ``from_dict`` / ``normalize`` helpers so the
state machine can stay focused on dispatch and TTL logic instead of
serialization plumbing.

Label refresh inside ``normalize`` is what creates the only direct
dependency this module has on :mod:`.labels` — and that arrow is
one-way (labels don't know about snapshots except through ``getattr``,
so there is no cycle).
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .defaults import GROUP_BASELINE, RELATION_BASELINE
from .labels import (
    derive_combined_label,
    derive_group_label,
    derive_relation_label,
)
from .utils import clamp


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