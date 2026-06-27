"""Tests for the pure :class:`TalkWillingnessState` accumulator.

TalkWillingnessState lives at module level in ``main.py`` so it can be
imported directly without standing up the full AstrBot runtime.

These tests focus on the math:

- Time charge accumulation (寂寞蓄力)
- Turn-density charge (满足感)
- Self-emotion modulation (心情差/兴奋好奇)
- Threshold zones: accumulation / trigger / reversal
- Refractory suppression window
- Consecutive-apply cap
- User-message arrival resets the consecutive counter
- Scope cleanup leaves no residue

Design note on test pacing: TalkWillingnessState's steady-state W
(under default decay=0.92, happy-view emotion_charge=0.04, time_charge
~0.0225 at 100s ticks) sits around 0.7, which is in the trigger zone.
Reaching the trigger zone from W=0 takes ~40-50 ticks because each
tick only adds ~0.06 to W against 8% decay. Tests that need to
exercise the trigger / reversal / refractory paths use 1-second tick
intervals over 1000-5000 simulated seconds (~1000-5000 ticks) so W
definitely settles into the trigger zone.
"""

from __future__ import annotations

import pytest

from main import TalkWillingnessState, _TalkWillingness


# ----------------------------------------------------------------------
# Test helpers
# ----------------------------------------------------------------------


class _FakeGroupView:
    """Minimal stand-in for ``GroupEmotionSnapshot``."""

    def __init__(
        self,
        valence: float = 0.5,
        arousal: float = 0.5,
        curiosity: float = 0.5,
    ) -> None:
        self.valence = valence
        self.arousal = arousal
        self.curiosity = curiosity


def _neutral_view() -> _FakeGroupView:
    return _FakeGroupView()


def _happy_view() -> _FakeGroupView:
    return _FakeGroupView(valence=0.6, arousal=0.7, curiosity=0.7)


def _sad_view() -> _FakeGroupView:
    return _FakeGroupView(valence=0.2, arousal=0.3, curiosity=0.3)


def _seed_w(tw: TalkWillingnessState, scope: str, w: float) -> None:
    """Test helper: set a scope's W to a specific value.

    Tests for behavior past the trigger zone (refractory, consecutive
    cap, reversal) need W in a known region. Driving W from 0 to 0.85
    via natural accumulation takes 50+ ticks at typical intervals —
    too slow for unit tests. Seeding W directly sidesteps that while
    still exercising the real threshold / refractory / cap code.
    """
    state = tw._states.setdefault(scope, _TalkWillingness())
    state.W = w


# ----------------------------------------------------------------------
# Basic shape
# ----------------------------------------------------------------------


def test_tick_returns_triple() -> None:
    """``tick`` returns ``(W, should_apply, intensity)``."""
    tw = TalkWillingnessState()
    W, should_apply, intensity = tw.tick(
        scope="g", now=100.0,
        user_message_arrived=False,
        group_view=_neutral_view(),
        user_turns_in_5min=0,
    )
    assert isinstance(W, float)
    assert isinstance(should_apply, bool)
    assert isinstance(intensity, float)
    assert intensity >= 0.0


def test_initial_W_is_zero_no_apply() -> None:
    """A fresh scope at t=0 should not trigger — W starts at 0."""
    tw = TalkWillingnessState()
    W, should_apply, intensity = tw.tick(
        scope="fresh", now=0.0,
        user_message_arrived=False,
        group_view=_neutral_view(),
        user_turns_in_5min=0,
    )
    assert W == pytest.approx(0.0)
    assert should_apply is False
    assert intensity == 0.0


# ----------------------------------------------------------------------
# Time charge (寂寞蓄力) — direct helper tests
# ----------------------------------------------------------------------


def test_no_time_charge_under_30s_silence() -> None:
    """Below the silence floor (30s), the time charge formula returns 0."""
    tw = TalkWillingnessState()
    assert tw._time_charge(0.0, user_msg=False) == 0.0
    assert tw._time_charge(15.0, user_msg=False) == 0.0
    assert tw._time_charge(29.999, user_msg=False) == 0.0


