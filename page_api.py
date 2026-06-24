"""Dashboard WebUI page API for astrbot_plugin_emotion_state_machine.

Registers REST endpoints under ``/astrbot_plugin_emotion_state_machine/page/``
that the AstrBot Dashboard frontend (loaded via the bridge SDK in
``window.AstrBotPluginPage``) calls. Three endpoints cover everything:
``/health`` (probe), ``/state`` (full dump), ``/state/<scope>``
(single scope).

Two path shapes are registered (defensive — different AstrBot versions
prefix the path differently):

- ``/astrbot_plugin_emotion_state_machine/page/{name}`` — plugin-prefixed
- ``/page/{name}`` — short form

If both fail, registration is skipped and the inline WebUI at ``/esm/``
remains as the fallback.

Diagnostic logging uses ``print()`` to stdout in addition to the
AstrBot logger, because some AstrBot log filter configurations drop
INFO/DEBUG from third-party loggers. The print lines are prefixed
``[EMT-DBG]`` so they're easy to grep.
"""

from __future__ import annotations

import os
import sys
import traceback
from typing import Any

# Plugin dir on sys.path so sibling emotion_engine resolves.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from emotion_engine import get_full_state, __version__  # type: ignore[import-untyped]


PLUGIN_NAME = "astrbot_plugin_emotion_state_machine"
PAGE_API_PREFIX = f"/{PLUGIN_NAME}/page"


def _diag(msg: str) -> None:
    """Diagnostic write. Tries: print → stderr → AstrBot logger → file.

    We use ALL four channels because AstrBot sometimes captures stdout,
    filters third-party loggers, and otherwise suppresses diagnostic
    output. The file-based path is the reliable one — write to
    ``<data_dir>/esm-debug.log`` and operators can tail it directly.
    """
    line = f"[EMT-DBG] {msg}"
    # 1. stdout
    try:
        print(line, flush=True)
    except Exception:
        pass
    # 2. stderr
    try:
        import sys
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
    except Exception:
        pass
    # 3. AstrBot logger (may be filtered)
    try:
        from astrbot.api import logger as LOG
        LOG.warning(msg)
    except Exception:
        pass
    # 4. file in plugin data dir — reliable fallback
    try:
        from pathlib import Path
        data_dir = Path(__file__).parent / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        with open(data_dir / "esm-debug.log", "a", encoding="utf-8") as f:
            from datetime import datetime
            f.write(f"{datetime.now().isoformat()} {line}\n")
    except Exception:
        pass


def _resolve_register(plugin) -> tuple[Any, str] | None:
    """Return a callable ``register(path, handler, methods, desc)``."""
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
        _diag(f"PluginPageApi.__init__ for plugin id={id(plugin)}")

    def register_routes(self) -> None:
        """Register the three page endpoints under both path shapes."""
        _diag("register_routes: ENTRY")
        resolved = _resolve_register(self.plugin)
        if resolved is None:
            _diag("register_routes: NO register_web_api on context")
            raise RuntimeError(
                "context.register_web_api not available"
            )
        register, source = resolved
        _diag(f"register_routes: backend = {source}")

        handlers = [
            ("/health", self._health, ["GET"], "ESM health probe"),
            ("/state", self._full_state, ["GET"],
             "ESM full emotion state"),
            ("/state/<scope>", self._scope_detail, ["GET"],
             "ESM single scope detail"),
        ]
        prefix_variants = [PAGE_API_PREFIX, "/page"]
        ok_count = 0
        for sub_path, handler, methods, desc in handlers:
            for prefix in prefix_variants:
                full_path = prefix + sub_path
                try:
                    register(full_path, handler, methods, desc)
                    _diag(f"register_routes:   OK  {full_path} {methods}")
                    ok_count += 1
                except Exception as exc:
                    _diag(
                        f"register_routes:   FAIL {full_path}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    _diag(traceback.format_exc())
        _diag(
            f"register_routes: DONE, ok_count={ok_count}/"
            f"{len(handlers)*len(prefix_variants)}"
        )
        if ok_count == 0:
            raise RuntimeError("no routes registered")

    # ---------- handlers ----------

    async def _health(self) -> dict[str, Any]:
        _diag("_health handler HIT")
        machine = self.plugin.machine
        return {
            "version": __version__,
            "appraisal_mode": machine.appraisal_mode,
            "signal_count": len(getattr(machine, "groups", {})),
            "scope_count": len(machine.groups),
        }

    async def _full_state(self) -> dict[str, Any]:
        _diag("_full_state handler HIT")
        return get_full_state(self.plugin.machine)

    async def _scope_detail(self, scope: str) -> dict[str, Any]:
        _diag(f"_scope_detail handler HIT scope={scope}")
        state = get_full_state(self.plugin.machine)
        for s in state["scopes"]:
            if s["scope"] == scope:
                return s
        return {"error": "scope not found", "scope": scope}