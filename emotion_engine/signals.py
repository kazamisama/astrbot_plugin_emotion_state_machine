"""Signal taxonomy surface: names + per-layer weight tables.

A "signal" is a typed event category (``praise``, ``insult``, ``poke``,
...) that the dispatcher routes to two layers simultaneously — the
group atmosphere and the per-user relation. Each signal has three
pieces of metadata:

- ``GROUP_SIGNAL_WEIGHTS[signal]``: dimension deltas applied to the
  group snapshot.
- ``RELATION_SIGNAL_WEIGHTS[signal]``: dimension deltas applied to
  the per-user relation snapshot.
- ``SIGNAL_LAYER_WEIGHTS[signal]``: a ``(group_weight, relation_weight)``
  tuple that controls how the same intensity gets split between the
  two layers.

The tables themselves live in :mod:`.defaults` (they are configuration
data, not behavior). This module is the *interface* — the public entry
points other code uses to enumerate signals (``signal_names()``) and
the import surface that keeps ``from emotion_engine import
GROUP_SIGNAL_WEIGHTS``-style imports working.
"""

from __future__ import annotations

from .defaults import (
    GROUP_SIGNAL_WEIGHTS,
    RELATION_SIGNAL_WEIGHTS,
    SIGNAL_LAYER_WEIGHTS,
)


def signal_names() -> list[str]:
    """All signals the engine understands, sorted alphabetically.

    Sorted so the output is deterministic for display (e.g. the
    ``/emotion_signal`` help text). Use this to validate signal
    arguments before calling ``apply_signal``.
    """
    return sorted(GROUP_SIGNAL_WEIGHTS.keys())