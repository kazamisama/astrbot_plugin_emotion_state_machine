"""Discrete label derivation from snapshot dimensions.

A "label" is the human-readable one-word tag a snapshot carries
(``"calm"``, ``"annoyed"``, ``"attached"``, ...) — what shows up in
the ``/emotion_state`` command and in the prompt block. Labels are
computed from the continuous dimensions by threshold tables declared
in :mod:`.defaults`. Tuning the bot's apparent reactivity means editing
those tables, not this code.

``_eval_label_condition`` is exported because tests (and advanced
configurations) inspect the ``<dim>_min`` / ``<dim>_max`` convention
directly.

Snapshot types are referenced only via :func:`getattr`; this module
deliberately does NOT import from :mod:`.state` so it stays free of
circular dependencies (state normalization needs label derivation, so
the arrow points state → labels, never the other way).
"""

from __future__ import annotations

from typing import Any

from .defaults import GROUP_LABEL_THRESHOLDS, RELATION_LABEL_THRESHOLDS


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


def derive_group_label(snapshot: Any) -> str:
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


def derive_relation_label(snapshot: Any) -> str:
    """Map private relation dimensions to one relation label.

    Evaluation follows ``RELATION_LABEL_THRESHOLDS`` insertion order; the
    first matching label wins. ``"neutral"`` is the default fallback.
    """
    for label, thresholds in RELATION_LABEL_THRESHOLDS.items():
        if _eval_label_condition(snapshot, thresholds):
            return label
    return "neutral"


def derive_combined_label(group: Any, relation: Any | None = None) -> str:
    """Pick the effective label for prompt injection.

    The relation layer wins when it carries an opinion ("guarded" or
    "irritated" trump the group label). "attached" wins only when the
    group is calm enough to support it (high stress + attached feels
    wrong). Otherwise the group label stands on its own.
    """
    if relation is not None:
        if relation.label in {"guarded", "irritated"}:
            return relation.label
        if relation.label == "attached" and group.stress <= 0.55:
            return "attached"
    return group.label


def derive_label(snapshot: Any) -> str:
    """Backward-compatible alias for group label derivation."""
    return derive_group_label(snapshot)