"""Pytest bootstrap for astrbot_plugin_emotion_state_machine.

`main.py` imports from `astrbot.api.*`, which is not available in this
project's test environment. To allow testing the public API on the
plugin class without standing up the full AstrBot runtime, we install
minimal fake modules in `sys.modules` before any test imports `main`.

The existing `test_emotion_engine.py` only imports the pure
`emotion_engine` module and is unaffected by this shim.
"""

from __future__ import annotations

import sys
import types


class _FakeLogger:
    def info(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): pass
    def debug(self, *args, **kwargs): pass
    def error(self, *args, **kwargs): pass


class _FakeEventMessageType:
    ALL = "all"


class _FakePermissionType:
    ADMIN = "admin"


class _FakeFilter:
    EventMessageType = _FakeEventMessageType
    PermissionType = _FakePermissionType

    def event_message_type(self, *args, **kwargs):
        return lambda fn: fn

    def on_llm_request(self):
        return lambda fn: fn

    def command(self, *args, **kwargs):
        return lambda fn: fn

    def permission_type(self, *args, **kwargs):
        return lambda fn: fn


class _FakeStar:
    def __init__(self, context):
        self.context = context


class _FakeTextPart:
    """Minimal stand-in for ``astrbot.core.agent.message.TextPart``.

    Stores ``text`` / ``type`` as plain attributes so tests can assert
    on them without instantiating the real class. ``mark_as_temp()``
    returns ``self`` so chaining works exactly like the real one.

    The real ``TextPart`` is unavailable in this test environment
    because ``astrbot.core.agent.message`` is not part of the
    AstrBot public package surface that conftest fakes; without this
    shim, ``import main`` raises ``ModuleNotFoundError`` at collection
    time.
    """

    def __init__(self, text: str = "", type: str = "text") -> None:
        self.text = text
        self.type = type

    def mark_as_temp(self) -> "_FakeTextPart":
        # Mirror the real method's contract: return self so callers can
        # chain ``.mark_as_temp()`` directly.
        return self


def _install_fake_astrbot() -> None:
    if "astrbot" in sys.modules and getattr(
        sys.modules["astrbot"], "_is_fake", False
    ):
        return

    astrbot = types.ModuleType("astrbot")
    astrbot._is_fake = True  # type: ignore[attr-defined]

    api = types.ModuleType("astrbot.api")
    api.logger = _FakeLogger()

    event_mod = types.ModuleType("astrbot.api.event")
    event_mod.AstrMessageEvent = object
    event_mod.filter = _FakeFilter()

    star_mod = types.ModuleType("astrbot.api.star")
    star_mod.Context = object
    star_mod.Star = _FakeStar

    config_pkg = types.ModuleType("astrbot.core")
    config_sub = types.ModuleType("astrbot.core.config")
    config_mod = types.ModuleType("astrbot.core.config.astrbot_config")
    config_mod.AstrBotConfig = object

    # v0.10.0+: fake TextPart so plugin methods that build TextPart
    # objects (e.g. ``to_text_part``) can be tested in isolation.
    agent_pkg = types.ModuleType("astrbot.core.agent")
    agent_pkg.__path__ = []  # mark as package so submodule import works
    msg_mod = types.ModuleType("astrbot.core.agent.message")
    msg_mod.TextPart = _FakeTextPart

    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.star"] = star_mod
    sys.modules["astrbot.core"] = config_pkg
    sys.modules["astrbot.core.config"] = config_sub
    sys.modules["astrbot.core.config.astrbot_config"] = config_mod
    sys.modules["astrbot.core.agent"] = agent_pkg
    sys.modules["astrbot.core.agent.message"] = msg_mod


_install_fake_astrbot()
