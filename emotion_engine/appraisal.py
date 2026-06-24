"""Direct appraisal: apply a signal's weights to a snapshot.

This module is the ``appraisal_mode == "direct"`` implementation — the
canonical weight-delta path used by the state machine. It is the
production code path today and is byte-equivalent to the inline
implementation the old monolithic ``emotion_engine.py`` carried.

A planned ``appraisal_mode == "occ"`` will introduce OCC-style
intermediate appraisal variables (desirability, praiseworthiness,
unexpectedness, ...) and route them through this same
``apply_weights`` step. By isolating the deltas-mutation primitive in
its own module now, that future layer can sit on top without touching
either the state machine or the snapshot dataclasses.
"""

from __future__ import annotations

from typing import Any

from .utils import clamp


def apply_weights(target: Any, weights: dict[str, float], multiplier: float) -> None:
    """Mutate ``target`` by adding ``delta * multiplier`` for each
    ``(dim, delta)`` in ``weights``, clamping into ``[0.0, 1.0]``.

    ``target`` is duck-typed — the only requirement is that it has
    attribute names matching the dimension keys in ``weights`` (the
    group snapshot exposes ``valence`` / ``arousal`` / ``stress`` /
    ``curiosity``; the relation snapshot exposes ``trust`` / ``affection``
    / ``irritation`` / ``familiarity``).

    ``multiplier`` is the post-layer-weight, post-intensity multiplier
    that the dispatcher computes — i.e. ``intensity *
    layer_weight``. Callers should not re-multiply by intensity or
    layer weight themselves.
    """
    for key, delta in weights.items():
        current = getattr(target, key)
        setattr(target, key, clamp(current + delta * multiplier))