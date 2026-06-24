"""Appraisal layer: how a signal becomes a dimension delta.

Three estimator strategies, all behind the same interface:

- :class:`DirectEstimator` — original v0.4.0 behavior. Signal → group/relation
  dimension deltas, looked up directly in the legacy ``*_SIGNAL_WEIGHTS``
  tables. Zero transformation, byte-equivalent to pre-v0.5.0.

- :class:`OCCStaticEstimator` — OCC appraisal (Ortony, Clore, Collins
  1988) reduced to a static lookup. Signal → appraisal profile (a
  weighted set of OCC variables: praiseworthiness, blameworthiness,
  desirability, …) → dimension deltas via the appraisal→dimension
  mapping table. No text or context awareness.

- :class:`OCCHeuristicEstimator` — same as static, but adjusts the
  appraisal profile per-call using text features (punctuation,
  repetition, emoji), group state (arousal amplification), and user
  relation (trust-weighted praise). Pure functions only, no LLM.

All three estimators return ``(group_deltas, relation_deltas)`` as
flat ``{dim: delta}`` dicts, **unscaled by intensity**. The state
machine applies them via :func:`apply_weights`, multiplying by
intensity and the per-layer multiplier — same as the v0.4.0 path.

The factory :func:`get_estimator` resolves the configured mode string
to an instance. The state machine caches the instance and offers a
runtime setter for switching modes without restart.

Backward compatibility
----------------------

:func:`apply_weights` is preserved as a free function and continues
to be importable from the package root. Legacy callers (tests,
external plugins) that do ``from emotion_engine import apply_weights``
keep working unchanged.
"""

from __future__ import annotations

from typing import Protocol

from .appraisal_heuristics import AppraisalContext, estimate_appraisal
from .defaults import (
    APPRAISAL_MODES,
    APPRAISAL_TO_DIMENSION_GROUP,
    APPRAISAL_TO_DIMENSION_RELATION,
    GROUP_SIGNAL_WEIGHTS,
    RELATION_SIGNAL_WEIGHTS,
    SIGNAL_APPRAISAL_PROFILES,
)
from .utils import clamp


# ---------------------------------------------------------------------------
# Low-level mutator (kept for backward compat)
# ---------------------------------------------------------------------------

def apply_weights(target: object, weights: dict[str, float], multiplier: float) -> None:
    """Mutate ``target`` by adding ``delta * multiplier`` for each
    ``(dim, delta)`` in ``weights``, clamping into ``[0.0, 1.0]``.

    This is the canonical low-level application primitive — it knows
    nothing about signals or appraisal. All three estimators above
    produce flat ``{dim: delta}`` dicts that this function consumes.

    ``target`` is duck-typed (must expose attributes matching the
    dimension keys). ``multiplier`` is the post-layer-weight,
    post-intensity multiplier that the dispatcher computes — callers
    should not re-multiply by intensity or layer weight themselves.
    """
    for key, delta in weights.items():
        current = getattr(target, key)
        setattr(target, key, clamp(current + delta * multiplier))


# ---------------------------------------------------------------------------
# Estimator interface
# ---------------------------------------------------------------------------

class AppraisalEstimator(Protocol):
    """Strategy interface: signal + context → flat dimension deltas.

    The state machine calls :meth:`compute` once per
    :class:`~emotion_engine.state.EmotionEvent`. The returned tuple is
    ``(group_deltas, relation_deltas)`` — both are flat
    ``{dimension_name: delta}`` dicts in the same shape as the
    legacy ``GROUP_SIGNAL_WEIGHTS`` and ``RELATION_SIGNAL_WEIGHTS``.

    Values are **per-unit intensity** and **per-unit layer weight**;
    the dispatcher scales by ``intensity * layer_multiplier`` before
    passing to :func:`apply_weights`.
    """

    def compute(
        self,
        signal: str,
        intensity: float,  # accepted for protocol symmetry; estimators don't use it
        ctx: AppraisalContext | None = None,
    ) -> tuple[dict[str, float], dict[str, float]]: ...


# ---------------------------------------------------------------------------
# Direct estimator — v0.4.0 behavior, byte-equivalent
# ---------------------------------------------------------------------------

