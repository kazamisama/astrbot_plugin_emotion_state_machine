from __future__ import annotations

import time

from emotion_engine import (
    EmotionEvent,
    EmotionStateMachine,
    build_prompt_block,
    derive_group_label,
    derive_label,
    derive_relation_label,
    infer_signals,
)


def test_infer_signals_from_text() -> None:
    signals = infer_signals("谢谢，插件 bug 修好了？", mentioned=True)
    names = [name for name, _reason in signals]
    assert "mention" in names
    assert "thanks" in names
    assert "technical" in names
    assert "question" in names


def test_apply_signal_changes_group_snapshot_compatibly() -> None:
    machine = EmotionStateMachine(decay_half_life_seconds=900)
    before = machine.get("group-1")
    before_valence = before.valence

    after = machine.apply("group-1", EmotionEvent(signal="praise", intensity=1.0, timestamp=1000.0))

    assert after.valence > before_valence
    assert after.last_signal == "praise"
    assert after.transitions == 1


def test_interaction_splits_group_and_user_relation() -> None:
    machine = EmotionStateMachine(decay_half_life_seconds=900)

    view = machine.apply_interaction(
        "group-1",
        "user-a",
        EmotionEvent(signal="thanks", intensity=1.0, timestamp=1000.0),
    )

    assert view.group.valence > 0.56
    assert view.group.stress < 0.18
    assert view.relation is not None
    assert view.relation.trust > 0.55
    assert view.relation.affection > 0.46
    assert view.relation.irritation < 0.16


def test_group_dilution_uses_active_user_count() -> None:
    single = EmotionStateMachine(decay_half_life_seconds=900)
    single_view = single.apply_interaction(
        "group-1",
        "user-a",
        EmotionEvent(signal="insult", intensity=1.0, timestamp=1000.0),
    )
    single_delta = single_view.group.stress - 0.18

    crowded = EmotionStateMachine(decay_half_life_seconds=900)
    for index in range(4):
        crowded.apply_interaction(
            "group-1",
            f"user-{index}",
            EmotionEvent(signal="friendly", intensity=0.0, timestamp=999.0 + index),
        )
    crowded_view = crowded.apply_interaction(
        "group-1",
        "user-a",
        EmotionEvent(signal="insult", intensity=1.0, timestamp=1005.0),
    )
    crowded_delta = crowded_view.group.stress - 0.18

    assert len(crowded_view.group.active_users) == 5
    assert crowded_delta < single_delta


def test_user_relation_is_private_per_user() -> None:
    machine = EmotionStateMachine(decay_half_life_seconds=900)
    machine.apply_interaction(
        "group-1",
        "user-a",
        EmotionEvent(signal="insult", intensity=1.0, timestamp=1000.0),
    )

    user_a = machine.get_relation("group-1", "user-a", apply_decay=False)
    user_b = machine.get_relation("group-1", "user-b", apply_decay=False)

    assert user_a.irritation > user_b.irritation
    assert user_a.trust < user_b.trust
    assert user_b.transitions == 0


def test_decay_moves_towards_baseline() -> None:
    machine = EmotionStateMachine(decay_half_life_seconds=10)
    snapshot = machine.apply("group-1", EmotionEvent(signal="pressure", intensity=2.0, timestamp=1000.0))
    stressed = snapshot.stress

    decayed = machine.get("group-1", now=1010.0, apply_decay=True)

    assert decayed.stress < stressed
    assert decayed.stress > 0.18


def test_serialization_roundtrip() -> None:
    machine = EmotionStateMachine(decay_half_life_seconds=123)
    machine.apply_interaction("group-1", "user-a", EmotionEvent(signal="technical", timestamp=1000.0))

    restored = EmotionStateMachine.from_dict(machine.to_dict())
    group = restored.get("group-1", apply_decay=False)
    relation = restored.get_relation("group-1", "user-a", apply_decay=False)

    assert restored.decay_half_life_seconds == 123
    assert group.last_signal == "technical"
    assert group.transitions == 1
    assert relation.last_signal == "technical"
    assert relation.transitions == 1


def test_prompt_block_contains_group_and_user_layers() -> None:
    machine = EmotionStateMachine()
    view = machine.apply_interaction("group-1", "user-a", EmotionEvent(signal="technical", intensity=2.0))
    block = build_prompt_block("group-1", view)

    assert "## Bot Emotion State" in block
    assert "group:" in block
    assert "towards_current_user:" in block
    assert "style_hint" in block
    assert view.label in block


