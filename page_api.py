"""Dashboard WebUI page API for astrbot_plugin_emotion_state_machine.

Registers REST endpoints under ``/astrbot_plugin_emotion_state_machine/page/``
that the AstrBot Dashboard frontend (loaded via the bridge SDK in
``window.AstrBotPluginPage``) calls. Three endpoints cover everything:
``/health`` (probe), ``/state`` (full dump), ``/state/<scope>``
(single scope).

Two registration backends are tried in order:

1. ``context.register_web_api`` — the canonical AstrBot Dashboard API.
   This is what ``astrbot_plugin_engram_core`` uses.
2. ``astrbot.api.star.register`` — module-level fallback for older
   AstrBot versions that exposed the function at module scope instead
   of binding it on the context. Same signature, same effect.

If both fail, the caller (``main._register_official_page_api_if_available``)
logs a warning so the operator can see why the WebUI is offline.
"""

from __future__ import annotations

import os
import sys
from typing import Any

# Plugin dir on sys.path so sibling emotion_engine resolves.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from emotion_engine import get_full_state, __version__  # type: ignore[import-untyped]


PLUGIN_NAME = "astrbot_plugin_emotion_state_machine"
PAGE_API_PREFIX = f"/{PLUGIN_NAME}/page"


def _resolve_register(plugin) -> tuple[Any, str] | None:
    """Return a callable ``register(path, handler, methods, desc)``,
    or ``None`` if neither backend is available.

    Tries ``context.register_web_api`` first (matches engram's
    pattern). Falls back to ``astrbot.api.star.register`` for older
    AstrBot versions. Returns a 2-tuple ``(callable, source)`` so the
    caller can log which backend was used.
    """
    ctx = getattr(plugin, "context", None)
    if ctx is not None and hasattr(ctx, "register_web_api"):
        return ctx.register_web_api, "context.register_web_api"
    try:
        from astrbot.api.star import register as star_register
        return star_register, "astrbot.api.star.register"
    except Exception:
        return None


class PluginPageApi:
    """Lightweight facade. No separate handler modules needed — the
    emotion engine's ``get_full_state`` returns everything in one dict.
    """

    def __init__(self, plugin) -> None:
        self.plugin = plugin

    def register_routes(self) -> None:
        """Register the three page endpoints under PAGE_API_PREFIX.

        Logs each path at INFO level so operators can verify what got
        registered. Throws if no register backend is available; the
        caller in main.py catches and logs a WARNING.
        """
        resolved = _resolve_register(self.plugin)
        if resolved is None:
            raise RuntimeError(
                "no register_web_api on context and astrbot.api.star.register "
                "is not importable — Dashboard integration unavailable on "
                "this AstrBot version"
            )
        register, source = resolved
        import logging
        log = logging.getLogger("astrbot.emotion_state_machine")

        routes = [
            (f"{PAGE_API_PREFIX}/health", self._health, ["GET"],
             "ESM health + version info"),
            (f"{PAGE_API_PREFIX}/state", self._full_state, ["GET"],
             "ESM full emotion state (all scopes)"),
            (f"{PAGE_API_PREFIX}/state/<scope>", self._scope_detail,
             ["GET"], "ESM single scope detail"),
        ]
        for path, handler, methods, desc in routes:
            register(path, handler, methods, desc)
            log.info(
                "[emotion_state_machine] registered %s [%s] via %s",
                path, ",".join(methods), source,
            )

    # ---------- handlers ----------

    async def _health(self) -> dict[str, Any]:
        machine = self.plugin.machine
        return {
            "version": __version__,
            "appraisal_mode": machine.appraisal_mode,
            "signal_count": len(getattr(machine, "groups", {})),
            "scope_count": len(machine.groups),
        }

    async def _full_state(self) -> dict[str, Any]:
        return get_full_state(self.plugin.machine)

    async def _scope_detail(self, scope: str) -> dict[str, Any]:
        state = get_full_state(self.plugin.machine)
        for s in state["scopes"]:
            if s["scope"] == scope:
                return s
        return {"error": "scope not found", "scope": scope}