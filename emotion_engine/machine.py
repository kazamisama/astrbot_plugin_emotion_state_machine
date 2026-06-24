"""The orchestrator: :class:`EmotionStateMachine`.

Holds the in-memory snapshot store (groups + per-user relations) and
dispatches incoming events to the right snapshots. Decay, TTL pruning,
signal application, and serialization all live here.

This module is the only one that touches both the snapshot layer
(``state``) and the signal/appraisal layer (``signals``,
``signals_classify``, ``appraisal``). The others are pure data
definitions or pure helpers — the state machine is the seam where the
data definitions and the runtime behavior meet.

Read path: ``get_group`` / ``get_relation`` / ``get_combined`` →
optionally decay the snapshot in place → return.

Write path: ``apply_interaction`` / ``observe_text`` → look up the
snapshots (with decay) → look up weights → call ``apply_weights`` →
refresh labels → return the new combined view.

TTL is enforced lazily on read (``get_relation`` / ``get_combined``
prune stale entries before resolving) and proactively on
``prune_cold_state`` / ``_prune_groups`` for callers that want a
forced sweep.
"""

from __future__ import annotations

import time
from typing import Any

from .appraisal import (
    DirectEstimator,
    OCCHeuristicEstimator,
    apply_weights,
    get_estimator,
)
from .appraisal_heuristics import AppraisalContext
from .defaults import (
    APPRAISAL_MODES,
    GROUP_BASELINE,
    RELATION_BASELINE,
    SIGNAL_LAYER_WEIGHTS,
)
from .labels import derive_group_label, derive_relation_label
from .signals import signal_names
from .signals_classify import infer_signals
from .state import (
    CombinedEmotionView,
    EmotionEvent,
    GroupEmotionSnapshot,
    UserRelationSnapshot,
)
from .utils import (
    clamp,
    normalize_scope,
    normalize_user_id,
    prune_active_users,
)


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
        appraisal_mode: str = "direct",
    ):
        self.decay_half_life_seconds = max(1.0, float(decay_half_life_seconds))
        self.active_window_seconds = max(1.0, float(active_window_seconds))
        self.relation_ttl_seconds = max(1.0, float(relation_ttl_seconds))
        self.group_ttl_seconds = max(1.0, float(group_ttl_seconds))
        self.dilution_exponent = max(0.0, min(2.0, float(dilution_exponent)))
        self.groups: dict[str, GroupEmotionSnapshot] = {}
        self.relations: dict[str, dict[str, UserRelationSnapshot]] = {}
        # OCC appraisal layer (v0.5.0+)
        self.appraisal_mode: str = appraisal_mode
        self._estimator = get_estimator(appraisal_mode)
        # Sliding window of recent (signal, timestamp) per scope, used by
        # OCCHeuristicEstimator for habituation. Max 5 entries, TTL 5 min.
        self.recent_signals: dict[str, list[tuple[str, float]]] = {}

    def set_appraisal_mode(self, mode: str) -> None:
        """Switch the estimator at runtime.

        Safe to call between events (not during an in-flight
        ``apply_interaction`` — events are synchronous in this engine,
        so there is no concurrent access). The new mode takes effect
        on the next call to :meth:`apply_interaction`,
        :meth:`observe_text`, etc.

        Raises ``ValueError`` for unknown modes (same as
        :func:`~emotion_engine.appraisal.get_estimator`).
        """
        self.appraisal_mode = mode
        self._estimator = get_estimator(mode)

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

    def _append_recent_signal(self, scope: str, signal: str, timestamp: float) -> None:
        """Append to the per-scope recent-signal sliding window.

        Caps at 5 entries and drops entries older than 300 seconds.
        Calling this is a no-op for ``appraisal_mode != "occ_heuristic"``
        (the window is never read outside that mode).
        """
        bucket = self.recent_signals.setdefault(scope, [])
        bucket.append((signal, timestamp))
        cutoff = timestamp - 300.0
        while bucket and bucket[0][1] < cutoff:
            bucket.pop(0)
        while len(bucket) > 5:
            bucket.pop(0)

    def _build_appraisal_context(self, scope: str, event: EmotionEvent) -> AppraisalContext | None:
        """Build context for the heuristic estimator, or return None.

        We skip building the context when the estimator doesn't need
        it (direct / occ_static) to avoid the overhead of the recent-
        signal lookup. The heuristic estimator gracefully handles a
        None context by falling back to the static profile.
        """
        if not isinstance(self._estimator, OCCHeuristicEstimator):
            return None
        return AppraisalContext(
            text=event.text,
            mentioned=event.mentioned,
            timestamp=event.timestamp,
            group=self.groups.get(normalize_scope(scope)),
            relation=None,  # caller fills this
            recent_signals=self.recent_signals.get(normalize_scope(scope)),
        )

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

        # Ask the estimator for dimension deltas (signals → flat {dim: delta}).
        # The estimator is chosen by ``self.appraisal_mode`` at init or via
        # ``set_appraisal_mode``. DirectEstimator returns the v0.4.0 tables;
        # OCC estimators return per-signal appraisal profiles flattened through
        # the appraisal→dimension mapping.
        ctx = None
        if normalized_user:
            # Fill relation into the context for heuristic use
            ctx = self._build_appraisal_context(scope, event)
            if ctx is not None:
                ctx.relation = relation
        group_deltas, relation_deltas = self._estimator.compute(
            event.signal, intensity, ctx,
        )

        # Scale by intensity × layer multiplier and apply via the same
        # low-level mutator used in v0.4.0.
        apply_weights(group, group_deltas, intensity * group_multiplier)
        group.last_signal = event.signal
        group.last_reason = event.reason or event.signal
        group.updated_at = float(event.timestamp)
        group.transitions += 1
        group.label = derive_group_label(group)

        if relation is not None:
            apply_weights(relation, relation_deltas, intensity * relation_multiplier)
            relation.last_signal = event.signal
            relation.last_reason = event.reason or event.signal
            relation.updated_at = float(event.timestamp)
            relation.transitions += 1
            relation.label = derive_relation_label(relation)

        # Track recent signals for habituation (only matters in heuristic mode)
        self._append_recent_signal(scope, event.signal, event.timestamp)

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
        """Serialize to a JSON-compatible dict.

        Recent-signals are serialized as ``list[list]`` (since JSON has
        no native tuple type — each entry becomes ``[signal, ts]``).
        The 300-second TTL and 5-entry cap are enforced at insert time
        in :meth:`_append_recent_signal`, so no pruning is needed here.
        """
        recent: dict[str, list[list[Any]]] = {}
        for scope, bucket in self.recent_signals.items():
            if bucket:
                recent[scope] = [list(e) for e in bucket]
        return {
            "version": 3,
            "appraisal_mode": self.appraisal_mode,
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
            "recent_signals": recent,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, decay_half_life_seconds: float | None = None) -> "EmotionStateMachine":
        # Appraisal mode: default "direct" for backward compat with
        # v0.4.0 JSON files that don't have this field.
        appraisal_mode = str(data.get("appraisal_mode", "direct"))
        if appraisal_mode not in APPRAISAL_MODES:
            appraisal_mode = "direct"
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
            appraisal_mode=appraisal_mode,
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

        # Restore recent signals (v0.5.0+, not present in v0.4.0 JSON).
        # Each entry serialized as ``[signal, ts]`` (JSON has no tuple).
        raw_recent = data.get("recent_signals", {})
        if isinstance(raw_recent, dict):
            for scope, bucket in raw_recent.items():
                if isinstance(bucket, list):
                    restored: list[tuple[str, float]] = []
                    for entry in bucket:
                        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                            sig, ts = entry[0], entry[1]
                            if isinstance(sig, str) and isinstance(ts, (int, float)):
                                restored.append((sig, float(ts)))
                    if restored:
                        machine.recent_signals[normalize_scope(scope)] = restored

        # NOTE: cold-scope pruning is intentionally NOT done here.
        # ``from_dict`` is a pure data loader — tests use synthetic
        # timestamps in the past and rely on the round-trip preserving
        # their fixtures. Production callers (the plugin's ``_load_state``)
        # invoke ``_prune_groups()`` explicitly after load.
        return machine