def test_time_charge_kicks_in_after_30s() -> None:
    """Past 30s of silence, time charge scales linearly with elapsed."""
    tw = TalkWillingnessState()
    assert tw._time_charge(30.0, user_msg=False) == pytest.approx(0.0)
    # 60s elapsed → 30s headroom / 600s window × 0.15 max = 0.0075
    assert tw._time_charge(60.0, user_msg=False) == pytest.approx(0.0075)
    # 330s elapsed → 300s headroom / 600s × 0.15 = 0.075
    assert tw._time_charge(330.0, user_msg=False) == pytest.approx(0.075)


def test_time_charge_capped_at_10min() -> None:
    """Past 10.5 minutes of silence (headroom ≥ TIME_WINDOW), the
    charge saturates at 0.15. (10 minutes total elapsed because
    TIME_SILENCE_FLOOR=30s eats the first half-minute.)
    """
    tw = TalkWillingnessState()
    # At 630s elapsed: headroom = 600s = TIME_WINDOW exactly → 0.15
    assert tw._time_charge(630.0, user_msg=False) == pytest.approx(0.15)
    assert tw._time_charge(3600.0, user_msg=False) == pytest.approx(0.15)
    assert tw._time_charge(86400.0, user_msg=False) == pytest.approx(0.15)


def test_user_message_zeroes_time_charge() -> None:
    """A user message suppresses the time charge to 0 even after hours."""
    tw = TalkWillingnessState()
    assert tw._time_charge(3600.0, user_msg=True) == 0.0


# ----------------------------------------------------------------------
# Turn-density charge (满足感) — direct helper tests
# ----------------------------------------------------------------------


def test_high_turn_density_suppresses() -> None:
    """≥3 user turns → -0.10."""
    tw = TalkWillingnessState()
    assert tw._turn_charge(3, elapsed=600.0) == -0.10
    assert tw._turn_charge(10, elapsed=60.0) == -0.10


def test_zero_turns_with_recent_elapsed_is_awkward() -> None:
    """0 user turns + elapsed < 60s → -0.05."""
    tw = TalkWillingnessState()
    assert tw._turn_charge(0, elapsed=0.0) == -0.05
    assert tw._turn_charge(0, elapsed=30.0) == -0.05
    assert tw._turn_charge(0, elapsed=59.999) == -0.05


def test_zero_turns_after_60s_is_neutral() -> None:
    """0 turns + elapsed ≥ 60s → 0."""
    tw = TalkWillingnessState()
    assert tw._turn_charge(0, elapsed=60.0) == 0.0
    assert tw._turn_charge(0, elapsed=120.0) == 0.0


def test_one_or_two_turns_is_neutral() -> None:
    """1-2 turns → 0 (below the high-density threshold)."""
    tw = TalkWillingnessState()
    assert tw._turn_charge(1, elapsed=300.0) == 0.0
    assert tw._turn_charge(2, elapsed=300.0) == 0.0


# ----------------------------------------------------------------------
# Emotion factor — direct helper tests
# ----------------------------------------------------------------------


def test_low_valence_penalizes() -> None:
    """valence < 0.35 → -0.08 penalty regardless of arousal/curiosity."""
    tw = TalkWillingnessState()
    assert tw._emotion_charge(_FakeGroupView(valence=0.2)) == -0.08
    assert tw._emotion_charge(_FakeGroupView(valence=0.34)) == -0.08
    # 0.35 itself is NOT a red line — boundary inclusive.
    assert tw._emotion_charge(_FakeGroupView(valence=0.35)) != -0.08


def test_neutral_emotion_is_zero_charge() -> None:
    """valence=arousal=curiosity=0.5 → 0 charge."""
    tw = TalkWillingnessState()
    assert tw._emotion_charge(_neutral_view()) == 0.0


