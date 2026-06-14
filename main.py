"""
AstrBot Emotion State Machine

A lightweight plugin that simulates the bot's emotional state as a decaying
state machine.  It can observe conversation signals, expose debug commands, and
inject a compact emotion block before LLM requests.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig

try:
    from .emotion_engine import (
        CombinedEmotionView,
        EmotionEvent,
        EmotionStateMachine,
        GroupEmotionSnapshot,
        UserRelationSnapshot,
        build_prompt_block,
        format_combined_view,
        normalize_scope,
        normalize_user_id,
        signal_names,
    )
except ImportError:  # pragma: no cover - allow direct script imports in tests
    from emotion_engine import (
        CombinedEmotionView,
        EmotionEvent,
        EmotionStateMachine,
        GroupEmotionSnapshot,
        UserRelationSnapshot,
        build_prompt_block,
        format_combined_view,
        normalize_scope,
        normalize_user_id,
        signal_names,
    )


class EmotionStateMachinePlugin(Star):
    """Simulate bot emotion state per conversation scope."""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = self._resolve_data_dir()
        self.state_path = self._resolve_state_path()
        self.machine = EmotionStateMachine(
            decay_half_life_seconds=self._cfg_float("decay_half_life_seconds", 900.0, 1.0),
            active_window_seconds=self._cfg_float("active_window_seconds", 300.0, 1.0),
            relation_ttl_seconds=self._cfg_float("relation_ttl_seconds", 604800.0, 1.0),
            group_ttl_seconds=self._cfg_float("group_ttl_seconds", 2592000.0, 1.0),
            dilution_exponent=self._cfg_float("dilution_exponent", 0.5, 0.0),
        )
        self._last_save_time = 0.0
        self._load_state()

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
        if min_value is not None:
            value = max(min_value, value)
        return value

    def _cfg_int(self, key: str, default: int, min_value: int | None = None) -> int:
        try:
            value = int(self.config.get(key, default))
        except (TypeError, ValueError):
            value = default
        if min_value is not None:
            value = max(min_value, value)
        return value

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

    def _scope_id(self, event: AstrMessageEvent) -> str:
        return event.get_group_id() or event.unified_msg_origin or "_private"

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

        scope = self._scope_id(event)
        user_id = str(event.get_sender_id())
        mentioned = bool(getattr(event, "is_at_or_wake_command", False))
        view = self.machine.observe_text(scope, text, user_id=user_id, mentioned=mentioned)
        logger.debug(
            "[emotion_state_machine] observed message | "
            f"scope={scope} user={user_id} group_label={view.group.label} combined_label={view.label}"
        )
        self._save_state()

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, request):
        """Inject a compact emotion state block before LLM requests."""
        if not self._cfg_bool("enabled", True):
            return
        if not self._cfg_bool("inject_enabled", True):
            return
        if not hasattr(request, "system_prompt"):
            return
        if self._cfg_bool("only_group", True) and not event.get_group_id():
            return

        scope = self._scope_id(event)
        user_id = str(event.get_sender_id() or "")
        view = self.machine.get_combined(scope, user_id)
        block = build_prompt_block(scope, view)
        request.system_prompt = (request.system_prompt or "").rstrip() + "\n\n" + block

    @filter.command("emotion_state")
    async def emotion_state(self, event: AstrMessageEvent):
        """Show current emotion state for this conversation."""
        scope = self._scope_id(event)
        user_id = str(event.get_sender_id() or "")
        view = self.machine.get_combined(scope, user_id)
        event.set_result(event.plain_result(format_combined_view(view)))

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

        intensity = 1.0
        if len(args) >= 2:
            try:
                intensity = float(args[1])
            except ValueError:
                event.set_result(event.plain_result("intensity 需要是数字，例如 0.5、1、1.5"))
                return

        scope = self._scope_id(event)
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
        scope = self._scope_id(event)
        user_id = str(event.get_sender_id() or "")
        self.machine.reset(scope)
        view = self.machine.get_combined(scope, user_id, apply_decay=False)
        self._save_state(force=True)
        event.set_result(event.plain_result("✅ Emotion state 已重置\n\n" + format_combined_view(view)))

    @filter.command("emotion_prompt")
    async def emotion_prompt(self, event: AstrMessageEvent):
        """Preview the prompt block that would be injected."""
        scope = self._scope_id(event)
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

    def observe_text(
        self,
        scope: str,
        text: str,
        *,
        user_id: str = "",
        mentioned: bool = False,
    ) -> CombinedEmotionView:
        """Infer signals from raw text and apply them to the state.

        Prefer this over apply_signal() when the caller has raw user text
        rather than a pre-classified signal name. Persists state.
        """
        norm_scope = normalize_scope(scope)
        norm_user = normalize_user_id(user_id) if user_id else ""
        view = self.machine.observe_text(
            norm_scope, text, user_id=norm_user or None, mentioned=mentioned
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

        `signal` must be one of `list_signals()`. Unknown names raise
        ``ValueError``. ``intensity`` is validated: non-numeric input
        raises ``TypeError``; NaN raises ``ValueError``; finite numbers
        outside ``[0.0, 2.0]`` are clamped. Persists state.

        For message-handling hot paths where you don't want an invalid
        signal to break the bot's reply, prefer :meth:`try_apply_signal`.
        """
        available = signal_names()
        if signal not in available:
            raise ValueError(
                f"Unknown signal: {signal!r}. Available: {', '.join(available)}"
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
        """
        view = self.get_combined_state(scope, user_id)
        return build_prompt_block(normalize_scope(scope), view)

    def render_state_text(self, scope: str, user_id: str = "") -> str:
        """Human-readable rendering of the current state, identical to the
        /emotion_state command output. Useful for plugin debug/log lines."""
        view = self.get_combined_state(scope, user_id)
        return format_combined_view(view)

    def list_signals(self) -> list[str]:
        """Names of signals the engine understands. Use this to validate
        signal arguments before calling apply_signal()."""
        return signal_names()

    async def terminate(self):
        self._save_state(force=True)