def test_label_derivation_can_be_annoyed() -> None:
    machine = EmotionStateMachine()
    snapshot = machine.get("group-1", apply_decay=False)
    snapshot.valence = 0.2
    snapshot.stress = 0.8
    snapshot.arousal = 0.7

    assert derive_label(snapshot) == "annoyed"


def test_relation_ttl_prunes_stale_users() -> None:
    """After TTL expires, get_relation returns a fresh baseline snapshot.

    Stale entries are dropped entirely (not reset to baseline), so a
    returning user starts fresh — the returned snapshot must reflect
    baseline values, not the previous praise-driven trust/affection.
    """
    machine = EmotionStateMachine(decay_half_life_seconds=900, relation_ttl_seconds=10)
    machine.apply_interaction(
        "group-1",
        "user-a",
        EmotionEvent(signal="praise", intensity=1.0, timestamp=1000.0),
    )
    # Sanity check: praise moved trust above baseline.
    stale_snap = machine.get_relation("group-1", "user-a", apply_decay=False)
    assert stale_snap.trust > 0.55

    # Read far enough in the future that TTL expires — entry pruned,
    # get_relation auto-creates a fresh baseline.
    fresh_snap = machine.get_relation("group-1", "user-a", now=2000.0, apply_decay=True)
    assert fresh_snap.trust == 0.55
    assert fresh_snap.transitions == 0


def test_relation_ttl_keeps_fresh_users() -> None:
    """Relations within the TTL window are preserved across reads."""
    machine = EmotionStateMachine(decay_half_life_seconds=900, relation_ttl_seconds=10)
    machine.apply_interaction(
        "group-1",
        "user-a",
        EmotionEvent(signal="praise", intensity=1.0, timestamp=1000.0),
    )

    # 5 seconds later — still within the 10s window.
    snap = machine.get_relation("group-1", "user-a", now=1005.0, apply_decay=True)

    assert snap is not None
    assert "user-a" in machine.relations.get("group-1", {})


def test_relation_ttl_applies_on_combined_read() -> None:
    """get_combined also prunes stale relations, even with empty user_id."""
    machine = EmotionStateMachine(decay_half_life_seconds=900, relation_ttl_seconds=10)
    machine.apply_interaction(
        "group-1",
        "user-a",
        EmotionEvent(signal="praise", intensity=1.0, timestamp=1000.0),
    )
    # Combined read with no user_id and a far-future timestamp.
    machine.get_combined("group-1", user_id=None, now=2000.0, apply_decay=True)

    assert "user-a" not in machine.relations.get("group-1", {})


def test_relation_ttl_persists_in_to_dict() -> None:
    """relation_ttl_seconds is round-tripped through to_dict / from_dict."""
    machine = EmotionStateMachine(decay_half_life_seconds=900, relation_ttl_seconds=12345.0)
    restored = EmotionStateMachine.from_dict(machine.to_dict())

    assert restored.relation_ttl_seconds == 12345.0


# ----------------------------------------------------------------------
# infer_signals question-detection heuristic
# ----------------------------------------------------------------------


def _names(signals: list[tuple[str, str]]) -> list[str]:
    return [name for name, _reason in signals]


def test_infer_signals_question_at_end_still_fires() -> None:
    """Trailing '?' / '？' must still trigger the question signal."""
    signals = infer_signals("修好了？")
    assert "question" in _names(signals)


def test_infer_signals_question_english_trailing() -> None:
    signals = infer_signals("is this working?")
    assert "question" in _names(signals)


def test_infer_signals_bare_question_mark_in_middle_does_not_fire() -> None:
    """Bare '?' in the middle of a sentence is too noisy — must not
    trigger the question signal on its own."""
    signals = infer_signals("OK? 然后我们继续")
    assert "question" not in _names(signals)


def test_infer_signals_question_word_fires_without_mark() -> None:
    """Chinese question words trigger the question signal even without '?'."""
    for word in ("怎么", "什么", "为什么", "哪", "谁", "如何", "多少"):
        signals = infer_signals(f"你{word}做这件事的")
        assert "question" in _names(signals), f"Expected question for word: {word}"


