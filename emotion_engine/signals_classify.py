"""Text → signal inference.

The classifier is currently a pure keyword scan (Chinese + a few
common English / technical tokens). It is intentionally simple so it
runs on the hot message path with zero allocations beyond the result
list — the alternative would be an LLM call, which is several orders
of magnitude more expensive and is left to higher-level plugins (a
planned ``register_classifier`` hook will let other plugins override
the inference path without touching this module).

The output is a list of ``(signal_name, reason_string)`` tuples,
capped at four signals per message via :func:`dedupe_signals`. The
state machine then converts each tuple into an :class:`EmotionEvent`.
"""

from __future__ import annotations

from .defaults import KEYWORD_SIGNALS, QUESTION_INDICATORS


def _ends_with_question_mark(text: str) -> bool:
    """True if the message ends with ``?`` or ``？`` (after stripping
    trailing whitespace). Bare ``?`` in the middle of a sentence does
    not count — see :func:`infer_signals` for the rationale.
    """
    stripped = text.rstrip()
    return bool(stripped) and stripped[-1] in ("?", "？")


def _contains_interrogative(text: str) -> bool:
    """True if the message contains a Chinese question word, modal
    question phrase, or the ``吗`` particle near the end.
    """
    if any(indicator in text for indicator in QUESTION_INDICATORS):
        return True
    # "吗" is a suffix; only treat it as a question marker if it sits
    # right before the end of a sentence (followed by optional terminal
    # punctuation). This avoids matching substrings like "马马虎虎".
    stripped = text.rstrip(" \t\n\r.!?。！？,，;；:：")
    return stripped.endswith("吗")


def infer_signals(text: str, *, mentioned: bool = False) -> list[tuple[str, str]]:
    """Infer emotion signals from a plain message.

    The ``question`` signal is fired only when:

    1. The message ends with ``?`` / ``？`` (most common case), **or**
    2. The message contains a Chinese interrogative word, modal question
       phrase, or the ``吗`` particle near the end.

    Bare ``?`` in the middle of a sentence (e.g. "OK? 然后...") is
    intentionally not enough — too noisy for casual group conversation
    where users sprinkle question marks into statements and rhetorical
    tags.
    """
    lowered = (text or "").lower()
    signals: list[tuple[str, str]] = []
    if mentioned:
        signals.append(("mention", "bot mentioned"))
    if _ends_with_question_mark(lowered) or _contains_interrogative(lowered):
        signals.append(("question", "question mark or interrogative"))
    for signal, keywords in KEYWORD_SIGNALS:
        if any(keyword.lower() in lowered for keyword in keywords):
            signals.append((signal, f"keyword:{signal}"))
    return dedupe_signals(signals)


def dedupe_signals(signals: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Collapse duplicate signal names and cap the result at four.

    The 4-item cap protects the state machine from runaway inputs: a
    user who writes a paragraph mixing praise + technical + question +
    thanks + pressure should not produce five transitions in a single
    apply_interaction cycle (the bot would feel "spasmodic" otherwise).
    """
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for signal, reason in signals:
        if signal in seen:
            continue
        seen.add(signal)
        result.append((signal, reason))
    return result[:4]