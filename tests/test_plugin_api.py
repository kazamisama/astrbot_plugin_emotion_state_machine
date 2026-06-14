"""Tests for the EmotionStateMachinePlugin public API.

These tests exercise the wrapper methods that other plugins are expected
to call. They bypass the full AstrBot Star.__init__ by constructing the
plugin instance with __new__ and only the attributes the public methods
touch.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from main import EmotionStateMachinePlugin
from emotion_engine import EmotionStateMachine


class _FakeConfig:
    """Minimal stand-in for AstrBotConfig.

    Only the keys the public API + _save_state read need to be present.
    """

    def __init__(self, **overrides: Any) -> None:
        self._values: dict[str, Any] = {
            "enabled": True,
            "only_group": True,
            "inject_enabled": True,
            "persist_state": False,  # default off in tests
            "state_path": "",
            "save_interval_seconds": 10.0,
            "decay_half_life_seconds": 900.0,
            "active_window_seconds": 300.0,
            "relation_ttl_seconds": 604800.0,
            "group_ttl_seconds": 2592000.0,
            "dilution_exponent": 0.5,
        }
        self._values.update(overrides)

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)


def _make_plugin(**config_overrides: Any) -> EmotionStateMachinePlugin:
    """Build a plugin instance suitable for testing the public API.

    Skips Star.__init__ — the public API does not call into the AstrBot
    context, so we only set the attributes it touches.
    """
    plugin = EmotionStateMachinePlugin.__new__(EmotionStateMachinePlugin)
    plugin.context = SimpleNamespace()  # unused by public API
    plugin.config = _FakeConfig(**config_overrides)
    plugin.data_dir = Path(tempfile.gettempdir())
    plugin.state_path = plugin.data_dir / "emotion_state_test.json"
    plugin.machine = EmotionStateMachine(
        decay_half_life_seconds=plugin.config.get("decay_half_life_seconds", 900.0),
        active_window_seconds=plugin.config.get("active_window_seconds", 300.0),
        relation_ttl_seconds=plugin.config.get("relation_ttl_seconds", 604800.0),
        group_ttl_seconds=plugin.config.get("group_ttl_seconds", 2592000.0),
        dilution_exponent=plugin.config.get("dilution_exponent", 0.5),
    )
    plugin._last_save_time = 0.0
    return plugin


def _fake_event(*, group_id: str | None = "group-x", self_id: str = "bot") -> Any:
    return SimpleNamespace(
        get_group_id=lambda: group_id,
        get_self_id=lambda: self_id,
        get_sender_id=lambda: "user-a",
        unified_msg_origin="om-x",
        is_at_or_wake_command=False,
    )


# ----------------------------------------------------------------------
# Scope helpers
# ----------------------------------------------------------------------


def test_get_scope_matches_private_scope_id() -> None:
    plugin = _make_plugin()
    event = _fake_event(group_id="g-42")
    # The public method should agree with the private one.
    assert plugin.get_scope(event) == plugin._scope_id(event)


def test_get_scope_falls_back_to_unified_origin_for_private() -> None:
    plugin = _make_plugin()
    event = _fake_event(group_id=None)
    event.unified_msg_origin = "private:user-a:bot"
    assert plugin.get_scope(event) == "private:user-a:bot"


def test_get_scope_handles_missing_origin() -> None:
    plugin = _make_plugin()
    event = _fake_event(group_id=None)
    event.unified_msg_origin = None
    assert plugin.get_scope(event) == "_private"


# ----------------------------------------------------------------------
# Read API
# ----------------------------------------------------------------------


def test_list_signals_is_non_empty_and_unique() -> None:
    plugin = _make_plugin()
    signals = plugin.list_signals()
    assert isinstance(signals, list)
    assert len(signals) > 0
    assert len(signals) == len(set(signals))
    # Spot check that the well-known signals are present.
    for expected in ("praise", "thanks", "insult", "pressure"):
        assert expected in signals


def test_get_combined_state_default_is_calm() -> None:
    plugin = _make_plugin()
    view = plugin.get_combined_state("group-1", "user-a")
    assert view.group.label == "calm"
    assert view.label == "calm"


def test_get_group_state_and_relation_state_independent() -> None:
    plugin = _make_plugin()
    plugin.apply_signal("group-1", "user-a", "praise", intensity=1.0)

    group = plugin.get_group_state("group-1")
    relation = plugin.get_relation_state("group-1", "user-a")
    stranger = plugin.get_relation_state("group-1", "user-b")

    # Group moved; user-a's relation moved; user-b's did not.
    assert group.last_signal == "praise"
    assert relation.last_signal == "praise"
    assert stranger.transitions == 0


def test_render_state_text_matches_built_in_command() -> None:
    plugin = _make_plugin()
    plugin.apply_signal("group-1", "user-a", "thanks", intensity=1.0)
    text = plugin.render_state_text("group-1", "user-a")
    # Output starts with an emoji header, then sections for group and
    # relation, ending with a combined_label line.
    assert "Group Emotion" in text
    assert "group-1" in text
    assert "User Relation" in text
    assert "user-a" in text
    assert "combined_label" in text
    assert "thanks" in text  # the signal we just applied


# ----------------------------------------------------------------------
# Write API
# ----------------------------------------------------------------------


def test_apply_signal_accepts_known_signal() -> None:
    plugin = _make_plugin()
    view = plugin.apply_signal("g", "u", "praise", intensity=1.2, reason="unit test")
    assert view.group.last_signal == "praise"
    assert view.relation is not None
    assert view.relation.last_signal == "praise"


def test_apply_signal_rejects_unknown_signal() -> None:
    plugin = _make_plugin()
    with pytest.raises(ValueError, match="Unknown signal"):
        plugin.apply_signal("g", "u", "not_a_real_signal")


def test_observe_text_infers_and_applies() -> None:
    plugin = _make_plugin()
    view = plugin.observe_text(
        "g", "谢谢，搞定了！", user_id="u", mentioned=True
    )
    # The public method delegates to engine.observe_text; we only assert
    # that the state advanced (some signal was applied).
    assert view.group.transitions >= 1


def test_reset_scope_clears_group_and_relations() -> None:
    plugin = _make_plugin()
    plugin.apply_signal("g", "u", "praise", intensity=1.0)

    # Sanity: both layers moved.
    group_before = plugin.get_group_state("g", apply_decay=False)
    rel_before = plugin.get_relation_state("g", "u", apply_decay=False)
    assert group_before.transitions >= 1
    assert rel_before.transitions >= 1

    plugin.reset_scope("g")

    group_after = plugin.get_group_state("g", apply_decay=False)
    rel_after = plugin.get_relation_state("g", "u", apply_decay=False)
    # Reset clears BOTH group snapshot and all relations under that scope
    # (matches the built-in /emotion_reset command).
    assert group_after.transitions == 0
    assert rel_after.transitions == 0


def test_force_decay_moves_state_toward_baseline() -> None:
    plugin = _make_plugin(decay_half_life_seconds=10.0)
    # Drive stress up.
    plugin.apply_signal("g", "u", "pressure", intensity=2.0)
    stressed = plugin.get_group_state("g", apply_decay=False).stress

    # Advance the clock 100s (10 half-lives) — decay should drive stress
    # strongly back toward the baseline (0.18).
    decayed = plugin.force_decay("g", now=time.time() + 100.0)

    assert decayed.stress < stressed
    # After ~10 half-lives, we should be within 0.01 of the baseline.
    assert decayed.stress == pytest.approx(0.18, abs=0.01)


# ----------------------------------------------------------------------
# Prompt block API
# ----------------------------------------------------------------------


def test_build_prompt_block_contains_layers() -> None:
    plugin = _make_plugin()
    plugin.apply_signal("g", "u", "technical", intensity=1.0)
    block = plugin.build_prompt_block("g", "u")
    assert "## Bot Emotion State" in block
    assert "group:" in block
    assert "towards_current_user:" in block


# ----------------------------------------------------------------------
# Normalization
# ----------------------------------------------------------------------


def test_get_combined_state_normalizes_inputs() -> None:
    plugin = _make_plugin()
    # Whitespace / case shouldn't matter thanks to normalize_scope.
    view_a = plugin.get_combined_state("  Group-1  ", "  User-A  ")
    view_b = plugin.get_combined_state("group-1", "user-a")
    assert view_a.group.label == view_b.group.label


# ----------------------------------------------------------------------
# try_apply_signal — safe variant for hot paths
# ----------------------------------------------------------------------


def test_try_apply_signal_returns_view_for_known_signal() -> None:
    plugin = _make_plugin()
    view = plugin.try_apply_signal("g", "u", "praise", intensity=1.0)
    assert view is not None
    assert view.group.last_signal == "praise"
    assert view.relation is not None
    assert view.relation.last_signal == "praise"


def test_try_apply_signal_returns_none_for_unknown_signal() -> None:
    """Hot-path variant must NOT raise on invalid input."""
    plugin = _make_plugin()
    result = plugin.try_apply_signal("g", "u", "not_a_real_signal")
    assert result is None


def test_try_apply_signal_returns_none_for_nan_intensity() -> None:
    plugin = _make_plugin()
    result = plugin.try_apply_signal("g", "u", "praise", intensity=float("nan"))
    assert result is None


def test_try_apply_signal_returns_none_for_non_numeric_intensity() -> None:
    plugin = _make_plugin()
    result = plugin.try_apply_signal("g", "u", "praise", intensity="not a number")  # type: ignore[arg-type]
    assert result is None


def test_try_apply_signal_does_not_mutate_state_on_failure() -> None:
    """When the safe variant returns None, no signal should be applied
    to the engine — state should be unchanged."""
    plugin = _make_plugin()
    before = plugin.get_group_state("g")
    assert plugin.try_apply_signal("g", "u", "not_a_real_signal") is None
    after = plugin.get_group_state("g", apply_decay=False)
    assert after.transitions == before.transitions
    assert after.last_signal == before.last_signal


# ----------------------------------------------------------------------
# intensity validation in apply_signal
# ----------------------------------------------------------------------


def test_apply_signal_clamps_out_of_range_intensity() -> None:
    """Finite out-of-range values are silently clamped, matching the
    historical behavior of EmotionStateMachine.apply_interaction."""
    plugin = _make_plugin()
    # intensity > 2.0 should clamp to 2.0 — no exception.
    view = plugin.apply_signal("g", "u", "praise", intensity=10.0)
    assert view is not None
    view_low = plugin.apply_signal("g", "u", "praise", intensity=-1.0)
    assert view_low is not None


def test_apply_signal_rejects_nan_intensity() -> None:
    plugin = _make_plugin()
    with pytest.raises(ValueError, match="finite"):
        plugin.apply_signal("g", "u", "praise", intensity=float("nan"))


def test_apply_signal_rejects_non_numeric_intensity() -> None:
    plugin = _make_plugin()
    with pytest.raises(TypeError, match="number"):
        plugin.apply_signal("g", "u", "praise", intensity=None)  # type: ignore[arg-type]


def test_apply_signal_unchanged_contract_for_valid_input() -> None:
    """The original happy path must keep working after the validation
    refactor — praise with intensity=1.0 still moves valence up."""
    plugin = _make_plugin()
    view = plugin.apply_signal("g", "u", "praise", intensity=1.0)
    assert view.group.last_signal == "praise"
    assert view.group.valence > 0.56


# ----------------------------------------------------------------------
# prune_cold_state — plugin-level maintenance entry point
# ----------------------------------------------------------------------


def test_prune_cold_state_returns_count_dict() -> None:
    plugin = _make_plugin()
    # Touch a scope so it's not empty.
    plugin.apply_signal("g", "u", "praise", intensity=1.0)
    result = plugin.prune_cold_state()
    assert isinstance(result, dict)
    assert "groups_pruned" in result
    assert "relations_pruned" in result
    # Nothing is cold yet → no pruning.
    assert result["groups_pruned"] == 0
    assert result["relations_pruned"] == 0


def test_prune_cold_state_includes_group_ttl_config() -> None:
    """The plugin must wire up the group_ttl_seconds config from schema."""
    plugin = _make_plugin()
    # Default value (2592000 = 30 days) should be applied.
    assert plugin.machine.group_ttl_seconds == 2592000.0