def test_infer_signals_modal_question_phrase_fires() -> None:
    """Modal question phrases like 行不行 / 能不能 / 是不是 fire the signal."""
    for phrase in (
        "行不行", "能不能", "会不会", "可不可以", "要不要",
        "好不好", "对不对", "有没有", "是不是",
    ):
        signals = infer_signals(f"这个{phrase}")
        assert "question" in _names(signals), f"Expected question for phrase: {phrase}"


def test_infer_signals_ma_particle_fires_near_end() -> None:
    """The '吗' particle near the end fires the question signal."""
    for text in ("行吗", "行吗？", "行吗。", "行吗！"):
        signals = infer_signals(text)
        assert "question" in _names(signals), f"Expected question for: {text}"


def test_infer_signals_ma_substring_does_not_fire() -> None:
    """'吗' as a substring (not as a terminal particle) must not fire —
    e.g. '马马虎虎' contains '吗' but is not a question."""
    signals = infer_signals("马马虎虎的一天")
    assert "question" not in _names(signals)


def test_infer_signals_plain_statement_has_no_question() -> None:
    """Plain statements without indicators stay question-free."""
    for text in (
        "今天天气不错",
        "好的收到",
        "thanks a lot",
        "all good",
        "我们去吃饭吧",  # imperative, no indicator
    ):
        signals = infer_signals(text)
        assert "question" not in _names(signals), f"Unexpected question for: {text}"


def test_infer_signals_existing_test_still_passes() -> None:
    """Regression guard for the original test_infer_signals_from_text case."""
    signals = infer_signals("谢谢，插件 bug 修好了？", mentioned=True)
    names = _names(signals)
    assert "mention" in names
    assert "thanks" in names
    assert "technical" in names
    assert "question" in names


# ----------------------------------------------------------------------
# Group / relation label thresholds (refactor regression)
# ----------------------------------------------------------------------


def test_group_label_thresholds_dict_is_exposed() -> None:
    """The threshold dict must be importable and have the canonical labels."""
    from emotion_engine import GROUP_LABEL_THRESHOLDS
    assert set(GROUP_LABEL_THRESHOLDS.keys()) == {
        "annoyed", "hurt", "tense", "excited", "happy", "curious", "quiet",
    }


def test_relation_label_thresholds_dict_is_exposed() -> None:
    from emotion_engine import RELATION_LABEL_THRESHOLDS
    assert set(RELATION_LABEL_THRESHOLDS.keys()) == {
        "guarded", "attached", "trusted", "irritated", "unfamiliar",
    }


def test_group_label_annoyed_threshold_regression() -> None:
    """Refactor must not change the boundary behavior of label derivation."""
    from emotion_engine import GroupEmotionSnapshot
    snap = GroupEmotionSnapshot(valence=0.40, arousal=0.40, stress=0.70, curiosity=0.40)
    # stress >= 0.68 and valence <= 0.42 → "annoyed"
    assert derive_group_label(snap) == "annoyed"


def test_group_label_hurt_threshold_regression() -> None:
    from emotion_engine import GroupEmotionSnapshot
    snap = GroupEmotionSnapshot(valence=0.30, arousal=0.40, stress=0.50, curiosity=0.40)
    # valence <= 0.34 and stress >= 0.42 → "hurt"
    assert derive_group_label(snap) == "hurt"


def test_group_label_excited_threshold_regression() -> None:
    from emotion_engine import GroupEmotionSnapshot
    snap = GroupEmotionSnapshot(valence=0.80, arousal=0.70, stress=0.10, curiosity=0.40)
    # valence >= 0.72 and arousal >= 0.62 → "excited"
    assert derive_group_label(snap) == "excited"


def test_group_label_quiet_fallback_regression() -> None:
    from emotion_engine import GroupEmotionSnapshot
    snap = GroupEmotionSnapshot(valence=0.50, arousal=0.10, stress=0.10, curiosity=0.30)
    # arousal <= 0.22 and stress <= 0.28 → "quiet"
    assert derive_group_label(snap) == "quiet"


def test_group_label_calm_fallback_regression() -> None:
    from emotion_engine import GroupEmotionSnapshot
    snap = GroupEmotionSnapshot(valence=0.55, arousal=0.40, stress=0.30, curiosity=0.40)
    # No threshold matches → "calm" (default)
    assert derive_group_label(snap) == "calm"


