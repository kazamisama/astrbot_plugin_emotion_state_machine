"""Prompt block construction and human-readable rendering.

Two responsibilities live here:

1. **Prompt block** (:func:`build_prompt_block`) — produces the
   low-noise text the ``on_llm_request`` hook injects into the system
   prompt. Wrapped in :data:`ESM_BLOCK_START` / :data:`ESM_BLOCK_END`
   sentinel markers so re-injection can replace (not append) prior
   blocks. The markers are HTML-style comments and pass through all
   known LLM tokenizers without interpretation.
2. **Human-readable rendering** (:func:`format_snapshot`,
   :func:`format_relation`, :func:`format_combined_view`,
   :func:`style_hint_for`) — produces the same text the
   ``/emotion_state`` command shows, for use in debug logs and other
   plugins' status output.

``style_hint_for`` is the bridge between the discrete label world and
the prompt's prose. It maps ``(group_label, relation_label)`` pairs to
short style directives the LLM can act on without exposing numeric
scores. New labels can be added by extending the two dicts at the
bottom of this module.
"""

from __future__ import annotations

import time
from typing import Union

from .labels import derive_combined_label
from .state import CombinedEmotionView, GroupEmotionSnapshot, UserRelationSnapshot


# Sentinel markers wrap the emotion block so the plugin can find and
# replace the block on re-injection (instead of appending a duplicate).
# HTML comments are invisible to all known LLM tokenizers and pass through
# system prompts untouched.
ESM_BLOCK_START = "<!-- esm:emotion-block:start -->"
ESM_BLOCK_END = "<!-- esm:emotion-block:end -->"


def format_snapshot(scope: str, snapshot: GroupEmotionSnapshot) -> str:
    age = max(0.0, time.time() - snapshot.updated_at)
    return (
        f"🧭 Group Emotion | {scope}\n"
        f"- label: {snapshot.label}\n"
        f"- valence: {snapshot.valence:.2f}\n"
        f"- arousal: {snapshot.arousal:.2f}\n"
        f"- stress: {snapshot.stress:.2f}\n"
        f"- curiosity: {snapshot.curiosity:.2f}\n"
        f"- active_users: {len(snapshot.active_users)}\n"
        f"- last_signal: {snapshot.last_signal}\n"
        f"- last_reason: {snapshot.last_reason}\n"
        f"- transitions: {snapshot.transitions}\n"
        f"- updated: {age:.0f}s ago"
    )


def format_relation(scope: str, user_id: str, snapshot: UserRelationSnapshot) -> str:
    age = max(0.0, time.time() - snapshot.updated_at)
    return (
        f"👤 User Relation | {scope} / {user_id}\n"
        f"- label: {snapshot.label}\n"
        f"- trust: {snapshot.trust:.2f}\n"
        f"- affection: {snapshot.affection:.2f}\n"
        f"- irritation: {snapshot.irritation:.2f}\n"
        f"- familiarity: {snapshot.familiarity:.2f}\n"
        f"- last_signal: {snapshot.last_signal}\n"
        f"- last_reason: {snapshot.last_reason}\n"
        f"- transitions: {snapshot.transitions}\n"
        f"- updated: {age:.0f}s ago"
    )


def format_combined_view(view: CombinedEmotionView) -> str:
    text = format_snapshot(view.scope, view.group)
    if view.relation is not None:
        text += "\n\n" + format_relation(view.scope, view.user_id, view.relation)
        text += f"\n\n- combined_label: {view.label}"
    return text


def build_prompt_block(
    scope: str,
    view_or_snapshot: Union[CombinedEmotionView, GroupEmotionSnapshot],
) -> str:
    """Build a low-noise prompt block for LLM context injection.

    The returned string is wrapped in :data:`ESM_BLOCK_START` /
    :data:`ESM_BLOCK_END` sentinel markers so the plugin can detect and
    replace a previous injection instead of stacking duplicates. The
    markers are HTML-style comments and are not rendered or interpreted
    by LLMs.
    """
    if isinstance(view_or_snapshot, CombinedEmotionView):
        view = view_or_snapshot
    else:
        view = CombinedEmotionView(scope=scope, user_id="", group=view_or_snapshot, relation=None)

    style_hint = style_hint_for(view)
    group = view.group
    relation = view.relation
    relation_line = "towards_current_user: unavailable"
    if relation is not None:
        relation_line = (
            f"towards_current_user: label={relation.label}, trust={relation.trust:.2f}, "
            f"affection={relation.affection:.2f}, irritation={relation.irritation:.2f}, "
            f"familiarity={relation.familiarity:.2f}"
        )

    inner = (
        "## Bot Emotion State\n"
        f"scope: {scope}\n"
        f"combined_label: {view.label}\n"
        f"group: label={group.label}, valence={group.valence:.2f}, arousal={group.arousal:.2f}, "
        f"stress={group.stress:.2f}, curiosity={group.curiosity:.2f}, active_users={len(group.active_users)}\n"
        f"{relation_line}\n"
        f"last_signal: group={group.last_signal}"
        + (f", user={relation.last_signal}" if relation is not None else "")
        + "\n"
        f"style_hint: {style_hint}\n"
        "Use this as subtle continuity only. Do not mention numeric scores unless explicitly asked."
    )
    return f"{ESM_BLOCK_START}\n{inner}\n{ESM_BLOCK_END}"


def style_hint_for(
    view_or_snapshot: Union[CombinedEmotionView, GroupEmotionSnapshot],
) -> str:
    """Map the current label pair to a short style directive for the LLM.

    Relation labels win when they carry strong opinions ("guarded",
    "irritated"). "attached" combined with a calm group atmosphere
    produces a soft style. Otherwise we fall through to the group label
    hints. ``"calm"`` is the fallback when no group label matches.
    """
    if isinstance(view_or_snapshot, CombinedEmotionView):
        group = view_or_snapshot.group
        relation = view_or_snapshot.relation
    else:
        group = view_or_snapshot
        relation = None

    if relation is not None:
        if relation.label == "guarded":
            return "be concise and careful with this user; avoid playful escalation"
        if relation.label == "irritated":
            return "keep boundaries, answer plainly, do not intensify conflict"
        if relation.label == "attached" and group.stress <= 0.55:
            return "soft, familiar, naturally attentive to the current user"
        if relation.label == "trusted":
            return "relaxed and cooperative with the current user"

    hints = {
        "annoyed": "slightly sharper and brief, but not hostile",
        "hurt": "quiet, restrained, avoid playful provocation",
        "tense": "efficient, direct, reduce noise",
        "excited": "more energetic and responsive",
        "happy": "warm and relaxed",
        "curious": "ask precise follow-up only when useful",
        "quiet": "low-energy, concise companionship",
        "calm": "balanced and natural",
    }
    return hints.get(group.label, hints["calm"])