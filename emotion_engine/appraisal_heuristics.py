"""Heuristic OCC appraisal adjustments.

Used only when ``appraisal_mode == "occ_heuristic"``. Each function
takes a baseline appraisal value and a context, returns an adjusted
value. All functions are **pure** (no I/O, no time.time() calls inside
the math — timestamps are passed in) so they are individually unit-
testable and the math is reproducible across calls.

The functions are intentionally small and focused — each one nudges
exactly one appraisal variable along one observable axis (text
features, group state, relation trust, etc.). Interference is
controlled by capping each adjustment within a tight range
(typically ``±0.10`` to ``±0.30`` per function), so two heuristics
hitting the same variable never conspire to produce a wild swing.

Why no LLM? The plugin is on the hot message path. LLM calls would
add 200ms+ latency and break the "lightweight state machine" design
goal. The heuristics below capture the *cheap* signal: punctuation
density, character repetition, emoji polarity, group arousal, and
trust-based weighting. Sarcasm and in-jokes require semantic
understanding and remain an explicit gap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .defaults import SIGNAL_APPRAISAL_PROFILES
from .state import GroupEmotionSnapshot, UserRelationSnapshot


@dataclass
class AppraisalContext:
    """All the local information an estimator might want.

    Built by :meth:`EmotionStateMachine.apply_interaction` for every
    call. The estimator can use as many or as few fields as it likes;
    fields default to "no info" so an estimator can also be called
    with a partially-filled context (e.g. from a test fixture).
    """

    text: str = ""
    mentioned: bool = False
    timestamp: float = 0.0
    group: GroupEmotionSnapshot | None = None
    relation: UserRelationSnapshot | None = None
    recent_signals: list[tuple[str, float]] | None = None


# ---------------------------------------------------------------------------
# Feature sets
# ---------------------------------------------------------------------------

# A small positive/negative emoji set. Not exhaustive — we just need
# enough signal to nudge appraisal by ±0.10. The exact set can be
# extended by the operator via configuration in a later version.
_POSITIVE_EMOJI = frozenset("😀😃😄😁😆😊🥰😍🤗👍💖✨🎉🌟💕❤😺😻")
_NEGATIVE_EMOJI = frozenset("😢😭😞😔😡🤬💔😰😨🥺😖😣😩🙁☹")

# Repeated character pattern: 啊啊啊 / 哈哈哈 / 啊啊啊啊 / 666
# Matches a single character repeated 3+ times (so 啊呀呀 doesn't match
# but 啊啊啊 does).
_REPEATED_CHARS = re.compile(r"(.)\1{2,}")


# ---------------------------------------------------------------------------
# Heuristic functions — each is small, pure, and independently testable
# ---------------------------------------------------------------------------

def _arousal_from_text(base: float, text: str) -> float:
    """Punctuation density + character repetition + length drive arousal up.

    Long all-caps exclamations like "谢谢！！！啊啊啊" should produce
    a higher arousal than the same praise said flatly. We use
    additive nudges capped at ``+0.30`` so even a maximally emphatic
    message can't push a calm appraisal variable into a manic one.
    """
    excl = text.count("！") + text.count("!")
    qmark = text.count("？") + text.count("?")
    repeats = bool(_REPEATED_CHARS.search(text))
    length_factor = min(1.0, len(text) / 100.0)
    boost = (
        excl * 0.05
        + qmark * 0.02
        + (0.1 if repeats else 0.0)
        + length_factor * 0.1
    )
    # Cap the total nudge to ±0.30 so a single heuristic can't dominate.
    return min(1.0, base + min(0.30, boost))


def _expectedness_from_recent(
    base: float, same_signal_recent_ts: float | None, now: float
) -> float:
    """Habituation: same-signal repetition in the last 2 minutes raises
    expectedness (and thus lowers novelty when paired with OCC).

    A 30-second window counts as "rapid repetition" (full +0.30
    bump); a 2-minute window counts as "lukewarm" (+0.15). Beyond
    2 minutes the signal is treated as independent.
    """
    if same_signal_recent_ts is None:
        return base
    age = max(0.0, now - same_signal_recent_ts)
    if age < 30.0:
        return min(1.0, base + 0.30)
    if age < 120.0:
        return min(1.0, base + 0.15)
    return base


def _praiseworthiness_from_trust(
    base: float, relation: UserRelationSnapshot | None
) -> float:
    """Trusted users' praise counts more; irritated users' praise is
    discounted (sarcasm risk).

    Three regimes:
    - **No relation** (stranger): mild 15% discount — first-time praise
      is plausible but not as weighty.
    - **High trust** (>0.7): 10% boost — close friend's praise is more
      genuine and more impactful.
    - **High irritation** (>0.6): 50% discount — someone you've been
      arguing with suddenly praising you is much more likely to be
      sarcasm or backhanded.
    - **Otherwise**: pass-through.
    """
    if relation is None:
        return base * 0.85
    if relation.trust > 0.7:
        return min(1.0, base * 1.10)
    if relation.irritation > 0.6:
        return base * 0.5
    return base


def _desirability_from_emoji(text: str) -> float:
    """Return a signed nudge in ``[-0.10, +0.10]`` based on emoji polarity.

    Returns ``+0.10`` for any positive emoji, ``-0.10`` for any
    negative emoji, ``0.0`` if neither is present. Only one direction
    fires per message (we don't double-count if the user pasted both
    positive and negative emoji — unlikely in practice, and the
    absolute cap at 0.10 keeps the math safe).

    Designed as a **delta** to be applied by the caller, not a
    replacement value — see :func:`estimate_appraisal` for usage.
    Returning a signed amount rather than a clamped absolute value
    is what lets the negative direction actually reach the profile
    (a clamp-at-0 base would swallow the sign).
    """
    if any(c in text for c in _POSITIVE_EMOJI):
        return +0.10
    if any(c in text for c in _NEGATIVE_EMOJI):
        return -0.10
    return 0.0


def _arousal_from_group_state(
    base: float, group: GroupEmotionSnapshot | None
) -> float:
    """Tense groups amplify emotional intensity; calm groups dampen it.

    This implements a basic "mood congruence" effect: a praise landed
    in a tense room feels more intense than the same praise in a calm
    room. Range is [×0.8, ×1.2] to keep the nudge subtle.
    """
    if group is None:
        return base
    if group.arousal > 0.7:
        return min(1.0, base * 1.2)
    if group.arousal < 0.2:
        return base * 0.8
    return base


# ---------------------------------------------------------------------------
# Top-level estimator entry point
# ---------------------------------------------------------------------------

def estimate_appraisal(signal: str, ctx: AppraisalContext) -> dict[str, float]:
    """Compute the adjusted appraisal profile for ``signal`` given
    the context.

    Starts from the static profile in
    :data:`emotion_engine.defaults.SIGNAL_APPRAISAL_PROFILES`, then
    applies each applicable heuristic in turn. The order matters only
    for the mentioned-nudge at the end, which we apply last so it
    always has a chance to nudge the final value.

    The returned dict contains only variables present in the static
    profile for this signal (heuristics never *add* variables, only
    *adjust* existing ones). Unknown signals return an empty dict.
    """
    profile = dict(SIGNAL_APPRAISAL_PROFILES.get(signal, {}))

    # Text-feature adjustments
    if "arousal" in profile:
        profile["arousal"] = _arousal_from_text(profile["arousal"], ctx.text)
    if "desirability" in profile or "undesirability" in profile:
        emoji_nudge = _desirability_from_emoji(ctx.text)
        if "desirability" in profile:
            profile["desirability"] = max(0.0, min(1.0, profile["desirability"] + emoji_nudge))
        if "undesirability" in profile:
            # Mirror image: positive emoji reduces undesirability,
            # negative emoji amplifies it.
            profile["undesirability"] = max(0.0, min(1.0, profile["undesirability"] - emoji_nudge))

    # Context adjustments
    if "praiseworthiness" in profile:
        profile["praiseworthiness"] = _praiseworthiness_from_trust(
            profile["praiseworthiness"], ctx.relation
        )
    if "arousal" in profile:
        profile["arousal"] = _arousal_from_group_state(
            profile["arousal"], ctx.group
        )
    if "expectedness" in profile and ctx.recent_signals:
        same_ts = next(
            (t for s, t in reversed(ctx.recent_signals) if s == signal),
            None,
        )
        if same_ts is not None:
            profile["expectedness"] = _expectedness_from_recent(
                profile["expectedness"], same_ts, ctx.timestamp
            )

    # Mentioned-nudge: applied last so it always wins ties.
    if ctx.mentioned:
        if "arousal" in profile:
            profile["arousal"] = min(1.0, profile["arousal"] + 0.10)
        if "expectedness" in profile:
            profile["expectedness"] = max(0.0, profile["expectedness"] * 0.5)

    return profile