def test_relation_label_guarded_regression() -> None:
    from emotion_engine import UserRelationSnapshot
    snap = UserRelationSnapshot(irritation=0.75, trust=0.35)
    # irritation >= 0.68 and trust <= 0.42 → "guarded"
    assert derive_relation_label(snap) == "guarded"


def test_relation_label_attached_regression() -> None:
    from emotion_engine import UserRelationSnapshot
    snap = UserRelationSnapshot(affection=0.70, trust=0.70, irritation=0.20)
    # affection >= 0.66 and trust >= 0.62 and irritation <= 0.35 → "attached"
    assert derive_relation_label(snap) == "attached"


def test_relation_label_unfamiliar_regression() -> None:
    from emotion_engine import UserRelationSnapshot
    snap = UserRelationSnapshot(familiarity=0.10)
    # familiarity <= 0.18 → "unfamiliar"
    assert derive_relation_label(snap) == "unfamiliar"


def test_eval_label_condition_min_max_semantics() -> None:
    """Direct test of the helper that the new derive_* functions rely on."""
    from emotion_engine import _eval_label_condition
    snap = type("S", (), {"stress": 0.5, "valence": 0.3})()
    # _min: value >= threshold
    assert _eval_label_condition(snap, {"stress_min": 0.4}) is True
    assert _eval_label_condition(snap, {"stress_min": 0.6}) is False
    # _max: value <= threshold
    assert _eval_label_condition(snap, {"valence_max": 0.4}) is True
    assert _eval_label_condition(snap, {"valence_max": 0.2}) is False
    # Combined: all must hold
    assert _eval_label_condition(
        snap, {"stress_min": 0.4, "valence_max": 0.4}
    ) is True
    assert _eval_label_condition(
        snap, {"stress_min": 0.4, "valence_max": 0.2}
    ) is False


# ----------------------------------------------------------------------
# Cold-scope / cold-relation pruning
# ----------------------------------------------------------------------


def test_prune_groups_removes_cold_scopes() -> None:
    machine = EmotionStateMachine(decay_half_life_seconds=900, group_ttl_seconds=10)
    machine.apply_interaction(
        "group-cold", "user-a",
        EmotionEvent(signal="praise", intensity=1.0, timestamp=1000.0),
    )
    machine.apply_interaction(
        "group-fresh", "user-b",
        EmotionEvent(signal="praise", intensity=1.0, timestamp=1000.0),
    )

    # Read far in the future — both groups exceed the 10s TTL by default.
    # Manually bump group-cold to a stale updated_at to make the test
    # deterministic without fighting the real-time clock.
    machine.groups["group-cold"].updated_at = 1000.0
    machine.groups["group-fresh"].updated_at = 1995.0  # within TTL at now=2000

    pruned = machine._prune_groups(now=2000.0)
    assert pruned == 1
    assert "group-cold" not in machine.groups
    assert "group-fresh" in machine.groups


def test_prune_groups_returns_zero_when_nothing_stale() -> None:
    machine = EmotionStateMachine(decay_half_life_seconds=900, group_ttl_seconds=3600)
    machine.apply_interaction(
        "g", "u", EmotionEvent(signal="praise", timestamp=time.time()),
    )
    assert machine._prune_groups() == 0


def test_prune_groups_also_drops_unreachable_relations() -> None:
    """If a group is pruned, its relations bucket is unreachable from
    the public API — drop it too to free memory."""
    machine = EmotionStateMachine(
        decay_half_life_seconds=900,
        group_ttl_seconds=10,
        relation_ttl_seconds=10,
    )
    machine.apply_interaction(
        "g", "user-x",
        EmotionEvent(signal="praise", intensity=1.0, timestamp=1000.0),
    )
    assert "g" in machine.relations
    assert "user-x" in machine.relations["g"]

    machine.groups["g"].updated_at = 500.0  # stale
    machine._prune_groups(now=2000.0)

    assert "g" not in machine.relations


