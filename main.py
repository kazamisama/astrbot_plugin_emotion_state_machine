"""
AstrBot Emotion State Machine

A lightweight plugin that simulates the bot's emotional state as a decaying
state machine.  It can observe conversation signals, expose debug commands, and
inject a compact emotion block before LLM requests.
"""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
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
        # Register Dashboard routes FIRST so they remain available even
        # if _load_state() raises (e.g. corrupt JSON). The handlers
        # reference self.machine which is created above.
        self._register_official_page_api_if_available()
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

    def _scope_id(self, event: AstrMessageEvent) -> str:
        base = event.get_group_id() or event.unified_msg_origin or "_private"
        stamp = self._cfg_str("persona_stamp", "")
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

        scope = self._scope_id(event)
        user_id = str(event.get_sender_id())
        mentioned = bool(getattr(event, "is_at_or_wake_command", False))
        # Filter the engine's inferred signals against the disabled list
        # before applying, so disabled signals never enter the state.
        disabled = self._get_disabled_signals()
        view = self.machine.observe_text(
            scope, text, user_id=user_id, mentioned=mentioned,
            disabled_signals=disabled if disabled else None,
        )
        logger.debug(
            "[emotion_state_machine] observed message | "
            f"scope={scope} user={user_id} group_label={view.group.label} combined_label={view.label}"
        )
        self._save_state()

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, request):
        """Inject a compact emotion state block before LLM requests.

        Uses sentinel-wrapped blocks so re-injection replaces the prior
        block in place instead of stacking duplicates. Safe to call
        multiple times on the same request.
        """
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
            f"- persona_stamp: {self._cfg_str('persona_stamp', '') or '(none — shared across personas)'}",
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
        scope = self._scope_id(event)
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

    @filter.command("emotion_chart")
    async def emotion_chart(self, event: AstrMessageEvent):
        """Render the current emotion state as an ASCII bar chart.

        Same data as `/emotion_state`, presented as a horizontal bar
        chart with PAD (Pleasure-Arousal-Dominance) alignment.
        """
        scope = self._scope_id(event)
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
    ) -> CombinedEmotionView:
        """Infer signals from raw text and apply them to the state.

        Prefer this over apply_signal() when the caller has raw user text
        rather than a pre-classified signal name. Signals listed in the
        ``disabled_signals`` config are filtered out before application.
        Persists state.
        """
        norm_scope = normalize_scope(scope)
        norm_user = normalize_user_id(user_id) if user_id else ""
        disabled = self._get_disabled_signals()
        view = self.machine.observe_text(
            norm_scope, text,
            user_id=norm_user or None,
            mentioned=mentioned,
            disabled_signals=disabled if disabled else None,
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
                # v0.9.21 fix: signal_count must be the number of distinct
                # signal TYPES (13), not the number of groups (which
                # equalled scope_count and made the stat card useless).
                "signal_count": len(signal_names()),
                "scope_count": len(machine.groups),
                "hidden_user_ids": hidden_users,
                "hidden_scope_patterns": hidden_scopes,
                "active_window_seconds": machine.active_window_seconds,
            }

        async def full_state():
            return get_full_state(self.machine)

        async def scope_detail(scope: str):
            state = get_full_state(self.machine)
            for s in state["scopes"]:
                if s["scope"] == scope:
                    return s
            return {"error": "scope not found", "scope": scope}

        try:
            self.context.register_web_api(
                f"/{_PLUGIN_NAME}/health", health, ["GET"], "ESM health",
            )
            self.context.register_web_api(
                f"/{_PLUGIN_NAME}/state", full_state, ["GET"], "ESM state",
            )
            self.context.register_web_api(
                f"/{_PLUGIN_NAME}/state/<scope>", scope_detail,
                ["GET"], "ESM single scope detail",
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