def test_high_arousal_curiosity_charges() -> None:
    """arousal=0.7, curiosity=0.7 → 0.04 charge."""
    tw = TalkWillingnessState()
    assert tw._emotion_charge(_happy_view()) == pytest.approx(0.04)


def test_emotion_charge_does_not_touch_relation() -> None:
    """The emotion factor reads only group-layer attributes."""

    class ViewWithRelation(_FakeGroupView):
        trust = 1.0  # would explode affection if accidentally read
        affection = 1.0

    tw = TalkWillingnessState()
    assert tw._emotion_charge(ViewWithRelation(0.6, 0.7, 0.7)) == \
        tw._emotion_charge(_FakeGroupView(0.6, 0.7, 0.7))


# ----------------------------------------------------------------------
# Threshold zones — need many ticks to reach the trigger zone
# ----------------------------------------------------------------------


def test_trigger_zone_fires_with_correct_intensity() -> None:
    """W in the trigger zone (LOW, HIGH] fires with intensity in
    ``[INTENSITY_MIN, INTENSITY_MAX]``. Seed W directly to avoid
    driving it from 0 (which takes many ticks of natural accumulation).

    Use ``last_tick_ts = now - 100`` so elapsed=100s — that way
    time_charge is non-zero but the "awkward" turn charge (elapsed<60)
    does NOT fire, and the math is easy to verify.
    """
    tw = TalkWillingnessState()
    _seed_w(tw, "g", 0.7)
    tw._states["g"].last_tick_ts = 0.0  # now=100, elapsed=100
    _, should_apply, intensity = tw.tick(
        "g", now=100.0, user_message_arrived=False,
        group_view=_neutral_view(), user_turns_in_5min=0,
    )
    assert should_apply
    # W_pre = 0.7 * 0.92 + 0.0175 (time) + 0 (emotion) + 0 (turn) = 0.6615
    # ratio = (0.6615 - 0.55) / 0.30 = 0.372
    # intensity = 0.05 + 0.372 * 0.20 ≈ 0.124
    assert 0.10 < intensity < 0.16

    # Lower in zone → lower intensity (linear mapping).
    # Seed slightly above LOW so post-decay still triggers.
    tw2 = TalkWillingnessState()
    _seed_w(tw2, "g", 0.60)
    tw2._states["g"].last_tick_ts = 0.0
    _, sa_low, intensity_low = tw2.tick(
        "g", now=100.0, user_message_arrived=False,
        group_view=_neutral_view(), user_turns_in_5min=0,
    )
    assert sa_low
    assert intensity_low < intensity


def test_reversal_zone_does_not_apply() -> None:
    """W > HIGH must NOT fire — reversal mode actively pulls W down."""
    tw = TalkWillingnessState()
    _seed_w(tw, "g", 1.0)  # well into reversal zone
    tw._states["g"].last_tick_ts = 0.0  # elapsed=100 → time_charge on
    W, should_apply, _ = tw.tick(
        "g", now=100.0, user_message_arrived=False,
        group_view=_neutral_view(), user_turns_in_5min=0,
    )
    assert not should_apply
    # W_pre = 1.0 * 0.92 + 0.0175 (time) = 0.9375
    # W > 0.85 (HIGH) → reversal: W = 0.9375 * 0.65 - 0.05 = 0.559
    assert W == pytest.approx(0.559, abs=0.01)
    # And W is strictly less than the seeded value.
    assert W < 1.0


def test_reversal_zone_pulls_W_down() -> None:
    """In reversal zone, W strictly decreases between consecutive ticks."""
    tw = TalkWillingnessState()
    _seed_w(tw, "g", 1.0)
    W1, sa1, _ = tw.tick(
        "g", now=100.0, user_message_arrived=False,
        group_view=_happy_view(), user_turns_in_5min=0,
    )
    assert not sa1
    W2, sa2, _ = tw.tick(
        "g", now=101.0, user_message_arrived=False,
        group_view=_happy_view(), user_turns_in_5min=0,
    )
    # W2 should be ≤ W1 (either still in reversal pulling down, or
    # already pulled below HIGH and accumulating again).
    assert W2 < W1 or (sa2 and W2 < W1)


