"""Dashboard WebUI page API for astrbot_plugin_emotion_state_machine.

Registers REST endpoints under ``/astrbot_plugin_emotion_state_machine/page/``
that the AstrBot Dashboard frontend (loaded via the bridge SDK in
``window.AstrBotPluginPage``) calls. Three endpoints cover everything:
``/health`` (probe), ``/state`` (full dump), ``/state/<scope>``
(single scope).

Version note
-------------

engram-core registers routes as ``f"/{PLUGIN_NAME}/page/{name}"``
(full plugin-prefixed path). Some AstrBot versions of ``register_web_api``
add the plugin prefix internally, in which case the full path produces
a doubled prefix and the bridge can't find the route. To handle both
shapes, we register each endpoint under TWO paths:

- the plugin-prefixed path (matches engram's convention)
- the short ``/page/...`` path (fallback if the bridge's prefix differs)

If both succeed, the bridge queries hit whichever path matches its
own prefix shape. If only one succeeds, the warning log will say which.

If neither backend is available, registration is skipped and the
inline WebUI at ``/esm/`` remains as the fallback.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

# Plugin dir on sys.path so sibling emotion_engine resolves.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from emotion_engine import get_full_state, __version__  # type: ignore[import-untyped]


PLUGIN_NAME = "astrbot_plugin_emotion_state_machine"
PAGE_API_PREFIX = f"/{PLUGIN_NAME}/page"
# Use AstrBot's own logger so messages flow through the user's
# configured log filter. Per-route registration success lines are
# logged at WARNING so they show up in the default console.
try:
    from astrbot.api import logger as LOG  # type: ignore
except Exception:  # pragma: no cover - test fallback
    import logging as _logging
    LOG = _logging.getLogger("astrbot.emotion_state_machine")


def _resolve_register(plugin) -> tuple[Any, str] | None:
    """Return a callable ``register(path, handler, methods, desc)``.

    Only accepts ``context.register_web_api`` — that's the canonical
    AstrBot Dashboard API. The module-level ``astrbot.api.star.register``
    is for LLM tool registration (different signature, different
    purpose) and would silently no-op or error if called with web-API
    arguments.
    """
    ctx = getattr(plugin, "context", None)
    if ctx is not None and hasattr(ctx, "register_web_api"):
        return ctx.register_web_api, "context.register_web_api"
    return None


class PluginPageApi:
    """Lightweight facade. No separate handler modules needed — the
    emotion engine's ``get_full_state`` returns everything in one dict.
    """

    def __init__(self, plugin) -> None:
        self.plugin = plugin

    def register_routes(self) -> None:
        """Register the three page endpoints under both path shapes.

        Logs each successful registration at INFO and each failure at
        WARNING. Throws only if NO registration at all is possible
        (which the caller in main.py catches).
        """
        resolved = _resolve_register(self.plugin)
        if resolved is None:
            raise RuntimeError(
                "context.register_web_api not available — Dashboard "
                "integration requires a newer AstrBot"
            )
        register, source = resolved
        LOG.warning(
            "[emotion_state_machine] registering Dashboard routes via %s",
            source,
        )

        handlers = [
            ("/health", self._health, ["GET"], "ESM health probe"),
            ("/state", self._full_state, ["GET"],
             "ESM full emotion state"),
            ("/state/<scope>", self._scope_detail, ["GET"],
             "ESM single scope detail"),
        ]
        # Register each endpoint under BOTH path shapes to maximize
        # compatibility across AstrBot versions.
        prefix_variants = [PAGE_API_PREFIX, "/page"]
        ok_count = 0
        for sub_path, handler, methods, desc in handlers:
            for prefix in prefix_variants:
                full_path = prefix + sub_path
                try:
                    register(full_path, handler, methods, desc)
                    LOG.warning(
                        "[emotion_state_machine]   registered %s [%s]",
                        full_path, ",".join(methods),
                    )
                    ok_count += 1
                except Exception as exc:
                    LOG.warning(
                        "[emotion_state_machine]   failed %s: %r",
                        full_path, exc,
                    )
        if ok_count == 0:
            raise RuntimeError(
                "all route registrations failed — check AstrBot version"
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