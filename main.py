"""
AstrBot Emotion State Machine

A lightweight plugin that simulates the bot's emotional state as a decaying
state machine.  It can observe conversation signals, expose debug commands, and
inject a compact emotion block before LLM requests.

Public API for other plugins
============================

The complete public API contract is documented in ``_PUBLIC_API.md`` at the
plugin root. Methods NOT listed there are implementation details and may change
without notice. Highlights (v0.10.0+):

- Reading state:    ``get_combined_state``, ``get_group_state``,
                    ``get_relation_state``, ``render_state_text``
- Building blocks:  ``build_prompt_block``, ``to_text_part``
- Writing state:    ``observe_text``, ``apply_signal``, ``try_apply_signal``,
                    ``apply_self_reply_signal``, ``reset_scope``
- Meta:             ``list_signals``, ``is_signal_enabled``,
                    ``list_disabled_signals``, ``get_scope``

To obtain an instance from another plugin::

    esm = self.context.get_registered_star("astrbot_plugin_emotion_state_machine")
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.agent.message import TextPart
from astrbot.core.config.astrbot_config import AstrBotConfig

try:
    from .emotion_engine import (
        ESM_BLOCK_END,
        ESM_BLOCK_START,
        CombinedEmotionView,
        EmotionEvent,
        EmotionStateMachine,
        GroupEmotionSnapshot,
        UserRelationSnapshot,
        build_prompt_block,
        format_combined_chart,
        format_combined_view,
        normalize_scope,
        normalize_user_id,
        signal_names,
    )
    from .emotion_engine import __version__ as _ESM_VERSION  # v0.8.21
    from .emotion_engine.api import get_full_state
    from .emotion_engine.webui import render_webui_page, render_state_json
except ImportError:  # pragma: no cover - allow direct script imports in tests
    from emotion_engine import (
        ESM_BLOCK_END,
        ESM_BLOCK_START,
        CombinedEmotionView,
        EmotionEvent,
        EmotionStateMachine,
        GroupEmotionSnapshot,
        UserRelationSnapshot,
        __version__ as _ESM_VERSION,
        build_prompt_block,
        format_combined_chart,
        format_combined_view,
        normalize_scope,
        normalize_user_id,
        signal_names,
    )
    from emotion_engine.api import get_full_state
    from emotion_engine.webui import render_webui_page, render_state_json


# Compiled once at module load — the sentinels are static, so the
# find-and-replace pattern doesn't need to be rebuilt per injection.
_ESM_BLOCK_PATTERN = re.compile(
    re.escape(ESM_BLOCK_START) + r".*?" + re.escape(ESM_BLOCK_END),
    re.DOTALL,
)


def _inject_emotion_block(system_prompt: str, block: str) -> str:
    """Inject ``block`` into ``system_prompt``, replacing any prior
    emotion block identified by the sentinel markers.

    - If the system prompt already contains an emotion block (start +
      end markers), the entire range is replaced with ``block``.
    - Otherwise the block is appended after a blank line, or at the
      start if the prompt is empty.

    The result is normalized so that there is exactly one trailing
    newline, regardless of which branch ran.
    """
    base = (system_prompt or "").rstrip()
    if _ESM_BLOCK_PATTERN.search(base):
        # Remove ALL existing emotion blocks first, then append the new
        # one. Two-step guarantees the invariant "exactly one emotion
        # block in the prompt" even when upstream code left duplicates
        # behind (a single regex sub would copy the new block into
        # every match).
        cleaned = _ESM_BLOCK_PATTERN.sub("", base).rstrip()
        result = cleaned + "\n\n" + block
    elif base:
        result = base + "\n\n" + block
    else:
        result = block
    return result.rstrip() + "\n"


# ----------------------------------------------------------------------------
# TalkWillingness — v0.10.0+ self-reply accumulation state machine
# ----------------------------------------------------------------------------
#
# Replaces the draft "interval throttling" approach with a brain-inspired
# accumulation model. The bot's "willingness to speak proactively" is
# modeled as a slowly accumulated internal state W, modulated by three
# factors (time / turn density / own emotion). W entering the trigger
# zone (LOW..HIGH) means the bot speaks; crossing HIGH triggers a
# reversal mode where W actively decreases (saturation protection).
#
# Patterns borrowed from neuroscience:
# - Habituation: consecutive_apply counter caps repeated identical output
# - Refractory period: post-trigger suppression window
# - Saturation reversal: HIGH threshold inversion
# - User interruption: consecutive counter reset on user message
#
# This class is *pure* (no I/O, no plugin state) so it can be tested
# deterministically. The plugin's apply_self_reply_signal() is responsible
# for providing inputs (scope, now, group view, turn count) and
# interpreting the (should_apply, intensity) decision.
#
# v0.10.0+: kept module-level rather than inside the Star class so tests
# can import it directly without standing up the full AstrBot runtime.


@dataclass
class _TalkWillingness:
    """Per-scope internal state tracked by :class:`TalkWillingnessState`.

    All fields are mutable; the owning state machine mutates them in
    place. Reset to defaults via :meth:`TalkWillingnessState.reset`.
    """

    W: float = 0.0
    last_tick_ts: float = 0.0
    consecutive_apply: int = 0
    last_apply_ts: float = 0.0


class TalkWillingnessState:
    """Brain-inspired self-reply accumulation state machine.

    Public entry point is :meth:`tick`. Returns a ``(W_new, should_apply,
    intensity)`` triple. ``should_apply`` is True iff W is in the trigger
    zone AND the consecutive-apply cap hasn't been hit.

    Tunables (override via ``config_getter`` at construction time):

    - ``self_reply_threshold_low`` (default 0.55): trigger zone entry.
    - ``self_reply_threshold_high`` (default 0.85): reversal zone entry.
    - ``self_reply_decay`` (default 0.92): per-tick natural decay.
    - ``self_reply_refractory_seconds`` (default 30.0): post-trigger
      suppression window during which W is multiplied by 0.30.
    - ``self_reply_max_consecutive`` (default 5): max self-reply signals
      before consecutive-apply counter forces a fall-back.
    """

    THRESHOLD_LOW = 0.55
    THRESHOLD_HIGH = 0.85
    HARD_CAP = 1.20
    REFRACTORY_SECONDS = 30.0
    MAX_CONSECUTIVE = 5
    DECAY = 0.92

    # Time factor tuning
    TIME_SILENCE_FLOOR = 30.0   # below this, no time charge
    TIME_WINDOW = 600.0         # over this, charge saturates
    TIME_CHARGE_MAX = 0.15

    # Turn factor tuning
    TURNS_HIGH_DENSITY = 3      # ≥3 turns/5min → satisfying
    TURN_SATISFACTION = -0.10
    TURN_AWKWARD = -0.05        # 0 turns + user present (elapsed < 60s)

    # Emotion factor tuning
    VALENCE_LOW_RED_LINE = 0.35
    EMOTION_CHARGE_SCALE = 0.10
    VALENCE_LOW_PENALTY = -0.08

    # Intensity mapping (W in LOW..HIGH → intensity 0.05..0.25)
    INTENSITY_MIN = 0.05
    INTENSITY_MAX = 0.25

    def __init__(
        self,
        config_getter: Callable[[str, Any], Any] | None = None,
        energy_getter: Callable[[], float] | None = None,
    ) -> None:
        # config_getter lets the plugin pass in ``self._cfg_str`` /
        # ``self._cfg_float`` so all thresholds come from
        # _conf_schema.json. Defaults to identity (return the second arg).
        self._cfg: Callable[[str, Any], Any] = (
            config_getter if config_getter is not None
            else lambda key, default: default
        )
        # energy_getter returns a 0..1 "bot energy" value each tick.
        # 1.0 = neutral (no modulation). Default lambda returns 1.0 so
        # TalkWillingness behaves identically when no energy system is
        # registered. The plugin can pass a callable that queries an
        # "energy_system" star; when that star is absent the callable
        # itself should return 1.0 to preserve neutrality.
        self._energy: Callable[[], float] = (
            energy_getter if energy_getter is not None else (lambda: 1.0)
        )
        self._states: dict[str, _TalkWillingness] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def tick(
        self,
        scope: str,
        now: float,
        user_message_arrived: bool,
        group_view: Any,
        user_turns_in_5min: int,
    ) -> tuple[float, bool, float]:
        """Update the per-scope state and decide whether to apply a
        self-reply signal.

        :param scope: normalized scope key (e.g. ``"group-123:sherry"``).
        :param now: monotonic-ish timestamp (``time.time()`` is fine).
        :param user_message_arrived: True iff a user message landed in
            the same tick — resets the consecutive-apply counter.
        :param group_view: ``GroupEmotionSnapshot`` for the scope, used
            to read arousal/curiosity/valence for the emotion factor.
            Any object with those three float attributes works.
        :param user_turns_in_5min: count of user turns in the last 5
            minutes for the turn-density factor.
        :returns: ``(W_new, should_apply_signal, intensity)``.

            - ``W_new``: post-tick accumulation value, clamped to
              ``[0, HARD_CAP]``.
            - ``should_apply_signal``: True iff in trigger zone and
              consecutive-apply cap not exceeded.
            - ``intensity``: signal intensity multiplier in
              ``[INTENSITY_MIN, INTENSITY_MAX]``; 0.0 when
              ``should_apply_signal`` is False.
        """
        # Initialize timestamp on the FIRST tick for this scope only.
        # Check the dict directly — checking ``state.last_tick_ts == 0.0``
        # is wrong because that value can also be a legitimate "0"
        # timestamp (e.g. when the bot starts at Unix epoch, which is
        # uncommon in practice but not impossible). The dict presence
        # check is unambiguous.
        is_first_tick = scope not in self._states
        state = self._states.setdefault(scope, _TalkWillingness())
        if is_first_tick:
            state.last_tick_ts = now

        elapsed = max(0.0, now - state.last_tick_ts)

        # Refractory: just-applied scope gets a sharp W suppression.
        refractory = float(self._cfg(
            "self_reply_refractory_seconds", self.REFRACTORY_SECONDS,
        ))
        if refractory > 0 and now - state.last_apply_ts < refractory:
            state.W *= 0.30

        # User message interrupts consecutive-apply chain.
        if user_message_arrived:
            state.consecutive_apply = 0

        # Natural decay (always applied, even if refractory was a no-op).
        decay = float(self._cfg("self_reply_decay", self.DECAY))
        state.W *= decay

        # Three-factor net charge.
        net_charge = (
            self._time_charge(elapsed, user_message_arrived)
            + self._turn_charge(user_turns_in_5min, elapsed)
            + self._emotion_charge(group_view)
        )
        # v0.10.0+ optional modulation factors. Both default to 1.0
        # (neutral) so behavior is unchanged when the inputs are
        # absent (no energy system registered / no active_users info).
        net_charge *= self._energy_factor() * self._crowd_factor(group_view)
        state.W += net_charge

        # Threshold decision — also mutates state.consecutive_apply
        # and state.last_apply_ts when triggering.
        should_apply, intensity = self._threshold_decision(state, now)

        # Clamp and advance clock.
        state.W = max(0.0, min(self.HARD_CAP, state.W))
        state.last_tick_ts = now

        return state.W, should_apply, intensity

    def on_scope_deleted(self, scope: str) -> None:
        """Drop the per-scope state when the underlying ESM scope is
        removed. Prevents the dict from growing unbounded across the
        plugin's lifetime.
        """
        self._states.pop(scope, None)

    def reset(self, scope: str) -> None:
        """Force-clear a scope's state. Currently identical to
        :meth:`on_scope_deleted`; kept as a separate name for API
        symmetry with ``reset_scope()`` on the engine.
        """
        self.on_scope_deleted(scope)

    def __len__(self) -> int:
        """Number of scopes currently tracked. Useful for tests and
        for diagnostic logging.
        """
        return len(self._states)

    # ------------------------------------------------------------------
    # Charge factors (private)
    # ------------------------------------------------------------------

    def _time_charge(self, elapsed: float, user_msg: bool) -> float:
        """寂寞蓄力: longer silence → stronger charge, capped.

        - < 30s silence OR user just spoke: 0
        - 30s..10min: linearly scales from 0 to TIME_CHARGE_MAX
        - > 10min: clamped to TIME_CHARGE_MAX
        """
        if user_msg or elapsed < self.TIME_SILENCE_FLOOR:
            return 0.0
        headroom = elapsed - self.TIME_SILENCE_FLOOR
        return min(
            self.TIME_CHARGE_MAX,
            headroom / self.TIME_WINDOW * self.TIME_CHARGE_MAX,
        )

    def _turn_charge(self, turns_recent: int, elapsed: float) -> float:
        """满足感: dense recent conversation → negative charge."""
        if turns_recent >= self.TURNS_HIGH_DENSITY:
            return self.TURN_SATISFACTION
        # 0 turns AND user is "present" (elapsed < 60s) is awkward;
        # mild negative charge to suppress premature self-reply.
        if turns_recent == 0 and elapsed < 60.0:
            return self.TURN_AWKWARD
        return 0.0

    def _emotion_charge(self, view: Any) -> float:
        """Self emotion modulation. Reads arousal/curiosity/valence from
        the group snapshot.

        Critical: this method MUST NOT touch affection/trust or any
        relation-layer dimension. Reading those would re-introduce the
        very feedback loop the self_reply signal was designed to break.
        """
        valence = float(getattr(view, "valence", 0.5))
        if valence < self.VALENCE_LOW_RED_LINE:
            return self.VALENCE_LOW_PENALTY
        arousal = float(getattr(view, "arousal", 0.5))
        curiosity = float(getattr(view, "curiosity", 0.5))
        scale = self.EMOTION_CHARGE_SCALE
        return (arousal - 0.5) * scale + (curiosity - 0.5) * scale

    # ------------------------------------------------------------------
    # Optional modulation factors (v0.10.0+, opt-in via config)
    # ------------------------------------------------------------------

    def _energy_factor(self) -> float:
        """v0.10.0+ energy modulation.

        Maps a 0..1 "bot energy" reading (from the optional
        energy_getter callable passed to ``__init__``) to a multiplier
        on net_charge in ``[0.5, 1.0]``. Energy=1.0 → factor 1.0
        (no modulation); energy=0.0 → factor 0.5 (half accumulation).

        If no energy_getter is registered, ``self._energy`` returns
        1.0 by default → factor=1.0 → no effect on accumulation.
        """
        try:
            energy = float(self._energy())
        except Exception:
            # Defensive: any failure in the energy source must NOT
            # break the tick path. Fall back to neutral.
            return 1.0
        if not math.isfinite(energy):
            return 1.0
        # Clamp to [0, 1] before mapping.
        energy = max(0.0, min(1.0, energy))
        return 0.5 + energy * 0.5

    def _crowd_factor(self, view: Any) -> float:
        """v0.10.0+ crowd-size modulation.

        Returns ``1 / sqrt(active_users)``, clamped to ``[0.1, 1.0]``.
        Larger groups → smaller factor → bot accumulates W more
        slowly, so proactive self-reply feels less noisy in busy
        rooms. 1-person (DMs / solo) → 1.0 (no modulation).

        Tolerant of group views that don't expose ``active_users``:
        returns 1.0 (neutral) when the attribute is missing or empty.
        """
        active = 0
        au = getattr(view, "active_users", None)
        if isinstance(au, dict):
            active = len(au)
        elif isinstance(au, (int, float)):
            active = int(au)
        elif au is not None and hasattr(au, "__len__"):
            try:
                active = len(au)
            except Exception:
                active = 0
        if active <= 1:
            return 1.0
        raw = 1.0 / math.sqrt(active)
        return max(0.1, min(1.0, raw))

    # ------------------------------------------------------------------
    # Threshold logic (private)
    # ------------------------------------------------------------------

    def _threshold_decision(
        self, state: _TalkWillingness, now: float,
    ) -> tuple[bool, float]:
        """Decide whether to fire this tick. Also mutates consecutive
        counter and last_apply_ts when firing (so the next refractory
        check sees the updated timestamp).
        """
        low = float(self._cfg("self_reply_threshold_low", self.THRESHOLD_LOW))
        high = float(self._cfg("self_reply_threshold_high", self.THRESHOLD_HIGH))
        max_consecutive = int(self._cfg(
            "self_reply_max_consecutive", self.MAX_CONSECUTIVE,
        ))
        # Sanity: LOW must be < HIGH; if config breaks the invariant,
        # fall back to defaults rather than raise.
        if not (0.0 < low < high):
            low, high = self.THRESHOLD_LOW, self.THRESHOLD_HIGH

        W = state.W

        if W > high:
            # Reversal zone: actively pull W down, do NOT apply.
            state.W = W * 0.65 - 0.05
            return False, 0.0

        if W > low:
            # Trigger zone: check consecutive-apply cap.
            if state.consecutive_apply >= max_consecutive:
                # Cap hit — force a fall-back so W can drain.
                state.W = W * 0.5
                return False, 0.0
            intensity = self._intensity_from_W(W, low, high)
            state.consecutive_apply += 1
            state.last_apply_ts = now
            state.W = W * 0.45  # trigger reset
            return True, intensity

        # Accumulation zone.
        return False, 0.0

    def _intensity_from_W(self, W: float, low: float, high: float) -> float:
        """Linearly map W in [low, high] to intensity in [MIN, MAX]."""
        if high <= low:
            return self.INTENSITY_MIN
        ratio = (W - low) / (high - low)
        ratio = max(0.0, min(1.0, ratio))
        return self.INTENSITY_MIN + ratio * (self.INTENSITY_MAX - self.INTENSITY_MIN)


class EmotionStateMachineStar(Star):
    """Simulate bot emotion state per conversation scope.

    Class name uses the ``<Name>Star`` convention matching engram-core
    (HippocampusStar) — the Dashboard's WebUI page route discovery
    in v4.25.x appears to key off this suffix.
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = self._resolve_data_dir()
        self.state_path = self._resolve_state_path()
        self.machine = EmotionStateMachine(
            decay_half_life_seconds=self._cfg_float("decay_half_life_seconds", 900.0, 1.0),
            active_window_seconds=self._cfg_float("active_window_seconds", 1800.0, 1.0),
            relation_ttl_seconds=self._cfg_float("relation_ttl_seconds", 604800.0, 1.0),
            group_ttl_seconds=self._cfg_float("group_ttl_seconds", 2592000.0, 1.0),
            dilution_exponent=self._cfg_float("dilution_exponent", 0.5, 0.0),
            appraisal_mode=self._cfg_str("appraisal_mode", "direct"),
        )
        self._last_save_time = 0.0
        # v0.10.0+: self-reply accumulation state machine (pure math;
        # config-driven via the cfg helper passed in). Module-level
        # class lives just above the Star class definition.
        self._talk_willingness = TalkWillingnessState(
            config_getter=self.config.get,
            energy_getter=self._get_bot_energy,
        )
        # Per-scope user-message timestamp tracking — feeds the time
        # factor ("how long since user spoke?") and the turn-density
        # factor in TalkWillingnessState.tick(). Both dicts grow
        # with scope count but stay bounded by group_ttl pruning
        # (handled in on_scope_deleted).
        self._last_user_msg_ts: dict[str, float] = {}
        self._user_turn_ts: dict[str, list[float]] = {}
        # Register Dashboard routes FIRST so they remain available even
        # if _load_state() raises (e.g. corrupt JSON). The handlers
        # reference self.machine which is created above.
        self._register_official_page_api_if_available()
        self._load_state()
        # v0.9.23: migrate old scope_ids that lack the persona stamp
        # (pre-v0.9.22 default was ""). Now persona_stamp defaults to
        # "default", so we rename existing scopes to inject it.
        self._migrate_scope_ids_if_needed()

    def _cfg_bool(self, key: str, default: bool) -> bool:
        """Coerce a config value to bool, tolerating common string forms.

        The Chinese matches (``"开启" / "是" / "启用"`` and their
        negations) are intentionally retained even though AstrBot's web
        admin UI stores canonical ``"true" / "false"`` strings and will
        never produce them. They exist to support users who edit
        ``config.json`` by hand and prefer Chinese tokens, which is
        common in this plugin's user base. Removing them would silently
        break those hand-edited configs.
        """
        value = self.config.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "y", "on", "开启", "是", "启用"}:
                return True
            if lowered in {"false", "0", "no", "n", "off", "关闭", "否", "禁用"}:
                return False
            return default
        if value is None:
            return default
        return bool(value)

    def _cfg_float(self, key: str, default: float, min_value: float | None = None) -> float:
        try:
            value = float(self.config.get(key, default))
        except (TypeError, ValueError):
            value = default
        # JSON / YAML configs may carry the literal strings
        # "NaN", "Infinity", "-Infinity" (hand-edited config.json,
        # or programmatic writes). float() accepts all three without
        # raising, so we must guard finiteness explicitly. A non-finite
        # value here propagates into EmotionStateMachine and poisons
        # every decay_factor: half_life=inf freezes decay entirely,
        # half_life=NaN turns every dimension into NaN on the next
        # tick. Fall back to the default and emit a WARNING so the
        # operator can fix the config without silently breaking state.
        if not math.isfinite(value):
            logger.warning(
                f"[emotion_state_machine] _cfg_float got non-finite "
                f"value for {key!r}: {value!r}, falling back to {default!r}"
            )
            value = default
        if min_value is not None:
            value = max(min_value, value)
        return value

    def _cfg_list(self, key: str, default: list[str] | None = None) -> list[str]:
        """Read a list-of-strings config value, tolerating common shapes.

        Returns a normalized list of stripped, lowercase strings. Silently
        drops non-string entries — a malformed config should not break
        the plugin.
        """
        if default is None:
            default = []
        raw = self.config.get(key, default)
        if raw is None:
            return list(default)
        if isinstance(raw, str):
            # Support a single comma-separated string for hand-edited
            # config.json files.
            raw = [part.strip() for part in raw.split(",") if part.strip()]
        if not isinstance(raw, list):
            return list(default)
        result: list[str] = []
        for item in raw:
            if isinstance(item, str):
                stripped = item.strip().lower()
                if stripped:
                    result.append(stripped)
        return result

    def _get_disabled_signals(self) -> set[str]:
        """Return the set of currently-disabled signal names (lowercased)."""
        return set(self._cfg_list("disabled_signals", []))

    def _cfg_int(self, key: str, default: int, min_value: int | None = None) -> int:
        try:
            value = int(self.config.get(key, default))
        except (TypeError, ValueError):
            value = default
        if min_value is not None:
            value = max(min_value, value)
        return value

    def _cfg_str(self, key: str, default: str) -> str:
        """Read a string config value, falling back to ``default`` on
        non-string or missing input.

        Strips whitespace. Returns ``default`` for ``None`` and non-str
        types so the caller never sees an unexpected type (e.g. an int
        that a misconfigured web admin accidentally wrote).
        """
        raw = self.config.get(key, default)
        if isinstance(raw, str):
            return raw.strip()
        return str(default) if default else ""

    def _resolve_data_dir(self) -> Path:
        base = Path("data") / "plugin_data" / "astrbot_plugin_emotion_state_machine"
        try:
            base.mkdir(parents=True, exist_ok=True)
            return base
        except Exception:
            fallback = Path(__file__).parent / "data"
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback

    def _resolve_state_path(self) -> Path:
        configured = str(self.config.get("state_path", "") or "").strip()
        if configured:
            return Path(configured)
        return self.data_dir / "emotion_state.json"

    def _known_persona_ids(self) -> set[str]:
        """v0.9.28: all persona IDs registered in AstrBot."""
        try:
            pm = getattr(self.context, "persona_manager", None)
            personas = getattr(pm, "personas_v3", []) or []
            return {
                str(p["name"])
                for p in personas
                if isinstance(p, dict) and p.get("name")
            }
        except Exception:
            return set()

    def _migrate_scope_ids_if_needed(self) -> None:
        """v0.9.23 / v0.9.28: migrate scope_ids so every scope has
        exactly one persona stamp suffix.

        Rules:
        - If scope ends with a known persona ID (any of: ``sherry``,
          ``mortis``, etc.), DO NOT touch it.
        - If scope ends with the configured stamp (``_bot_persona_name()``),
          skip.
        - Otherwise (scope has NO stamp), append the configured stamp.
        - v0.9.28 also cleans up DOUBLE stamps that earlier versions
          accidentally created (``...:sherri:mortis`` → ``...:sherri``).
        """
        stamp = self._bot_persona_name()
        if not stamp:
            return
        known = self._known_persona_ids() | {stamp}
        machine = self.machine
        renamed = 0
        cleaned = 0

        for scope_id in list(machine.groups.keys()):
            parts = scope_id.split(":")
            last = parts[-1] if parts else ""

            # Strip trailing ":sherri:mortis"-style double stamps.
            # If the last two segments are both known persona IDs,
            # drop the trailing one and keep the inner.
            if (
                len(parts) >= 2
                and parts[-1] in known
                and parts[-2] in known
            ):
                new_id = ":".join(parts[:-1])
                machine.groups[new_id] = machine.groups.pop(scope_id)
                if scope_id in machine.relations:
                    machine.relations[new_id] = machine.relations.pop(scope_id)
                logger.warning(
                    f"[emotion_state_machine] dedup persona stamp: "
                    f"{scope_id!r} -> {new_id!r}"
                )
                scope_id = new_id
                cleaned += 1

            # Now decide whether to append the configured stamp.
            last = scope_id.split(":")[-1]
            if last in known:
                continue  # already has a (valid) stamp

            new_id = scope_id + ":" + stamp
            machine.groups[new_id] = machine.groups.pop(scope_id)
            if scope_id in machine.relations:
                machine.relations[new_id] = machine.relations.pop(scope_id)
            renamed += 1
            logger.warning(
                f"[emotion_state_machine] migrated scope "
                f"{scope_id!r} -> {new_id!r}"
            )

        if renamed or cleaned:
            self._save_state(force=True)
            logger.warning(
                f"[emotion_state_machine] scope_id migration: "
                f"{renamed} appended stamp, {cleaned} deduped double-stamps"
            )

    
    def _bot_persona_name(self) -> str:
        """v0.9.24: sync fallback — get the bot's globally-configured
        default persona name from AstrBot's persona manager, or the
        ``persona_stamp`` config as last-resort fallback.
        """
        try:
            pm = getattr(self.context, "persona_manager", None)
            if pm is not None:
                name = getattr(pm, "default_persona", None)
                if isinstance(name, str) and name:
                    return name
        except Exception:
            pass
        return self._cfg_str("persona_stamp", "default")

    async def _resolve_event_persona(self, event: AstrMessageEvent) -> str:
        """v0.9.27: per-conversation persona lookup, mirrors engram's
        persona_resolver. Three tiers:

        1. ``sp.get_async("session_service_config")`` (set by
           ``/persona`` or ``/sel_persona`` command)
        2. ``conversation_manager.get_conversation(umo, cid).persona_id``
           (per-conversation persona assigned by AstrBot)
        3. ``persona_manager.get_default_persona_v3(umo=umo).name``
           (global default)

        Returns the resolved persona id, or the legacy fallback
        (``_bot_persona_name()``) on any failure. Each tier logs what
        it found so we can debug stuck-on-default cases.
        """
        try:
            from astrbot.api import sp
        except Exception:
            sp = None
        umo = getattr(event, "unified_msg_origin", None) or ""
        # tier 1: session_service_config.persona_id (set by /persona)
        if sp is not None and umo:
            try:
                cfg = await sp.get_async(
                    scope="umo", scope_id=umo,
                    key="session_service_config", default={},
                )
                pid = (cfg or {}).get("persona_id")
                logger.info(
                    f"[emotion_state_machine] persona tier1 (session_service_config) "
                    f"umo={umo!r} cfg={cfg!r} pid={pid!r}"
                )
                if pid:
                    return str(pid)
            except Exception as exc:
                logger.warning(f"persona tier1 failed: {exc!r}")
        # tier 2: conversation.persona_id via conversation_manager
        cm = getattr(self.context, "conversation_manager", None)
        logger.info(
            f"[emotion_state_machine] persona tier2 cm={type(cm).__name__ if cm else None} "
            f"umo={umo!r}"
        )
        if cm is not None and umo:
            try:
                cid = await cm.get_curr_conversation_id(umo)
                logger.info(
                    f"[emotion_state_machine] persona tier2 cid={cid!r}"
                )
                if cid is not None:
                    conv = await cm.get_conversation(umo, cid)
                    if conv is not None:
                        pid = getattr(conv, "persona_id", None)
                        logger.info(
                            f"[emotion_state_machine] persona tier2 conv.pid={pid!r} "
                            f"conv_type={type(conv).__name__}"
                        )
                        if pid == "[%None]":
                            return ""  # explicitly persona-less
                        if pid:
                            return str(pid)
            except Exception as exc:
                logger.warning(f"persona tier2 failed: {exc!r}")
        # tier 3: global default persona (per-UMO lookup)
        pm = getattr(self.context, "persona_manager", None)
        if pm is not None:
            try:
                dp = await pm.get_default_persona_v3(umo=umo or None)
                if isinstance(dp, dict):
                    pid = dp.get("name")
                    logger.info(
                        f"[emotion_state_machine] persona tier3 "
                        f"get_default_persona_v3 name={pid!r}"
                    )
                    if pid:
                        return str(pid)
            except Exception as exc:
                logger.warning(f"persona tier3 failed: {exc!r}")
        # final fallback: sync best-effort
        fallback = self._bot_persona_name()
        logger.info(
            f"[emotion_state_machine] persona fallback (sync) -> {fallback!r}"
        )
        return fallback

    async def _scope_id(self, event: AstrMessageEvent) -> str:
        base = event.get_group_id() or event.unified_msg_origin or "_private"
        # v0.9.48: persona_isolation_enabled switch — when off, all
        # personas share one namespace (pre-v0.9.22 behavior).
        if not self._cfg_bool("persona_isolation_enabled", True):
            return base
        stamp = await self._resolve_event_persona(event)
        return f"{base}:{stamp}" if stamp else base

    def _load_state(self) -> None:
        if not self._cfg_bool("persist_state", True):
            return
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.machine = EmotionStateMachine.from_dict(
                data,
                decay_half_life_seconds=self._cfg_float("decay_half_life_seconds", 900.0, 1.0),
            )
            self.machine.active_window_seconds = self._cfg_float("active_window_seconds", 300.0, 1.0)
            self.machine.relation_ttl_seconds = self._cfg_float("relation_ttl_seconds", 604800.0, 1.0)
            self.machine.group_ttl_seconds = self._cfg_float("group_ttl_seconds", 2592000.0, 1.0)
            self.machine.dilution_exponent = self._cfg_float("dilution_exponent", 0.5, 0.0)
            # Drop cold scopes that survived in the JSON file from a
            # previous run — bounds startup memory for long-running bots.
            pruned = self.machine._prune_groups()
            if pruned:
                logger.info(
                    f"[emotion_state_machine] pruned {pruned} cold scope(s) on load"
                )
            logger.info(f"[emotion_state_machine] loaded state from {self.state_path}")
        except Exception as exc:
            logger.warning(f"[emotion_state_machine] failed to load state: {exc}")

    def _save_state(self, *, force: bool = False) -> None:
        if not self._cfg_bool("persist_state", True):
            return
        now = time.time()
        interval = self._cfg_float("save_interval_seconds", 10.0, 0.0)
        if not force and now - self._last_save_time < interval:
            return
        # Prune cold scopes before writing so the JSON file shrinks over
        # time instead of growing with dead group/relation entries.
        self.machine._prune_groups()
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(self.machine.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(self.state_path)
            self._last_save_time = now
        except Exception as exc:
            logger.warning(f"[emotion_state_machine] failed to save state: {exc}")

    @filter.event_message_type(filter.EventMessageType.ALL, priority=920)
    async def observe_message(self, event: AstrMessageEvent):
        """Observe incoming plain messages and update emotion state."""
        if not self._cfg_bool("enabled", True):
            return
        if self._cfg_bool("only_group", True) and not event.get_group_id():
            return

        text = (event.message_str or "").strip()
        if not text:
            return
        if str(event.get_sender_id()) == str(event.get_self_id()):
            return

        scope = await self._scope_id(event)
        user_id = str(event.get_sender_id())
        mentioned = bool(getattr(event, "is_at_or_wake_command", False))
        is_private = not event.get_group_id()
        # v0.9.51: relation only updates when the message is actually
        # directed at the bot (@/wake or private chat). Group chitchat
        # still updates group atmosphere but not user relation.
        # Future enhancement: also update when bot replied to this user
        # recently (requires on_llm_response hook to track reply times).
        apply_to_relation = mentioned or is_private
        # Filter the engine's inferred signals against the disabled list
        # before applying, so disabled signals never enter the state.
        disabled = self._get_disabled_signals()
        view = self.machine.observe_text(
            scope, text, user_id=user_id, mentioned=mentioned,
            disabled_signals=disabled if disabled else None,
            update_relation=apply_to_relation,
        )
        # v0.10.0+: feed the user-message timestamp trackers used by
        # TalkWillingnessState's time + turn-density factors. Stored
        # separately from emotion state so they survive emotion_engine
        # scope resets / migrations.
        now = time.time()
        self._last_user_msg_ts[scope] = now
        # Sliding-window turn log: keep last 5 minutes only. Sorted
        # insert is fine at this scale (≤ ~30 turns / 5min typical).
        cutoff = now - 300.0
        buf = self._user_turn_ts.setdefault(scope, [])
        buf.append(now)
        # Prune in-place; cheap because the list is tiny.
        while buf and buf[0] < cutoff:
            buf.pop(0)
        logger.debug(
            "[emotion_state_machine] observed message | "
            f"scope={scope} user={user_id} group_label={view.group.label} combined_label={view.label}"
        )
        self._save_state()

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, request):
        """Inject a compact emotion state block before LLM requests.

        v0.9.x migration: switched from ``request.system_prompt += block``
        to ``request.extra_user_content_parts.append(TextPart(...))``.
        The latter appends to the **user message** rather than the system
        prompt, which preserves LLM prefix cache (the dynamic emotion
        block no longer pollutes the cacheable system prompt prefix) and
        matches AstrBot's official pattern for "system reminders inside
        user messages" (see ``astr_main_agent._append_image_caption``).
        """
        if not self._cfg_bool("enabled", True):
            return
        if not self._cfg_bool("inject_enabled", True):
            return
        if self._cfg_bool("only_group", True) and not event.get_group_id():
            return

        scope = await self._scope_id(event)
        user_id = str(event.get_sender_id() or "")
        view = self.machine.get_combined(scope, user_id)
        # v0.9.52: admins can override the prompt template via the
        # `emotion_block_template` config field (see _conf_schema.json).
        # When the field is empty, build_prompt_block falls back to
        # DEFAULT_EMOTION_BLOCK_TEMPLATE in emotion_engine.prompt.
        custom_template = self._cfg_str("emotion_block_template", "")
        block = build_prompt_block(
            scope, view,
            template=custom_template or None,
        )
        # Append to extra_user_content_parts (not system_prompt) so the
        # dynamic block lands inside the user message rather than the
        # system prompt, keeping the LLM prefix cache intact.
        if hasattr(request, "extra_user_content_parts"):
            # v0.9.59: mark_as_temp() so the block is provider-facing
            # only (LLM sees it) but NOT persisted to conversation
            # history. Without this, the block would leak into the
            # next message's user prompt, polluting history and
            # causing the bot to "see" its own emotion state from
            # past turns. Mirrors livingmemory's extra_user_content
            # injection pattern (memory_recall.py:286).
            request.extra_user_content_parts.append(
                TextPart(text=block, type="text").mark_as_temp()
            )
        elif hasattr(request, "system_prompt"):
            # Fallback for older AstrBot versions that don't expose
            # extra_user_content_parts. Falls back to the legacy sentinel
            # pattern (cache pollution accepted as graceful degradation).
            request.system_prompt = _inject_emotion_block(
                request.system_prompt or "", block
            )

    def _render_config_snapshot(self) -> str:
        """Render a compact, human-readable snapshot of the effective
        config — appended to /emotion_state output so admins can verify
        the live values without opening the config file.

        TTL seconds are also expressed in days for easier mental math.
        """
        decay_half_life = self._cfg_float("decay_half_life_seconds", 900.0, 1.0)
        active_window = self._cfg_float("active_window_seconds", 300.0, 1.0)
        relation_ttl = self._cfg_float("relation_ttl_seconds", 604800.0, 1.0)
        group_ttl = self._cfg_float("group_ttl_seconds", 2592000.0, 1.0)
        dilution = self._cfg_float("dilution_exponent", 0.5, 0.0)
        save_interval = self._cfg_float("save_interval_seconds", 10.0, 0.0)
        disabled = self.list_disabled_signals()

        def _days(seconds: float) -> str:
            return f"({seconds / 86400.0:.1f} days)"

        lines = [
            "⚙ Config snapshot",
            f"- enabled: {self._cfg_bool('enabled', True)}",
            f"- only_group: {self._cfg_bool('only_group', True)}",
            f"- inject_enabled: {self._cfg_bool('inject_enabled', True)}",
            f"- persist_state: {self._cfg_bool('persist_state', True)}",
            f"- appraisal_mode: {self._cfg_str('appraisal_mode', 'direct')}",
            f"- persona_isolation_enabled: {self._cfg_bool('persona_isolation_enabled', True)}",
            f"- persona_stamp (fallback): {self._cfg_str('persona_stamp', '') or '(none)'}",
            f"- decay_half_life_seconds: {decay_half_life:.0f}s",
            f"- active_window_seconds: {active_window:.0f}s",
            f"- relation_ttl_seconds: {relation_ttl:.0f}s {_days(relation_ttl)}",
            f"- group_ttl_seconds: {group_ttl:.0f}s {_days(group_ttl)}",
            f"- dilution_exponent: {dilution:.2f}",
            f"- save_interval_seconds: {save_interval:.1f}s",
            f"- disabled_signals: [{', '.join(disabled)}]" if disabled
            else "- disabled_signals: (none)",
        ]
        return "\n".join(lines)

    @filter.command("emotion_state")
    async def emotion_state(self, event: AstrMessageEvent):
        """Show current emotion state for this conversation.

        Output includes the current effective config snapshot at the
        bottom so admins can verify live values without opening the
        config file.
        """
        scope = await self._scope_id(event)
        user_id = str(event.get_sender_id() or "")
        view = self.machine.get_combined(scope, user_id)
        text = format_combined_view(view) + "\n\n" + self._render_config_snapshot()
        event.set_result(event.plain_result(text))

    @filter.command("emotion_signal")
    async def emotion_signal(self, event: AstrMessageEvent):
        """Manually apply a signal: /emotion_signal praise [intensity]."""
        args = (event.message_str or "").strip().split()
        if not args:
            event.set_result(
                event.plain_result(
                    "用法：/emotion_signal <signal> [intensity]\n"
                    f"可用 signal：{', '.join(signal_names())}"
                )
            )
            return

        signal = args[0].strip().lower()
        if signal not in signal_names():
            event.set_result(
                event.plain_result(
                    f"未知 signal：{signal}\n可用 signal：{', '.join(signal_names())}"
                )
            )
            return

        if signal in self._get_disabled_signals():
            event.set_result(
                event.plain_result(
                    f"signal {signal} 已被管理员禁用，无法手动施加。"
                )
            )
            return

        intensity = 1.0
        if len(args) >= 2:
            try:
                intensity = float(args[1])
            except ValueError:
                event.set_result(event.plain_result("intensity 需要是数字，例如 0.5、1、1.5"))
                return

        scope = await self._scope_id(event)
        user_id = str(event.get_sender_id() or "")
        view = self.machine.apply_interaction(
            scope,
            user_id,
            EmotionEvent(signal=signal, intensity=intensity, reason="manual command"),
        )
        self._save_state(force=True)
        event.set_result(event.plain_result(format_combined_view(view)))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("emotion_reset")
    async def emotion_reset(self, event: AstrMessageEvent):
        """Reset current conversation emotion state."""
        scope = await self._scope_id(event)
        user_id = str(event.get_sender_id() or "")
        self.machine.reset(scope)
        view = self.machine.get_combined(scope, user_id, apply_decay=False)
        self._save_state(force=True)
        event.set_result(event.plain_result("✅ Emotion state 已重置\n\n" + format_combined_view(view)))

    @filter.command("emotion_chart")
    async def emotion_chart(self, event: AstrMessageEvent):
        """Render the current emotion state as an ASCII bar chart.

        Same data as `/emotion_state`, presented as a horizontal bar
        chart with PAD (Pleasure-Arousal-Dominance) alignment.
        """
        scope = await self._scope_id(event)
        user_id = str(event.get_sender_id() or "")
        view = self.machine.get_combined(scope, user_id)
        event.set_result(event.plain_result(format_combined_chart(view)))

    @filter.command("emotion_web")
    async def emotion_web(self, event: AstrMessageEvent):
        """Serve the WebUI state as a prompt link.

        The WebUI is a self-contained HTML page. In most AstrBot
        deployments this is served at ``/esm/``. If your deployment
        doesn't host a web server, the state JSON can be obtained
        via ``/esm/api/state`` or by calling ``get_full_state()``
        via the public API.
        """
        event.set_result(
            event.plain_result(
                "🧭 ESM WebUI: 请访问 AstrBot 管理面板的插件 WebUI 页面。\n"
                "如果未配置 Web 路由，可使用 /emotion_state 或 /emotion_chart 查看文字版。"
            )
        )

    @filter.command("emotion_prompt")
    async def emotion_prompt(self, event: AstrMessageEvent):
        """Preview the prompt block that would be injected."""
        scope = await self._scope_id(event)
        user_id = str(event.get_sender_id() or "")
        view = self.machine.get_combined(scope, user_id)
        event.set_result(event.plain_result(build_prompt_block(scope, view)))

    # ------------------------------------------------------------------
    # Public API for other plugins
    # ------------------------------------------------------------------
    #
    # Other plugins can obtain this plugin's instance via
    #     self.context.get_registered_star("astrbot_plugin_emotion_state_machine")
    # and then call the methods below. All public methods:
    #   - normalize scope / user_id inputs,
    #   - return engine-native objects (no dicts),
    #   - do NOT trigger persistence on every read (only writes do),
    #   - keep behavior identical to the built-in /commands.
    #
    # Scope convention: other plugins MUST compute the scope via get_scope()
    # (or pass a scope string that we normalize here). Computing the scope
    # ad-hoc will cause state fragmentation.

    def get_scope(self, event: AstrMessageEvent) -> str:
        """Compute the state scope key from an AstrBot event.

        Other plugins should call this to compute the same scope key the
        built-in message observer uses, otherwise reads/writes will land in
        a different scope and look invisible.
        """
        return self._scope_id(event)

    def get_combined_state(
        self,
        scope: str,
        user_id: str = "",
        *,
        apply_decay: bool = True,
    ) -> CombinedEmotionView:
        """Read the combined emotion view (group + relation + label) for a
        scope+user. An empty user_id skips the per-user relation layer."""
        norm_scope = normalize_scope(scope)
        norm_user = normalize_user_id(user_id) if user_id else ""
        return self.machine.get_combined(
            norm_scope, norm_user or None, apply_decay=apply_decay
        )

    def get_group_state(
        self,
        scope: str,
        *,
        apply_decay: bool = True,
    ) -> GroupEmotionSnapshot:
        """Read only the group-level emotion snapshot for a scope."""
        return self.machine.get_group(
            normalize_scope(scope), apply_decay=apply_decay
        )

    def get_relation_state(
        self,
        scope: str,
        user_id: str,
        *,
        apply_decay: bool = True,
    ) -> UserRelationSnapshot:
        """Read only the per-user relation snapshot for scope+user."""
        return self.machine.get_relation(
            normalize_scope(scope),
            normalize_user_id(user_id),
            apply_decay=apply_decay,
        )

    def set_appraisal_mode(self, mode: str) -> None:
        """Switch the appraisal estimator at runtime (v0.5.0+).

        Valid modes: ``"direct"`` (v0.4.0 behavior), ``"occ_static"``
        (static OCC via ``SIGNAL_APPRAISAL_PROFILES``),
        ``"occ_heuristic"`` (OCC + text/group/trust heuristics).

        The switch takes effect immediately on the next message or
        external signal call. Raises ``ValueError`` for unknown modes.
        Persists the new mode on the next save.
        """
        self.machine.set_appraisal_mode(mode)

    def observe_text(
        self,
        scope: str,
        text: str,
        *,
        user_id: str = "",
        mentioned: bool = False,
        update_relation: bool = True,
    ) -> CombinedEmotionView:
        """Infer signals from raw text and apply them to the state.

        Prefer this over apply_signal() when the caller has raw user text
        rather than a pre-classified signal name. Signals listed in the
        ``disabled_signals`` config are filtered out before application.
        Persists state.

        v0.9.51: ``update_relation=False`` skips the relation layer — use
        it when the message is not actually directed at the bot.
        """
        norm_scope = normalize_scope(scope)
        norm_user = normalize_user_id(user_id) if user_id else ""
        disabled = self._get_disabled_signals()
        view = self.machine.observe_text(
            norm_scope, text,
            user_id=norm_user or None,
            mentioned=mentioned,
            disabled_signals=disabled if disabled else None,
            update_relation=update_relation,
        )
        self._save_state()
        return view

    def _validate_intensity(self, intensity: float) -> float:
        """Coerce ``intensity`` to a finite float in ``[0.0, 2.0]``.

        Raises ``TypeError`` for non-numeric input and ``ValueError`` for
        NaN. Out-of-range finite numbers are silently clamped — this
        matches the historical behavior of ``EmotionStateMachine.apply_interaction``
        and avoids masking the caller's intent with an exception.
        """
        try:
            value = float(intensity)
        except (TypeError, ValueError) as exc:
            raise TypeError(
                f"intensity must be a number, got {intensity!r}"
            ) from exc
        # NaN is the only float that does not equal itself.
        if value != value:
            raise ValueError("intensity must be a finite number, got NaN")
        return max(0.0, min(2.0, value))

    def apply_signal(
        self,
        scope: str,
        user_id: str,
        signal: str,
        *,
        intensity: float = 1.0,
        reason: str = "external",
    ) -> CombinedEmotionView:
        """Manually apply a named signal to both group and relation layers.

        `signal` must be one of :meth:`list_signals`. Unknown names raise
        ``ValueError``; signals in the ``disabled_signals`` config also
        raise ``ValueError`` (use :meth:`is_signal_enabled` to check
        first, or call :meth:`try_apply_signal` for the safe variant).
        ``intensity`` is validated: non-numeric input raises
        ``TypeError``; NaN raises ``ValueError``; finite numbers outside
        ``[0.0, 2.0]`` are clamped. Persists state.
        """
        available = signal_names()
        if signal not in available:
            raise ValueError(
                f"Unknown signal: {signal!r}. Available: {', '.join(available)}"
            )
        if signal.lower() in self._get_disabled_signals():
            raise ValueError(
                f"Signal {signal!r} is disabled by configuration. "
                "Remove it from the disabled_signals config to enable."
            )
        norm_intensity = self._validate_intensity(intensity)
        norm_scope = normalize_scope(scope)
        norm_user = normalize_user_id(user_id)
        view = self.machine.apply_interaction(
            norm_scope,
            norm_user,
            EmotionEvent(signal=signal, intensity=norm_intensity, reason=reason),
        )
        self._save_state()
        return view

    def try_apply_signal(
        self,
        scope: str,
        user_id: str,
        signal: str,
        *,
        intensity: float = 1.0,
        reason: str = "external",
    ) -> CombinedEmotionView | None:
        """Safe variant of :meth:`apply_signal` for hot paths.

        Returns ``None`` on ``ValueError`` / ``TypeError`` (unknown signal,
        non-numeric or NaN intensity) instead of letting the exception
        propagate. The bot reply will not be broken by an invalid
        external call. A warning is logged at WARNING level.

        State is **not** persisted when the call fails. State is persisted
        exactly once on success, same as :meth:`apply_signal`.
        """
        try:
            return self.apply_signal(
                scope, user_id, signal, intensity=intensity, reason=reason
            )
        except (ValueError, TypeError) as exc:
            logger.warning(
                f"[emotion_state_machine] try_apply_signal ignored: {exc} "
                f"(scope={scope!r}, user_id={user_id!r}, signal={signal!r})"
            )
            return None

    def reset_scope(self, scope: str) -> GroupEmotionSnapshot:
        """Reset a scope entirely: clears the group snapshot AND all
        per-user relations under that scope (matches the behavior of the
        built-in /emotion_reset command). Persists state.
        """
        snap = self.machine.reset(normalize_scope(scope))
        # v0.10.0+: also drop the per-scope TalkWillingness accumulator
        # and user-message trackers — they no longer have a backing
        # emotion state to drive, and leaving them around would silently
        # re-create scope entries on the next apply_self_reply_signal.
        self._cleanup_self_reply_tracking(normalize_scope(scope))
        self._save_state(force=True)
        return snap

    def prune_cold_state(self) -> dict[str, int]:
        """Prune cold groups and relations across all scopes.

        Returns ``{"groups_pruned": int, "relations_pruned": int}``.
        Persists state only when at least one entry was actually pruned
        (avoids needless disk I/O for a no-op maintenance call). Pair
        this with a scheduled task (e.g. once per day) to bound the
        on-disk state file size for long-running bots.
        """
        result = self.machine.prune_cold_state()
        if result["groups_pruned"] > 0 or result["relations_pruned"] > 0:
            self._save_state(force=True)
            logger.info(
                f"[emotion_state_machine] prune_cold_state: "
                f"groups={result['groups_pruned']}, "
                f"relations={result['relations_pruned']}"
            )
        return result

    def force_decay(
        self,
        scope: str,
        *,
        now: float | None = None,
    ) -> GroupEmotionSnapshot:
        """Force an immediate decay pass on a scope's group snapshot and
        persist. Useful for plugins that want to bound 'staleness' before
        reading state.

        `now` is the reference timestamp for the decay calculation; defaults
        to `time.time()`. Pass an explicit value to deterministically
        advance the clock (e.g. in tests or when replaying buffered
        events).
        """
        snap = self.machine.decay(normalize_scope(scope), now=now)
        self._save_state()
        return snap

    def build_prompt_block(self, scope: str, user_id: str = "") -> str:
        """Build the same prompt block the built-in LLM injector inserts.

        Other plugins that assemble their own system prompts can call this
        to embed an identical block without going through the standard
        on_llm_request hook (e.g. when injecting into a judge model instead
        of the main reply model).

        v0.10.0+: honors the ``emotion_block_template`` config the same
        way the built-in ``on_llm_request`` injector does. Previously
        this method silently bypassed the template override — calling
        code that wanted custom rendering had to reach into
        :meth:`to_text_part` or duplicate the template lookup. With this
        fix, both entry points produce byte-identical output.
        """
        view = self.get_combined_state(scope, user_id)
        template = self._cfg_str("emotion_block_template", "") or None
        return build_prompt_block(
            normalize_scope(scope), view, template=template,
        )

    def to_text_part(self, scope: str, user_id: str = "") -> TextPart:
        """Return the emotion block as a ``TextPart`` ready for direct
        injection into ``request.extra_user_content_parts``.

        v0.10.0+ — counterpart to :meth:`build_prompt_block` that returns
        a ``TextPart`` instead of a raw string. Other plugins (e.g.
        ``social_context`` judge channel) should prefer this over string
        concatenation so the emotion block arrives as an independent
        ``TextPart`` rather than getting spliced into another plugin's
        ``TextPart`` body.

        Honors the ``emotion_block_template`` config the same way the
        built-in ``on_llm_request`` does. The returned ``TextPart`` is
        ``mark_as_temp()`` so it is sent to the LLM but not persisted
        to conversation history — mirrors the v0.9.59 fix for the
        built-in injector.

        Example::

            extra_parts.append(plugin.to_text_part(scope, user_id))
        """
        view = self.get_combined_state(scope, user_id)
        template = self._cfg_str("emotion_block_template", "") or None
        block = build_prompt_block(
            normalize_scope(scope), view, template=template,
        )
        return TextPart(text=block, type="text").mark_as_temp()

    # ------------------------------------------------------------------
    # Self-reply signal API (v0.10.0+)
    # ------------------------------------------------------------------

    async def apply_self_reply_signal(self, event: AstrMessageEvent) -> bool:
        """Called by ``social_context`` (or any other plugin that decides
        the bot should reply proactively) immediately after the bot
        decides to speak. Consults :class:`TalkWillingnessState` to
        decide whether to actually apply a ``self_reply`` signal to
        the bot's emotion state, and applies it if so.

        :returns: True iff a self-reply signal was actually applied.
            Returns False (silently) for any of:

            - ``self_reply_signal_enabled`` is False
            - the event was triggered by a user @-wake (not proactive)
            - TalkWillingnessState decided "no" (outside trigger zone,
              in reversal zone, or consecutive-apply cap hit)
            - the configured signal is in ``disabled_signals``
            - ``try_apply_signal`` raised (caught and logged at debug)

        No exception ever escapes — failures here must not break
        ``social_context``'s reply flow.
        """
        if not self._cfg_bool("self_reply_signal_enabled", True):
            return False
        # Defense-in-depth: if the caller invokes this for a user-@
        # triggered reply, ignore. Real path is proactive (judge=yes
        # without user @).
        if getattr(event, "is_at_or_wake_command", False):
            return False

        try:
            scope = await self._scope_id(event)
        except Exception as exc:
            logger.debug(
                f"[emotion_state_machine] apply_self_reply_signal: "
                f"_scope_id failed: {exc!r}"
            )
            return False
        user_id = str(event.get_sender_id() or "")
        now = time.time()

        # Compute inputs for TalkWillingnessState.tick().
        last_user = self._last_user_msg_ts.get(scope, 0.0)
        # "User spoke in the same tick" means we just observed them
        # observe_message hook ran before this call — i.e. the user
        # message that prompted social_context's judge to fire.
        user_msg_arrived = last_user > 0 and (now - last_user) < 5.0
        # Count user turns in the trailing 5-min window.
        buf = self._user_turn_ts.get(scope, [])
        cutoff = now - 300.0
        user_turns_recent = sum(1 for ts in buf if ts >= cutoff)

        # Read the group snapshot for emotion factor. Decay first so
        # the values fed to TalkWillingness are the same the user
        # would see in /emotion_state.
        try:
            self.machine.decay_group(scope)
            group_view = self.machine.groups.get(scope)
        except Exception:
            group_view = None
        if group_view is None:
            # Scope was pruned between observe_message and this call.
            return False

        try:
            _, should_apply, intensity = self._talk_willingness.tick(
                scope=scope,
                now=now,
                user_message_arrived=user_msg_arrived,
                group_view=group_view,
                user_turns_in_5min=user_turns_recent,
            )
        except Exception as exc:
            logger.debug(
                f"[emotion_state_machine] TalkWillingness.tick failed: "
                f"{exc!r}"
            )
            return False

        if not should_apply:
            return False

        signal = self._cfg_str("self_reply_signal", "self_reply")
        # Skip if the configured signal is in the disabled list — same
        # defense apply_signal would have, but checked upfront so we
        # don't charge consecutive_apply for nothing.
        if signal.lower() in self._get_disabled_signals():
            return False

        try:
            self.try_apply_signal(
                scope=scope, user_id=user_id,
                signal=signal, intensity=intensity,
                reason="esm_self_reply",
            )
        except Exception as exc:
            logger.debug(
                f"[emotion_state_machine] try_apply_signal(self_reply) "
                f"failed: {exc!r}"
            )
            return False
        return True

    def _cleanup_self_reply_tracking(self, scope: str) -> None:
        """Drop per-scope trackers when an ESM scope is deleted.

        Called from the public ``reset_scope`` (and the POST
        ``/<plugin>/delete/<scope>`` HTTP handler). Bounded cleanup —
        both dicts grow only with active scopes, so on_scope_deleted
        keeps them in sync with the emotion state machine's group map.

        Defensive against test fixtures built via ``__new__`` that
        bypass ``__init__``: skips tracking cleanup if the attributes
        don't exist yet.
        """
        tw = getattr(self, "_talk_willingness", None)
        if tw is not None:
            tw.on_scope_deleted(scope)
        tracker = getattr(self, "_last_user_msg_ts", None)
        if tracker is not None:
            tracker.pop(scope, None)
        turn_log = getattr(self, "_user_turn_ts", None)
        if turn_log is not None:
            turn_log.pop(scope, None)

    def _get_bot_energy(self) -> float:
        """v0.10.0+ optional hook: query a registered "energy" star for
        the bot's current energy reading.

        Returns a value in ``[0.0, 1.0]``. Convention:

        - ``1.0`` means "fully rested / neutral" → no modulation on
          self-reply accumulation (TalkWillingness treats 1.0 as the
          identity multiplier).
        - ``0.0`` means "fully depleted" → TalkWillingness halves the
          accumulation rate so bot is less likely to self-reply when
          tired.

        If no ``astrbot_plugin_energy_system`` star is registered, this
        returns ``1.0`` (neutral) so behavior is bit-identical to
        pre-extension versions. Any exception during lookup is caught
        and falls back to 1.0 — the energy hook must never break the
        self-reply decision path.
        """
        # Lazy import to avoid a hard dependency on a specific plugin
        # name at module load time.
        try:
            energy_star = self.context.get_registered_star(
                "astrbot_plugin_energy_system",
            )
        except Exception:
            return 1.0
        if energy_star is None:
            return 1.0
        # Convention: the energy star exposes a `get_bot_energy()` method
        # returning a float in [0, 1]. If it doesn't, fall back to 1.0.
        getter = getattr(energy_star, "get_bot_energy", None)
        if not callable(getter):
            return 1.0
        try:
            val = float(getter())
        except Exception:
            return 1.0
        if not math.isfinite(val):
            return 1.0
        return max(0.0, min(1.0, val))

    def render_state_text(self, scope: str, user_id: str = "") -> str:
        """Human-readable rendering of the current state, identical to the
        /emotion_state command output. Useful for plugin debug/log lines."""
        view = self.get_combined_state(scope, user_id)
        return format_combined_view(view)

    def list_signals(self) -> list[str]:
        """Names of signals the engine understands. Use this to validate
        signal arguments before calling apply_signal()."""
        return signal_names()

    def is_signal_enabled(self, signal: str) -> bool:
        """Check whether a signal is currently enabled (not in the
        ``disabled_signals`` config). The check is case-insensitive.

        Returns ``False`` for unknown signals as a defensive default —
        callers should usually validate against :meth:`list_signals`
        first.
        """
        if signal not in signal_names():
            return False
        return signal.lower() not in self._get_disabled_signals()

    def list_disabled_signals(self) -> list[str]:
        """The currently-disabled signal names (lowercased), as a list."""
        return sorted(self._get_disabled_signals())

    def _register_official_page_api_if_available(self) -> None:
        """Register plugin Web APIs on the AstrBot Dashboard.

        v0.9.0: cleaned up — diagnostic logging removed now that the
        registration flow is verified to work. Routes are registered
        directly in the Star's ``__init__`` per the official
        AstrBot plugin-pages guide.
        """
        if not hasattr(self.context, "register_web_api"):
            return
        _PLUGIN_NAME = "astrbot_plugin_emotion_state_machine"

        async def health():
            machine = self.machine
            hidden_user_raw = self._cfg_str("hidden_user_ids", "webchat")
            hidden_users = [s.strip().lower() for s in hidden_user_raw.split(",") if s.strip()]
            hidden_scope_raw = self._cfg_str("hidden_scope_patterns", "webchat:")
            hidden_scopes = [s.strip().lower() for s in hidden_scope_raw.split(",") if s.strip()]
            return {
                "version": _ESM_VERSION,
                "appraisal_mode": machine.appraisal_mode,
                "signal_count": len(signal_names()),
                "scope_count": len(machine.groups),
                "hidden_user_ids": hidden_users,
                "hidden_scope_patterns": hidden_scopes,
                # v0.9.49: expose the configured default for the frontend's
                # filterBot toggle so schema defaults take effect on first
                # visit (otherwise hidden_user_ids/hidden_scope_patterns
                # never apply — shouldShowGroup/shouldShowUser gate them
                # behind settings.filterBot, which defaults to false).
                "filter_bot_default": self._cfg_bool("filter_bot_default", True),
                "active_window_seconds": machine.active_window_seconds,
                "bot_persona": self._bot_persona_name(),
            }

        async def full_state():
            return get_full_state(self.machine)

        async def scope_detail(scope: str):
            state = get_full_state(self.machine)
            for s in state["scopes"]:
                if s["scope"] == scope:
                    return s
            return {"error": "scope not found", "scope": scope}

        async def scope_delete(scope: str):
            """v0.9.29: remove an entire scope and its relations.
            v0.10.0+: also drops the per-scope TalkWillingness
            accumulator and user-message trackers so they don't
            silently re-create entries on the next
            apply_self_reply_signal call.
            """
            normalized = normalize_scope(scope)
            deleted = False
            if normalized in self.machine.groups:
                del self.machine.groups[normalized]
                deleted = True
            if normalized in self.machine.relations:
                del self.machine.relations[normalized]
                deleted = True
            # Always run cleanup — even when the scope wasn't found in
            # the engine, stale tracker entries may exist (e.g. user
            # observed a message but the engine never persisted the
            # scope). Defensive cleanup keeps the dicts bounded.
            self._cleanup_self_reply_tracking(normalized)
            if deleted:
                self._save_state(force=True)
                return {"deleted": scope}
            return {"error": "scope not found", "scope": scope}

        try:
            self.context.register_web_api(
                f"/{_PLUGIN_NAME}/health", health, ["GET"], "ESM health",
            )
            self.context.register_web_api(
                f"/{_PLUGIN_NAME}/state", full_state, ["GET"], "ESM state",
            )
            self.context.register_web_api(
                f"/{_PLUGIN_NAME}/state/<path:scope>", scope_detail,
                ["GET"], "ESM single scope detail",
            )
            # v0.9.32: use <path:scope> — scope names contain ":" which
            # Werkzeug treats as path separators. <path:> captures
            # multi-segment values (matches engram's pattern of
            # reading the scope from the JSON body instead).
            self.context.register_web_api(
                f"/{_PLUGIN_NAME}/delete/<path:scope>", scope_delete,
                ["POST"], "Delete a scope and its relations",
            )
        except Exception as e:
            logger.warning(
                f"[emotion_state_machine] register_web_api raised: {e!r}"
            )

    async def terminate(self):
        self._save_state(force=True)

    # ------------------------------------------------------------------
    # WebUI (v0.7.0+)
    # ------------------------------------------------------------------

    def register_web_routes(self, router) -> None:
        """Register ESM WebUI routes on the AstrBot web router.

        Expected to be called once during plugin initialization (after
        the web server is ready). ``router`` should expose
        ``.get(path, handler)`` and ``.add_static(...)``.
        Two routes are registered:

        - ``GET /esm/`` — serves a self-contained HTML dashboard.
        - ``GET /esm/api/state`` — returns the full emotion state as
          a compact JSON response.
        """
        plugin = self

        async def _page(_request):
            from astrbot.api.web import HTMLResponse
            return HTMLResponse(render_webui_page())

        async def _api_state(_request):
            from astrbot.api.web import JSONResponse
            return JSONResponse(json.loads(render_state_json(plugin.machine)))

        try:
            router.get("/esm/", _page)
            router.get("/esm/api/state", _api_state)
        except Exception as exc:
            logger.warning(
                f"[emotion_state_machine] failed to register web routes: {exc}"
            )

    def get_webui_page(self) -> str:
        """Return the complete WebUI as an HTML string.

        Can be served directly by any web framework. Use this in
        custom deployments where AstrBot's built-in router is not
        available.
        """
        return render_webui_page()

    def get_state_json(self) -> str:
        """Return the full emotion state as a compact JSON string.

        One-shot dump of all groups + relations. Suitable for
        external monitoring tools or custom dashboards.
        """
        return render_state_json(self.machine)


# Back-compat alias for v0.8.13 rename.
EmotionStateMachinePlugin = EmotionStateMachineStar