# ----------------------------------------------------------------------
# Refractory period
# ----------------------------------------------------------------------


def test_refractory_suppresses_W_after_apply() -> None:
    """After a trigger, the next tick within refractory must not fire.
    Seed W high, fire once, then tick within refractory.
    """
    tw = TalkWillingnessState()
    _seed_w(tw, "g", 0.7)
    _, sa1, _ = tw.tick(
        "g", now=100.0, user_message_arrived=False,
        group_view=_neutral_view(), user_turns_in_5min=0,
    )
    assert sa1  # fired
    fire_ts = 100.0

    # 1s later — refractory is 30s, so W should be × 0.30 suppressed
    # and not fire even if seeded high.
    _seed_w(tw, "g", 0.7)  # re-seed (the apply reset W)
    _, should_apply, _ = tw.tick(
        "g", now=fire_ts + 1.0, user_message_arrived=False,
        group_view=_neutral_view(), user_turns_in_5min=0,
    )
    assert not should_apply


def test_refractory_can_be_disabled() -> None:
    """Setting ``self_reply_refractory_seconds=0`` disables suppression."""
    cfg = {"self_reply_refractory_seconds": 0.0}
    tw = TalkWillingnessState(config_getter=lambda k, d: cfg.get(k, d))
    _seed_w(tw, "g", 0.7)
    _, sa1, _ = tw.tick(
        "g", now=100.0, user_message_arrived=False,
        group_view=_neutral_view(), user_turns_in_5min=0,
    )
    assert sa1
    # 1s later: re-seed and tick — must fire (refractory disabled).
    _seed_w(tw, "g", 0.7)
    _, sa2, _ = tw.tick(
        "g", now=101.0, user_message_arrived=False,
        group_view=_neutral_view(), user_turns_in_5min=0,
    )
    assert sa2


# ----------------------------------------------------------------------
# Consecutive cap
# ----------------------------------------------------------------------


def test_consecutive_cap_forces_fallback() -> None:
    """After MAX_CONSECUTIVE fires, the cap forces a fall-back."""
    cfg = {"self_reply_refractory_seconds": 0.0, "self_reply_max_consecutive": 3}
    tw = TalkWillingnessState(config_getter=lambda k, d: cfg.get(k, d))

    # Fire MAX_CONSECUTIVE times by repeatedly seeding W just above LOW.
    fire_count = 0
    for t in range(0, 100, 1):
        # Re-seed if W dropped below trigger zone (after reset).
        state = tw._states.get("g")
        if state is None or state.W < 0.56:
            _seed_w(tw, "g", 0.7)
        _, should_apply, _ = tw.tick(
            "g", now=float(t), user_message_arrived=False,
            group_view=_neutral_view(), user_turns_in_5min=0,
        )
        if should_apply:
            fire_count += 1
        if fire_count >= 3:
            break
    assert fire_count >= 3

    # Cap is now hit. Even with W re-seeded high, no more fires.
    for t in range(100, 200, 1):
        _seed_w(tw, "g", 0.7)
        _, should_apply, _ = tw.tick(
            "g", now=float(t), user_message_arrived=False,
            group_view=_neutral_view(), user_turns_in_5min=0,
        )
        assert not should_apply, f"unexpected fire at t={t}"