class DirectEstimator:
    """The pre-v0.5.0 inline weights lookup, surfaced as a class.

    Behavior is identical to the v0.4.0 path: signal → flat
    ``{dim: delta}`` from the legacy ``*_SIGNAL_WEIGHTS`` tables,
    ignoring any context. ``intensity`` and ``ctx`` are accepted
    for protocol symmetry but never used.

    Use this when you want v0.4.0 behavior with the v0.5.0
    architecture. Default mode of the state machine.
    """

    def compute(
        self,
        signal: str,
        intensity: float,
        ctx: AppraisalContext | None = None,
    ) -> tuple[dict[str, float], dict[str, float]]:
        return (
            dict(GROUP_SIGNAL_WEIGHTS.get(signal, {})),
            dict(RELATION_SIGNAL_WEIGHTS.get(signal, {})),
        )


# ---------------------------------------------------------------------------
# OCC static estimator — two-step lookup, no per-call adjustment
# ---------------------------------------------------------------------------

def _flatten_profile(
    profile: dict[str, float],
    mapping: dict[str, dict[str, float]],
) -> dict[str, float]:
    """Convert an appraisal profile into dimension deltas.

    For each ``(appraisal_var, value)`` in the profile and each
    ``(dimension, weight)`` in ``mapping[appraisal_var]``, accumulate
    ``value * weight`` into ``deltas[dimension]``. Variables not
    present in the mapping are silently ignored (forward-compat:
    new appraisal variables can be added to profiles without
    requiring immediate mapping table updates).
    """
    deltas: dict[str, float] = {}
    for var, value in profile.items():
        var_to_dim = mapping.get(var, {})
        for dim, weight in var_to_dim.items():
            deltas[dim] = deltas.get(dim, 0.0) + value * weight
    return deltas


class OCCStaticEstimator:
    """OCC appraisal reduced to a static lookup.

    Signal → appraisal profile (canonical strengths of praiseworthiness,
    blameworthiness, etc.) → dimension deltas. Values are in the same
    order of magnitude as :class:`DirectEstimator` but the path is
    more decomposable — tuning "what does praiseworthiness do to the
    bot's state" no longer requires editing every signal entry that
    uses it; just edit the mapping table.
    """

    def compute(
        self,
        signal: str,
        intensity: float,
        ctx: AppraisalContext | None = None,
    ) -> tuple[dict[str, float], dict[str, float]]:
        profile = SIGNAL_APPRAISAL_PROFILES.get(signal, {})
        return (
            _flatten_profile(profile, APPRAISAL_TO_DIMENSION_GROUP),
            _flatten_profile(profile, APPRAISAL_TO_DIMENSION_RELATION),
        )


# ---------------------------------------------------------------------------
# OCC heuristic estimator — static + per-call adjustments
# ---------------------------------------------------------------------------

class OCCHeuristicEstimator:
    """OCC + heuristic adjustments from text features and context state.

    Delegates to :func:`estimate_appraisal` (in
    :mod:`.appraisal_heuristics`) which starts from the static profile
    and nudges each variable based on punctuation density, character
    repetition, emoji polarity, group arousal, and trust-weighted
    praise. All heuristics are pure functions; the result is
    deterministic for a given context.

    If ``ctx`` is ``None``, falls back to the static estimator's
    output (so this estimator can be used uniformly even when no
    text or context is available).
    """

    def compute(
        self,
        signal: str,
        intensity: float,
        ctx: AppraisalContext | None = None,
    ) -> tuple[dict[str, float], dict[str, float]]:
        if ctx is None:
            return OCCStaticEstimator().compute(signal, intensity, ctx)
        profile = estimate_appraisal(signal, ctx)
        return (
            _flatten_profile(profile, APPRAISAL_TO_DIMENSION_GROUP),
            _flatten_profile(profile, APPRAISAL_TO_DIMENSION_RELATION),
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_estimator(mode: str) -> AppraisalEstimator:
    """Resolve a mode name to an estimator instance.

    The returned instance is **stateless** and safe to share across
    threads (the state machine caches it). Switching modes at
    runtime is safe; in-flight events use the estimator that was
    current when the event was queued, so no mid-event tearing is
    possible (events are synchronous in this engine).

    Raises ``ValueError`` for unknown modes. Use
    :data:`emotion_engine.defaults.APPRAISAL_MODES` to enumerate the
    valid set.
    """
    if mode == "direct":
        return DirectEstimator()
    if mode == "occ_static":
        return OCCStaticEstimator()
    if mode == "occ_heuristic":
        return OCCHeuristicEstimator()
    raise ValueError(
        f"Unknown appraisal_mode: {mode!r}. "
        f"Valid modes: {', '.join(APPRAISAL_MODES)}"
    )