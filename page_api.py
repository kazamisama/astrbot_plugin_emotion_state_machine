"""Dashboard WebUI page API for astrbot_plugin_emotion_state_machine.

Inspired by astrbot_plugin_engram_core/page_api.py. Same shape, same
registration pattern, same path prefix. The plugin main calls
`_register_official_page_api_if_available()` once at startup; this
class wires GET endpoints to the in-process handlers.

AstrBot invokes registered handlers as `await view_handler(**path_vars)`
(see dashboard/server.py:srv_plug_route). It passes *no* query string
or JSON body to the callable, so every handler here is an async wrapper
that reads parameters from `quart.request` directly.

API prefix: /astrbot_plugin_emotion_state_machine/page
Endpoints:
  GET  /health           -> {version, appraisal_mode, scope_count}
  GET  /state            -> get_full_state(machine)
  GET  /state/<scope>    -> single scope detail
"""
from __future__ import annotations
import os
import sys
from typing import Any

# Plugin dir on sys.path so sibling emotion_engine resolves when AstrBot
# imports this module under the plugin package.
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
    """Lightweight facade mirroring engram's structure."""

    def __init__(self, plugin) -> None:
        self.plugin = plugin

    def register_routes(self) -> None:
        """Register endpoints. Matches engram's exact pattern."""
        register = self.plugin.context.register_web_api

        register(f"{PAGE_API_PREFIX}/health", self._health,
                 ["GET"], "ESM health probe")
        register(f"{PAGE_API_PREFIX}/state", self._full_state,
                 ["GET"], "ESM full emotion state (all scopes)")
        register(f"{PAGE_API_PREFIX}/state/<scope>", self._scope_detail,
                 ["GET"], "ESM single scope detail")

    # ---------- async route handlers ----------

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