def test_user_message_resets_consecutive_counter() -> None:
    """A user message must reset consecutive_apply so the cap lifts."""
    cfg = {"self_reply_refractory_seconds": 0.0, "self_reply_max_consecutive": 2}
    tw = TalkWillingnessState(config_getter=lambda k, d: cfg.get(k, d))

    # Get consecutive_apply up to 2.
    fire_count = 0
    for t in range(0, 50, 1):
        state = tw._states.get("g")
        if state is None or state.W < 0.56:
            _seed_w(tw, "g", 0.7)
        _, should_apply, _ = tw.tick(
            "g", now=float(t), user_message_arrived=False,
            group_view=_neutral_view(), user_turns_in_5min=0,
        )
        if should_apply:
            fire_count += 1
        if fire_count >= 2:
            break
    assert tw._states["g"].consecutive_apply >= 2

    # User message arrives — consecutive counter resets.
    tw.tick("g", now=100.0, user_message_arrived=True,
            group_view=_neutral_view(), user_turns_in_5min=1)
    assert tw._states["g"].consecutive_apply == 0


# ----------------------------------------------------------------------
# Scope cleanup
# ----------------------------------------------------------------------


def test_on_scope_deleted_drops_state() -> None:
    """``on_scope_deleted`` removes the per-scope state entry."""
    tw = TalkWillingnessState()
    tw.tick("g-1", now=100.0, user_message_arrived=False,
            group_view=_neutral_view(), user_turns_in_5min=0)
    tw.tick("g-2", now=100.0, user_message_arrived=False,
            group_view=_neutral_view(), user_turns_in_5min=0)
    assert len(tw) == 2
    tw.on_scope_deleted("g-1")
    assert len(tw) == 1
    assert "g-1" not in tw._states
    assert "g-2" in tw._states


def test_on_scope_deleted_handles_unknown_scope() -> None:
    """Deleting a scope that was never tracked must be a no-op."""
    tw = TalkWillingnessState()
    tw.on_scope_deleted("never-seen")


def test_reset_alias_for_on_scope_deleted() -> None:
    """``reset(scope)`` is API-symmetric with ``reset_scope``."""
    tw = TalkWillingnessState()
    tw.tick("g", now=0.0, user_message_arrived=False,
            group_view=_neutral_view(), user_turns_in_5min=0)
    tw.reset("g")
    assert "g" not in tw._states


# ----------------------------------------------------------------------
# Edge cases
# ----------------------------------------------------------------------


def test_inverted_thresholds_fall_back_to_defaults() -> None:
    """If config sets LOW >= HIGH (invalid), tick falls back to defaults
    (0.55 / 0.85). Seed W in the default trigger zone to verify.
    """
    cfg = {"self_reply_threshold_low": 0.9, "self_reply_threshold_high": 0.5}
    tw = TalkWillingnessState(config_getter=lambda k, d: cfg.get(k, d))
    _seed_w(tw, "g", 0.7)  # in default trigger zone (LOW=0.55, HIGH=0.85)
    _, should_apply, intensity = tw.tick(
        "g", now=100.0, user_message_arrived=False,
        group_view=_neutral_view(), user_turns_in_5min=0,
    )
    assert should_apply
    assert 0.05 <= intensity <= 0.25


def test_HARD_CAP_clamps_W() -> None:
    """W must never exceed ``HARD_CAP`` (1.20)."""
    cfg = {"self_reply_decay": 1.0}
    tw = TalkWillingnessState(config_getter=lambda k, d: cfg.get(k, d))
    max_W = 0.0
    for t in range(0, 10000, 1):
        W, _, _ = tw.tick("g", now=float(t), user_message_arrived=False,
                          group_view=_happy_view(), user_turns_in_5min=0)
        if W > max_W:
            max_W = W
    assert max_W <= 1.20 + 1e-9


def test_first_tick_initializes_timestamp() -> None:
    """On the first tick for a scope, ``last_tick_ts`` is initialized to ``now``."""
    tw = TalkWillingnessState()
    assert "g" not in tw._states
    tw.tick("g", now=1234.5, user_message_arrived=False,
            group_view=_neutral_view(), user_turns_in_5min=0)
    assert tw._states["g"].last_tick_ts == 1234.5