def test_prune_cold_state_returns_counts() -> None:
    machine = EmotionStateMachine(
        decay_half_life_seconds=900,
        group_ttl_seconds=10,
        relation_ttl_seconds=10,
    )
    # 2 groups, 1 of them cold; 2 relations, 1 of them cold
    machine.apply_interaction(
        "g-cold", "u-stale",
        EmotionEvent(signal="praise", intensity=1.0, timestamp=1000.0),
    )
    machine.apply_interaction(
        "g-fresh", "u-fresh",
        EmotionEvent(signal="praise", intensity=1.0, timestamp=1995.0),
    )
    machine.groups["g-cold"].updated_at = 1000.0
    machine.relations["g-cold"]["u-stale"].updated_at = 1000.0

    result = machine.prune_cold_state(now=2000.0)
    assert result == {"groups_pruned": 1, "relations_pruned": 1}
    assert "g-cold" not in machine.groups
    assert "g-fresh" in machine.groups
    assert "u-stale" not in machine.relations.get("g-cold", {})


def test_from_dict_does_not_auto_prune() -> None:
    """from_dict is a pure data loader — it must NOT auto-prune based
    on the real wall clock, because tests use synthetic timestamps
    in the past. Pruning is the caller's responsibility (the plugin's
    ``_load_state`` invokes ``_prune_groups()`` explicitly)."""
    machine = EmotionStateMachine(decay_half_life_seconds=900, group_ttl_seconds=10)
    machine.apply_interaction(
        "g-1", "u-a",
        EmotionEvent(signal="praise", intensity=1.0, timestamp=1000.0),
    )

    restored = EmotionStateMachine.from_dict(machine.to_dict())
    # Both group and relation survive the round-trip because from_dict
    # does not call _prune_groups() internally.
    assert "g-1" in restored.groups
    assert "u-a" in restored.relations.get("g-1", {})


def test_explicit_prune_after_from_dict() -> None:
    """Caller can (and should in production) invoke _prune_groups
    explicitly after from_dict to bound startup memory."""
    machine = EmotionStateMachine(decay_half_life_seconds=900, group_ttl_seconds=10)
    machine.apply_interaction(
        "g-1", "u-a",
        EmotionEvent(signal="praise", intensity=1.0, timestamp=1000.0),
    )

    restored = EmotionStateMachine.from_dict(machine.to_dict())
    # Stale by synthetic timestamp 1000.0 — prune with a far-future now.
    pruned = restored._prune_groups(now=1e10)
    assert pruned >= 1
    assert "g-1" not in restored.groups


def test_group_ttl_persists_in_to_dict() -> None:
    machine = EmotionStateMachine(group_ttl_seconds=12345.0)
    restored = EmotionStateMachine.from_dict(machine.to_dict())
    assert restored.group_ttl_seconds == 12345.0


def test_prune_groups_default_ttl_is_30_days() -> None:
    """The default group_ttl_seconds should be 30 days (2592000s)."""
    machine = EmotionStateMachine()
    assert machine.group_ttl_seconds == 2592000.0


# ----------------------------------------------------------------------
# prune_active_users in-place semantics
# ----------------------------------------------------------------------


def test_prune_active_users_mutates_in_place() -> None:
    """prune_active_users must mutate the input dict, not rebuild it.
    The return value is the SAME object so callers that still assign
    the result back don't break, but no fresh dict is allocated on the
    common case (no stale entries).
    """
    from emotion_engine import prune_active_users
    # cutoff = now - window = 300 - 100 = 200
    # alice=250 (fresh), bob=300 (fresh), carol=50 (stale)
    bucket: dict[str, float] = {"alice": 250.0, "bob": 300.0, "carol": 50.0}
    bucket_id = id(bucket)

    returned = prune_active_users(bucket, now=300.0, window_seconds=100.0)

    # Same object — no allocation.
    assert id(returned) == bucket_id
    # Stale entries dropped, fresh kept.
    assert "alice" in returned
    assert "bob" in returned
    assert "carol" not in returned


def test_prune_active_users_empty_is_fast_path() -> None:
    """An empty dict returns immediately — no iteration, no allocation."""
    from emotion_engine import prune_active_users
    bucket: dict[str, float] = {}
    result = prune_active_users(bucket, now=1000.0, window_seconds=300.0)
    assert result is bucket
    assert result == {}


def test_prune_active_users_all_fresh_keeps_everything() -> None:
    from emotion_engine import prune_active_users
    bucket = {"alice": 950.0, "bob": 990.0}
    result = prune_active_users(bucket, now=1000.0, window_seconds=300.0)
    assert result is bucket
    assert set(result.keys()) == {"alice", "bob"}


