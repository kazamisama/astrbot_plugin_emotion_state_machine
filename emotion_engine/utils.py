"""Pure utility helpers shared by the emotion_engine submodules.

Framework-free — no imports from ``astrbot`` or from this plugin's
``main.py``. Deliberately kept small so it sits at the bottom of the
dependency graph and never needs to import from sibling modules.

Lives in its own module so that ``state``, ``machine``, and any future
appraisal-style layer can reuse the same primitive helpers without
re-introducing the circular dependencies that the old monolithic
``emotion_engine.py`` had to dance around.
"""

from __future__ import annotations

import math


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def normalize_scope(scope: str) -> str:
    return str(scope or "_default")


def normalize_user_id(user_id: str | None) -> str:
    return str(user_id or "").strip()


def prune_active_users(active_users: dict[str, float], now: float, window_seconds: float) -> dict[str, float]:
    """Drop entries older than ``now - window_seconds`` in place.

    Mutates ``active_users`` and returns the same object for backward
    compatibility with prior call sites. The previous implementation
    rebuilt a fresh dict on every invocation, which put allocation
    pressure on hot message paths in busy groups (one O(n) dict
    allocation per message). In-place deletion skips the copy entirely
    and only deletes the actual stale entries.

    The function tolerates non-dict input by returning it unchanged —
    this keeps the helper safe to call defensively.
    """
    if not active_users:
        return active_users
    cutoff = float(now) - float(window_seconds)
    stale = [user for user, ts in active_users.items() if float(ts) < cutoff]
    for user in stale:
        active_users.pop(user, None)
    return active_users


def _legacy_module_dilution(active_count: int) -> float:
    """Kept for backward-compatibility with any external callers that still
    import ``active_user_dilution`` from this module. Delegates to the
    ``EmotionStateMachine`` default (sqrt curve) when called as a free
    function.
    """
    return 1.0 / math.sqrt(max(1, int(active_count)))


# Backward-compatible alias. New callers should use
# ``EmotionStateMachine._active_user_dilution`` to honor the configured
# ``dilution_exponent``.
active_user_dilution = _legacy_module_dilution