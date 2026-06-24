"""Dashboard WebUI page API for astrbot_plugin_emotion_state_machine.

Registers REST endpoints under ``/astrbot_plugin_emotion_state_machine/page/``
that the AstrBot Dashboard frontend calls. Minimal surface — three endpoints
cover the full state, a single scope, and a health probe.

The ``register`` function is injected at runtime by AstrBot's dashboard
server (``star.Context.register_web_api``). On older AstrBot versions that
don't expose it, the caller silently skips registration.
"""

from __future__ import annotations

import sys
import os
from typing import Any

# Plugin dir on sys.path so sibling emotion_engine resolves.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from emotion_engine import get_full_state, __version__  # type: ignore[import-untyped]

PLUGIN_NAME = "astrbot_plugin_emotion_state_machine"
PAGE_API_PREFIX = f"/{PLUGIN_NAME}/page"


async def _query_args() -> dict:
    """Read query-string args from the active quart request."""
    try:
        from quart import request
        return dict(request.args)
    except Exception:
        return {}


class PluginPageApi:
    """Lightweight facade. No separate handler modules needed — the
    emotion engine's ``get_full_state`` returns everything in one dict.
    """

    def __init__(self, plugin) -> None:
        self.plugin = plugin

    def register_routes(self) -> None:
        register = self.plugin.context.register_web_api

        register(f"{PAGE_API_PREFIX}/health", self._health,
                 ["GET"], "ESM health + version info")
        register(f"{PAGE_API_PREFIX}/state", self._full_state,
                 ["GET"], "ESM full emotion state (all scopes)")
        register(f"{PAGE_API_PREFIX}/state/<scope>", self._scope_detail,
                 ["GET"], "ESM single scope detail")

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