def test_prune_active_users_all_stale_clears_dict() -> None:
    from emotion_engine import prune_active_users
    bucket = {"alice": 0.0, "bob": 100.0}
    result = prune_active_users(bucket, now=2000.0, window_seconds=300.0)
    assert result is bucket
    assert result == {}


def test_prune_active_users_via_apply_interaction_uses_same_dict() -> None:
    """Integration check: the engine's hot path doesn't allocate a new
    active_users dict on every message."""
    from emotion_engine import EmotionStateMachine
    machine = EmotionStateMachine(active_window_seconds=300.0)
    machine.apply_interaction(
        "g", "u-a",
        EmotionEvent(signal="praise", intensity=1.0, timestamp=1000.0),
    )
    initial_dict = machine.groups["g"].active_users
    initial_id = id(initial_dict)

    # Fire 5 more messages — the dict object identity must not change.
    for i in range(5):
        machine.apply_interaction(
            "g", f"u-{i}",
            EmotionEvent(signal="praise", intensity=0.1, timestamp=1001.0 + i),
        )
    assert id(machine.groups["g"].active_users) == initial_id


# ----------------------------------------------------------------------
# observe_text disabled_signals filtering
# ----------------------------------------------------------------------


def test_observe_text_filters_disabled_signals() -> None:
    """Signals in the disabled set are dropped before application, so
    the state engine only sees the survivors."""
    machine = EmotionStateMachine(decay_half_life_seconds=900)
    before = machine.get_group("g", apply_decay=False)

    # "谢谢" would normally trigger "thanks" and "?" would trigger
    # "question". Disable both — no signal should land.
    machine.observe_text(
        "g", "谢谢，这个能行吗？",
        user_id="u", mentioned=True,
        timestamp=1000.0,
        disabled_signals={"thanks", "question"},
    )

    after = machine.get_group("g", apply_decay=False)
    assert after.transitions == before.transitions
    assert after.last_signal == before.last_signal


def test_observe_text_disabled_set_is_optional() -> None:
    """When disabled_signals is None or empty, all inferred signals apply."""
    machine = EmotionStateMachine(decay_half_life_seconds=900)
    machine.observe_text("g", "谢谢", user_id="u", timestamp=1000.0)
    assert machine.get_group("g", apply_decay=False).transitions == 1


def test_observe_text_disabled_set_filters_one_keeps_another() -> None:
    """Selective filtering: disable one signal, another still fires."""
    machine = EmotionStateMachine(decay_half_life_seconds=900)
    # "谢谢" triggers "thanks" only (no "?" in this message).
    machine.observe_text(
        "g", "谢谢", user_id="u", timestamp=1000.0,
        disabled_signals={"praise"},  # not relevant, shouldn't matter
    )
    # Disabled "praise" doesn't filter "thanks" → thanks still applies.
    assert machine.get_group("g", apply_decay=False).last_signal == "thanks"


# ----------------------------------------------------------------------
# build_prompt_block sentinel markers
# ----------------------------------------------------------------------


def test_build_prompt_block_wraps_with_sentinels() -> None:
    """The block must be wrapped with start/end markers so the plugin
    can detect it for in-place replacement."""
    from emotion_engine import (
        ESM_BLOCK_END,
        ESM_BLOCK_START,
        build_prompt_block,
    )
    machine = EmotionStateMachine()
    view = machine.get_combined("g-1", "u-1")
    block = build_prompt_block("g-1", view)

    assert block.startswith(ESM_BLOCK_START)
    assert block.endswith(ESM_BLOCK_END)
    assert "## Bot Emotion State" in block


def test_sentinels_are_html_comment_style() -> None:
    """Sentinels must be HTML comments so they pass through LLMs
    untouched and don't render in any model output."""
    from emotion_engine import ESM_BLOCK_END, ESM_BLOCK_START
    assert ESM_BLOCK_START.startswith("<!--")
    assert ESM_BLOCK_START.endswith("-->")
    assert ESM_BLOCK_END.startswith("<!--")
    assert ESM_BLOCK_END.endswith("-->")


