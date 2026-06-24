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


# Module-level diagnostic — fires the moment the file is imported.
# Uses LOG.error + os.write(2, ...) + file fallback so we ALWAYS
# surface in the AstrBot console even if stdout/logger are captured.
import os as _os_mod, sys as _sys
from datetime import datetime as _dt2

_MODULE_LINE = (f"[EMT-DBG] page_api.py MODULE LOADED at {_dt2.now().isoformat()} "
                f"(PLUGIN_NAME={PLUGIN_NAME}, pid={_os_mod.getpid()})")

# ERROR-level AstrBot log — never filtered
try:
    from astrbot.api import logger as _LOG
    _LOG.error(_MODULE_LINE)
except Exception:
    pass

# os.write(2, ...) — bypasses sys.stderr capture
try:
    _os_mod.write(2, (_MODULE_LINE + "\n").encode("utf-8", errors="replace"))
except Exception:
    pass

# file fallback to multiple locations
for _loc in [
    _os_mod.path.join(_os_mod.path.expanduser("~"), ".astrbot", "esm-debug.log"),
    _os_mod.path.join(_os_mod.path.expanduser("~"), "esm-debug.log"),
    r"C:\Users\chiriu\.astrbot\esm-debug.log",
    r"C:\esm-debug.log",
    r"C:\Windows\Temp\esm-debug.log",
]:
    try:
        _d = _os_mod.path.dirname(_loc)
        if _d and not _os_mod.path.isdir(_d):
            _os_mod.makedirs(_d, exist_ok=True)
        with open(_loc, "a", encoding="utf-8") as _f:
            _f.write(_MODULE_LINE + "\n")
        break
    except Exception:
        continue


def _diag(msg: str) -> None:
    """Diagnostic write. Pushes to as many channels as possible.

    ERROR-level AstrBot log is always visible; os.write(2, ...) bypasses
    stdout capture; the file fallback survives even sandbox virtualisation.
    """
    line = f"[EMT-DBG] {msg}"
    import os as _os, sys as _sys
    # 1. ERROR-level logger — never filtered
    try:
        from astrbot.api import logger as LOG
        LOG.error(line)
    except Exception:
        pass
    # 2. os.write to fd 2 (stderr) — bypasses sys.stderr capture
    try:
        _os.write(2, (line + "\n").encode("utf-8", errors="replace"))
    except Exception:
        pass
    # 3. file at multiple fallback paths
    _DIAG_LOCATIONS = [
        _os.path.join(_os.path.expanduser("~"), ".astrbot", "esm-debug.log"),
        _os.path.join(_os.path.expanduser("~"), "esm-debug.log"),
        r"C:\Users\chiriu\.astrbot\esm-debug.log",
        r"C:\esm-debug.log",
        r"C:\Windows\Temp\esm-debug.log",
        "/tmp/esm-debug.log",
    ]
    for _loc in _DIAG_LOCATIONS:
        try:
            _dir = _os.path.dirname(_loc)
            if _dir and not _os.path.isdir(_dir):
                _os.makedirs(_dir, exist_ok=True)
            with open(_loc, "a", encoding="utf-8") as f:
                from datetime import datetime as _dt
                f.write(f"{_dt.now().isoformat()} {line}\n")
            break  # success — stop trying
        except Exception:
            continue


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
        # Brute-force: register every plausible path shape so the
        # bridge can find us regardless of which prefix style the
        # user's AstrBot version uses. Each is a separate (route,
        # handler, methods) entry in `registered_web_apis`; the
        # Werkzeug match function in `dashboard/server.py` tries
        # them in order.
        prefix_variants = [
            PAGE_API_PREFIX,                # /<plugin_name>/page/<x>
            "/page",                        # /page/<x>
            f"/{PLUGIN_NAME}",             # /<plugin_name>/<x>  (no /page)
            "",                             # /<x>  (bare)
        ]
        ok_count = 0
        registered_paths = []
        for sub_path, handler, methods, desc in handlers:
            for prefix in prefix_variants:
                full_path = prefix + sub_path
                if full_path in registered_paths:
                    continue
                try:
                    register(full_path, handler, methods, desc)
                    _diag(f"register_routes:   OK  {full_path} {methods}")
                    registered_paths.append(full_path)
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