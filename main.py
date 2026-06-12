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
        EmotionEvent,
        EmotionStateMachine,
        build_prompt_block,
        format_combined_view,
        signal_names,
    )
except ImportError:  # pragma: no cover - allow direct script imports in tests
    from emotion_engine import (
        EmotionEvent,
        EmotionStateMachine,
        build_prompt_block,
        format_combined_view,
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
        )
        self._last_save_time = 0.0
        self._load_state()

    def _cfg_bool(self, key: str, default: bool) -> bool:
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

    async def terminate(self):
        self._save_state(force=True)