def test_independent_scopes_have_independent_state() -> None:
    """Two scopes tracked by the same TalkWillingnessState must not
    share W / consecutive counters.
    """
    tw = TalkWillingnessState()
    # Seed g-a high, g-b at 0; align timestamps so elapsed=0 on first tick.
    _seed_w(tw, "g-a", 0.7)
    _seed_w(tw, "g-b", 0.0)
    tw._states["g-a"].last_tick_ts = 100.0
    tw._states["g-b"].last_tick_ts = 100.0

    _, sa_a, _ = tw.tick("g-a", now=100.0, user_message_arrived=False,
                         group_view=_neutral_view(), user_turns_in_5min=0)
    W_b, sa_b, _ = tw.tick("g-b", now=100.0, user_message_arrived=False,
                           group_view=_neutral_view(), user_turns_in_5min=0)

    assert sa_a is True
    assert sa_b is False
    assert W_b == 0.0
    assert tw._states["g-a"] is not tw._states["g-b"]


def test_config_getter_used_for_thresholds() -> None:
    """Custom config_getter values must take effect on threshold decisions."""
    cfg = {"self_reply_threshold_low": 0.05, "self_reply_threshold_high": 0.15,
           "self_reply_refractory_seconds": 0.0}
    tw = TalkWillingnessState(config_getter=lambda k, d: cfg.get(k, d))
    # Seed W at 0.10 — between custom LOW=0.05 and HIGH=0.15.
    _seed_w(tw, "g", 0.10)
    _, should_apply, _ = tw.tick(
        "g", now=100.0, user_message_arrived=False,
        group_view=_neutral_view(), user_turns_in_5min=0,
    )
    assert should_apply


def test_config_getter_returns_default_when_key_missing() -> None:
    """When config_getter is invoked with a key it doesn't recognize,
    the supplied default must be used.
    """
    cfg: dict[str, float] = {}
    tw = TalkWillingnessState(config_getter=lambda k, d: cfg.get(k, d))

    for t in range(0, 500, 1):
        _, _, _ = tw.tick("g", now=float(t), user_message_arrived=False,
                          group_view=_neutral_view(), user_turns_in_5min=0)

    state = tw._states["g"]
    assert state.W >= 0.0
    assert state.W < 0.1


def test_disabled_signals_in_apply_self_reply_skips() -> None:
    """Smoke check: TalkWillingnessState can return should_apply=True.
    The disabled_signals gating is handled by the plugin layer (see
    apply_self_reply_signal in main.py) — this test only verifies
    that the decision propagation contract holds.
    """
    tw = TalkWillingnessState()
    _seed_w(tw, "g", 0.7)
    _, should_apply, _ = tw.tick(
        "g", now=100.0, user_message_arrived=False,
        group_view=_neutral_view(), user_turns_in_5min=0,
    )
    assert should_apply


# ----------------------------------------------------------------------
# Optional modulation factors (v0.10.0+)
# ----------------------------------------------------------------------


def test_energy_factor_default_is_neutral() -> None:
    """Without an energy_getter, the factor is 1.0 (neutral)."""
    tw = TalkWillingnessState()
    assert tw._energy_factor() == 1.0


def test_energy_factor_zero_halves_accumulation() -> None:
    """energy=0.0 → factor 0.5. Energy=1.0 → factor 1.0."""
    tw = TalkWillingnessState(energy_getter=lambda: 0.0)
    assert tw._energy_factor() == pytest.approx(0.5)
    tw._energy = lambda: 1.0
    assert tw._energy_factor() == pytest.approx(1.0)
    tw._energy = lambda: 0.5
    assert tw._energy_factor() == pytest.approx(0.75)


def test_energy_factor_clamps_to_unit_range() -> None:
    """Energy values outside [0, 1] must be clamped, not propagated."""
    tw = TalkWillingnessState(energy_getter=lambda: 1.5)
    assert tw._energy_factor() == pytest.approx(1.0)
    tw._energy = lambda: -0.3
    assert tw._energy_factor() == pytest.approx(0.5)


