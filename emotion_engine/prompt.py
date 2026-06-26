"""Prompt block construction and human-readable rendering.

Two responsibilities live here:

1. **Prompt block** (:func:`build_prompt_block`) -- produces the
   low-noise text the ``on_llm_request`` hook injects into the system
   prompt. Wrapped in :data:`ESM_BLOCK_START` / :data:`ESM_BLOCK_END`
   sentinel markers so re-injection can replace (not append) prior
   blocks. The markers are HTML-style comments and pass through all
   known LLM tokenizers without interpretation.
2. **Human-readable rendering** (:func:`format_snapshot`,
   :func:`format_relation`, :func:`format_combined_view`,
   :func:`style_hint_for`) -- produces the same text the
   ``/emotion_state`` command shows, for use in debug logs and other
   plugins' status output.
3. **ASCII chart + PAD** (:func:`compute_pad`,
   :func:`format_group_chart`, :func:`format_relation_chart`,
   :func:`format_combined_chart`) -- v0.6.0: PAD (Pleasure-Arousal-
   Dominance) model alignment and horizontal bar chart rendering.

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
        f"🧭 群情绪 | {scope}\n"
        f"- \u6807\u7b7e: {snapshot.label}\n"
        f"- \u6109\u60a6\u5ea6: {snapshot.valence:.1f}\n"
        f"- \u5524\u9192\u5ea6: {snapshot.arousal:.1f}\n"
        f"- \u538b\u529b: {snapshot.stress:.1f}\n"
        f"- \u597d\u5947\u5fc3: {snapshot.curiosity:.1f}\n"
        f"- \u6d3b\u8dc3\u7528\u6237: {len(snapshot.active_users)}\n"
        f"- \u6700\u8fd1\u4fe1\u53f7: {snapshot.last_signal}\n"
        f"- \u6700\u8fd1\u539f\u56e0: {snapshot.last_reason}\n"
        f"- \u72b6\u6001\u8fc1\u79fb: {snapshot.transitions}\n"
        f"- \u66f4\u65b0\u4e8e: {age:.0f} \u79d2\u524d"
    )


def format_relation(scope: str, user_id: str, snapshot: UserRelationSnapshot) -> str:
    age = max(0.0, time.time() - snapshot.updated_at)
    return (
        f"👤 用户关系 | {scope} / {user_id}\n"
        f"- \u6807\u7b7e: {snapshot.label}\n"
        f"- \u4fe1\u4efb: {snapshot.trust:.1f}\n"
        f"- \u597d\u611f: {snapshot.affection:.1f}\n"
        f"- \u7126\u8651: {snapshot.irritation:.1f}\n"
        f"- \u719f\u6089\u5ea6: {snapshot.familiarity:.1f}\n"
        f"- \u6700\u8fd1\u4fe1\u53f7: {snapshot.last_signal}\n"
        f"- \u6700\u8fd1\u539f\u56e0: {snapshot.last_reason}\n"
        f"- \u72b6\u6001\u8fc1\u79fb: {snapshot.transitions}\n"
        f"- \u66f4\u65b0\u4e8e: {age:.0f} \u79d2\u524d"
    )


def format_combined_view(view: CombinedEmotionView) -> str:
    text = format_snapshot(view.scope, view.group)
    if view.relation is not None:
        text += "\n\n" + format_relation(view.scope, view.user_id, view.relation)
        text += f"\n\n- \u7efc\u5408\u6807\u7b7e: {view.label}"
    return text


# ---------------------------------------------------------------------------
# PAD model mapping (Mehrabian & Russell, 1974)
# ---------------------------------------------------------------------------

def compute_pad(snapshot: GroupEmotionSnapshot) -> tuple[float, float, float]:
    """Map group dimensions to PAD (Pleasure-Arousal-Dominance).

    - **P**leasure = ``valence`` (1:1 mapping).
    - **A**rousal = ``arousal`` (1:1 mapping).
    - **D**ominance = ``1.0 - stress`` (the bot feels in control when
      not under pressure; ``stress -> 1.0`` means complete loss of
      control, ``stress -> 0.0`` means full agency).

    PAD is a derived view -- it does not change internal state. All
    three values are in [0.0, 1.0].
    """
    return (snapshot.valence, snapshot.arousal, 1.0 - snapshot.stress)


# ---------------------------------------------------------------------------
# ASCII chart rendering (v0.6.0+)
# ---------------------------------------------------------------------------

_BAR_WIDTH = 10
_BAR_FILLED = "\u2588"  # full block
_BAR_EMPTY = "\u2591"   # light shade


def _bar(value: float, width: int = _BAR_WIDTH) -> str:
    """Render a horizontal bar ``width`` chars wide.

    Example: ``_bar(0.75)`` -> ``"â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–‘â–‘â–‘"``.
    """
    filled = max(0, min(width, int(value * width + 0.5)))
    return _BAR_FILLED * filled + _BAR_EMPTY * (width - filled)


def format_group_chart(scope: str, snapshot: GroupEmotionSnapshot) -> str:
    """ASCII bar chart for one group snapshot, including PAD.

    Output looks like::

        群情绪 | 123456
          valence      â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–‘â–‘ 0.78  happy
          arousal      â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–‘â–‘â–‘â–‘ 0.55
          stress       â–ˆâ–ˆâ–ˆâ–‘â–‘â–‘â–‘â–‘â–‘â–‘ 0.30
          curiosity    â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–‘ 0.85
          PAD: P=0.78 A=0.55 D=0.70
    """
    p, a, d = compute_pad(snapshot)
    age = max(0.0, time.time() - snapshot.updated_at)
    lines = [
        f"🧭 群情绪 | {scope}",
        f"  \u6109\u60a6\u5ea6     {_bar(snapshot.valence)} {snapshot.valence:.1f}  {snapshot.label}",
        f"  \u5524\u9192\u5ea6     {_bar(snapshot.arousal)} {snapshot.arousal:.1f}",
        f"  \u538b\u529b       {_bar(snapshot.stress)} {snapshot.stress:.1f}",
        f"  \u597d\u5947\u5fc3     {_bar(snapshot.curiosity)} {snapshot.curiosity:.1f}",
        f"  PAD: \u6109\u60a6={p:.1f} \u5524\u9192={a:.1f} \u652f\u914d={d:.1f}",
        f"  \u6d3b\u8dc3\u7528\u6237: {len(snapshot.active_users)} | "
        f"\u6700\u8fd1\u4fe1\u53f7: {snapshot.last_signal} | \u66f4\u65b0\u4e8e: {age:.0f} \u79d2\u524d",
    ]
    return "\n".join(lines)


def format_relation_chart(scope: str, user_id: str, snapshot: UserRelationSnapshot) -> str:
    """ASCII bar chart for one user relation snapshot.

    Output looks like::

        用户关系 | 123456 / user-a
          trust        â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–‘â–‘ 0.78  trusted
          affection    â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–‘ 0.92
          irritation   â–ˆâ–ˆâ–‘â–‘â–‘â–‘â–‘â–‘â–‘â–‘ 0.15
          familiarity  â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–‘â–‘â–‘â–‘ 0.62
    """
    age = max(0.0, time.time() - snapshot.updated_at)
    lines = [
        f"👤 用户关系 | {scope} / {user_id}",
        f"  \u4fe1\u4efb         {_bar(snapshot.trust)} {snapshot.trust:.1f}  {snapshot.label}",
        f"  \u597d\u611f         {_bar(snapshot.affection)} {snapshot.affection:.1f}",
        f"  \u7126\u8651        {_bar(snapshot.irritation)} {snapshot.irritation:.1f}",
        f"  \u719f\u6089\u5ea6     {_bar(snapshot.familiarity)} {snapshot.familiarity:.1f}",
        f"  \u6700\u8fd1\u4fe1\u53f7: {snapshot.last_signal} | \u72b6\u6001\u8fc1\u79fb: {snapshot.transitions} | "
        f"\u66f4\u65b0\u4e8e: {age:.0f} \u79d2\u524d",
    ]
    return "\n".join(lines)


def format_combined_chart(view: CombinedEmotionView) -> str:
    """Group chart + relation chart + combined label."""
    text = format_group_chart(view.scope, view.group)
    if view.relation is not None:
        text += "\n\n" + format_relation_chart(view.scope, view.user_id, view.relation)
        text += f"\n  \u7efc\u5408\u6807\u7b7e: {view.label}"
    return text


# ---------------------------------------------------------------------------
# Prompt block
# ---------------------------------------------------------------------------

# v0.9.52: default template — overridable via _conf_schema.json's
# `emotion_block_template` field. Available placeholders:
#   {scope} {combined_label} {style_hint}
#   {group_label} {group_valence} {group_arousal} {group_stress} {group_curiosity} {active_users}
#   {pad_p} {pad_a} {pad_d}
#   {relation_label} {relation_trust} {relation_affection} {relation_irritation} {relation_familiarity}
#   {group_last_signal} {relation_last_signal}
#   {relation_block} — pre-formatted multi-line block (label + 4 dims) or
#     the literal string "towards_current_user: unavailable" when no relation.
DEFAULT_EMOTION_BLOCK_TEMPLATE = (
    "\u60c5\u7eea\u72b6\u6001\n"
    "\u4f5c\u7528\u57df: {scope}\n"
    "\u7efc\u5408\u6807\u7b7e: {combined_label}\n"
    "\u7fa4\u60c5\u7eea: \u6807\u7b7e={group_label}, \u6109\u60a6\u5ea6={group_valence}, \u5524\u9192\u5ea6={group_arousal}, "
    "\u538b\u529b={group_stress}, \u597d\u5947\u5fc3={group_curiosity}, \u6d3b\u8dc3\u7528\u6237={active_users}\n"
    "PAD: \u6109\u60a6={pad_p} \u5524\u9192={pad_a} \u652f\u914d={pad_d}\n"
    "{relation_block}\n"
    "\u6700\u8fd1\u4fe1\u53f7: \u7fa4={group_last_signal}, \u7528\u6237={relation_last_signal}\n"
    "\u98ce\u683c\u63d0\u793a: {style_hint}\n"
    "\u4ec5\u4f5c\u4e3a\u8bed\u6c14\u8fde\u7eed\u6027\u7684\u53c2\u8003\u3002\u9664\u975e\u7528\u6237\u660e\u786e\u8981\u6c42\uff0c\u5426\u5219\u4e0d\u8981\u4e3b\u52a8\u63d0\u53ca\u6570\u503c\u3002"
)


def _build_emotion_block_variables(
    scope: str,
    view_or_snapshot: "Union[CombinedEmotionView, GroupEmotionSnapshot]",
) -> dict:
    """Build the {placeholder} -> value dict consumed by str.format()."""
    if isinstance(view_or_snapshot, CombinedEmotionView):
        view = view_or_snapshot
    else:
        view = CombinedEmotionView(scope=scope, user_id="", group=view_or_snapshot, relation=None)

    group = view.group
    relation = view.relation
    p, a, d = compute_pad(group)

    if relation is not None:
        relation_block = (
            f"\u5bf9\u5f53\u524d\u7528\u6237: \u5173\u7cfb\u6807\u7b7e={relation.label}, "
            f"\u4fe1\u4efb={relation.trust:.1f}, \u597d\u611f={relation.affection:.1f}, "
            f"\u7126\u8651={relation.irritation:.1f}, \u719f\u6089\u5ea6={relation.familiarity:.1f}"
        )
        relation_label = relation.label
        relation_trust = round(relation.trust, 1)
        relation_affection = round(relation.affection, 1)
        relation_irritation = round(relation.irritation, 1)
        relation_familiarity = round(relation.familiarity, 1)
        relation_last_signal = relation.last_signal
    else:
        relation_block = "\u5bf9\u5f53\u524d\u7528\u6237: \u65e0\u5173\u7cfb\u6570\u636e"
        relation_label = "n/a"
        relation_trust = relation_affection = relation_irritation = relation_familiarity = "n/a"
        relation_last_signal = "-"

    return {
        "scope": scope,
        "combined_label": view.label,
        "style_hint": style_hint_for(view),
        "group_label": group.label,
        "group_valence": round(group.valence, 1),
        "group_arousal": round(group.arousal, 1),
        "group_stress": round(group.stress, 1),
        "group_curiosity": round(group.curiosity, 1),
        "active_users": len(group.active_users),
        "pad_p": round(p, 1),
        "pad_a": round(a, 1),
        "pad_d": round(d, 1),
        "relation_block": relation_block,
        "relation_label": relation_label,
        "relation_trust": relation_trust,
        "relation_affection": relation_affection,
        "relation_irritation": relation_irritation,
        "relation_familiarity": relation_familiarity,
        "group_last_signal": group.last_signal,
        "relation_last_signal": relation_last_signal,
    }


def build_prompt_block(
    scope: str,
    view_or_snapshot: "Union[CombinedEmotionView, GroupEmotionSnapshot]",
    template: str | None = None,
) -> str:
    """Build a low-noise prompt block for LLM context injection.

    v0.9.52: ``template`` overrides DEFAULT_EMOTION_BLOCK_TEMPLATE.
    The caller (plugin) typically reads the override from
    _conf_schema.json's ``emotion_block_template`` field so admins
    can edit the wording without touching code.

    The returned string is wrapped in :data:`ESM_BLOCK_START` /
    :data:`ESM_BLOCK_END` sentinel markers so the plugin can detect and
    replace a previous injection instead of stacking duplicates. The
    markers are HTML-style comments and are not rendered or interpreted
    by LLMs.
    """
    variables = _build_emotion_block_variables(scope, view_or_snapshot)
    tmpl = template if template else DEFAULT_EMOTION_BLOCK_TEMPLATE
    inner = tmpl.format(**variables)
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