def test_two_distinct_blocks_have_different_content() -> None:
    """Smoke check: each call to build_prompt_block reflects whatever
    the engine state was at the time of the call. The two blocks built
    in sequence must differ because the state advances between calls.

    Note: CombinedEmotionView holds references to the mutable snapshots,
    so we capture the *values* immediately after each apply instead of
    keeping the view object across calls.
    """
    from emotion_engine import build_prompt_block
    machine = EmotionStateMachine()
    machine.apply_interaction(
        "g", "u-a", EmotionEvent(signal="praise", intensity=1.0, timestamp=1000.0),
    )
    block_a = build_prompt_block(
        "g", machine.get_combined("g", "u-a", apply_decay=False),
    )
    machine.apply_interaction(
        "g", "u-a", EmotionEvent(signal="thanks", intensity=1.0, timestamp=1001.0),
    )
    block_b = build_prompt_block(
        "g", machine.get_combined("g", "u-a", apply_decay=False),
    )
    # After the first apply, last_signal was "praise"; after the second
    # it became "thanks". The blocks should reflect that ordering.
    assert "praise" in block_a
    assert "thanks" in block_b
    assert "thanks" not in block_a
    assert "praise" not in block_b


def test_dilution_default_matches_sqrt_curve() -> None:
    """Default exponent 0.5 reproduces the historical 1/sqrt(n) curve."""
    machine = EmotionStateMachine()
    assert machine.dilution_exponent == 0.5
    assert machine._active_user_dilution(1) == 1.0
    assert machine._active_user_dilution(4) == 0.5
    assert machine._active_user_dilution(9) == 1.0 / 3.0


def test_dilution_zero_disables_dilution() -> None:
    """Exponent 0.0 returns 1.0 regardless of active count."""
    machine = EmotionStateMachine(dilution_exponent=0.0)
    assert machine._active_user_dilution(1) == 1.0
    assert machine._active_user_dilution(100) == 1.0


def test_dilution_linear_is_more_aggressive_than_sqrt() -> None:
    """Exponent 1.0 (linear) dilutes more strongly than 0.5 (sqrt)."""
    linear = EmotionStateMachine(dilution_exponent=1.0)
    sqrt = EmotionStateMachine(dilution_exponent=0.5)
    for n in (4, 9, 25, 100):
        assert linear._active_user_dilution(n) < sqrt._active_user_dilution(n)


def test_dilution_low_exponent_is_gentler_than_sqrt() -> None:
    """Exponent 0.3 dilutes more gently than 0.5 (sqrt)."""
    gentle = EmotionStateMachine(dilution_exponent=0.3)
    sqrt = EmotionStateMachine(dilution_exponent=0.5)
    for n in (4, 9, 25, 100):
        assert gentle._active_user_dilution(n) > sqrt._active_user_dilution(n)


def test_dilution_exponent_is_clamped() -> None:
    """Exponents outside [0, 2] are clamped to the allowed range."""
    assert EmotionStateMachine(dilution_exponent=-1.0).dilution_exponent == 0.0
    assert EmotionStateMachine(dilution_exponent=5.0).dilution_exponent == 2.0


def test_dilution_exponent_persists_in_to_dict() -> None:
    """dilution_exponent is round-tripped through to_dict / from_dict."""
    machine = EmotionStateMachine(dilution_exponent=0.7)
    restored = EmotionStateMachine.from_dict(machine.to_dict())
    assert restored.dilution_exponent == 0.7


def test_dilution_exponent_affects_apply_interaction() -> None:
    """A higher dilution_exponent produces a smaller group delta for the
    same signal, confirming the config actually flows through end-to-end."""
    gentle = EmotionStateMachine(dilution_exponent=0.0)
    harsh = EmotionStateMachine(dilution_exponent=1.0)

    # 4 active users → harsh gets 1/4 multiplier, gentle gets 1.0
    for index in range(3):
        gentle.apply_interaction(
            "g-gentle", f"warmup-{index}",
            EmotionEvent(signal="friendly", intensity=0.0, timestamp=999.0 + index),
        )
        harsh.apply_interaction(
            "g-harsh", f"warmup-{index}",
            EmotionEvent(signal="friendly", intensity=0.0, timestamp=999.0 + index),
        )

    gentle_view = gentle.apply_interaction(
        "g-gentle", "user-a",
        EmotionEvent(signal="praise", intensity=1.0, timestamp=1010.0),
    )
    harsh_view = harsh.apply_interaction(
        "g-harsh", "user-a",
        EmotionEvent(signal="praise", intensity=1.0, timestamp=1010.0),
    )

    assert gentle_view.group.valence > harsh_view.group.valence