def test_energy_factor_handles_nonfinite_and_exceptions() -> None:
    """NaN / inf / exceptions in energy_getter fall back to neutral."""
    tw = TalkWillingnessState(energy_getter=lambda: float("nan"))
    assert tw._energy_factor() == 1.0
    tw._energy = lambda: float("inf")
    assert tw._energy_factor() == 1.0
    def boom(): raise RuntimeError("energy system down")
    tw._energy = boom
    assert tw._energy_factor() == 1.0


def test_crowd_factor_default_is_neutral() -> None:
    """No active_users info → factor 1.0 (neutral)."""
    tw = TalkWillingnessState()
    assert tw._crowd_factor(_neutral_view()) == 1.0


def test_crowd_factor_scales_with_active_users() -> None:
    """1/sqrt(N) where N = active users count, clamped to [0.1, 1.0]."""
    tw = TalkWillingnessState()

    class ViewWithUsers(_FakeGroupView):
        def __init__(self, n: int):
            super().__init__()
            self.active_users = {f"u{i}": 1 for i in range(n)}

    assert tw._crowd_factor(ViewWithUsers(1)) == 1.0  # 1 person: no mod
    assert tw._crowd_factor(ViewWithUsers(4)) == pytest.approx(0.5)
    assert tw._crowd_factor(ViewWithUsers(25)) == pytest.approx(0.2)
    assert tw._crowd_factor(ViewWithUsers(100)) == pytest.approx(0.1)


def test_crowd_factor_floor_at_01() -> None:
    """For huge groups, factor clamps at 0.1 (never zero)."""
    tw = TalkWillingnessState()

    class ViewWithUsers(_FakeGroupView):
        def __init__(self, n: int):
            super().__init__()
            self.active_users = {f"u{i}": 1 for i in range(n)}

    assert tw._crowd_factor(ViewWithUsers(10000)) == 0.1


def test_crowd_factor_tolerates_dict_count_and_attribute_missing() -> None:
    """active_users can be a dict, an int, or missing."""
    tw = TalkWillingnessState()

    class V1(_FakeGroupView):
        active_users = {"a": 1, "b": 1, "c": 1}
    class V2(_FakeGroupView):
        active_users = 9
    class V3(_FakeGroupView):
        pass  # no attribute

    assert tw._crowd_factor(V1()) == pytest.approx(1.0 / 3 ** 0.5, abs=0.01)
    assert tw._crowd_factor(V2()) == pytest.approx(1.0 / 9 ** 0.5, abs=0.01)
    assert tw._crowd_factor(V3()) == 1.0


def test_tick_applies_energy_and_crowd_factors() -> None:
    """End-to-end: low energy + large crowd should significantly
    suppress W accumulation compared to neutral.
    """
    # Neutral case: energy=1.0, 1-person "group".
    tw_neutral = TalkWillingnessState(energy_getter=lambda: 1.0)
    _seed_w(tw_neutral, "g", 0.0)
    tw_neutral._states["g"].last_tick_ts = 0.0
    W_neutral, _, _ = tw_neutral.tick(
        "g", now=100.0, user_message_arrived=False,
        group_view=_neutral_view(), user_turns_in_5min=0,
    )

    # Suppressed case: energy=0.0, 25-person group (factor=0.2).
    tw_suppressed = TalkWillingnessState(energy_getter=lambda: 0.0)

    class View25(_FakeGroupView):
        active_users = {f"u{i}": 1 for i in range(25)}
    _seed_w(tw_suppressed, "g", 0.0)
    tw_suppressed._states["g"].last_tick_ts = 0.0
    W_suppressed, _, _ = tw_suppressed.tick(
        "g", now=100.0, user_message_arrived=False,
        group_view=View25(), user_turns_in_5min=0,
    )

    # Suppressed W must be strictly less than neutral W. With factor
    # 0.5 (energy) × 0.2 (crowd) = 0.1 vs neutral 1.0, the difference
    # is roughly 10× in net charge, which dominates over decay.
    assert W_suppressed < W_neutral * 0.5