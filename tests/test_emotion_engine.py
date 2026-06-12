from __future__ import annotations

from emotion_engine import (
    EmotionEvent,
    EmotionStateMachine,
    build_prompt_block,
    derive_label,
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
