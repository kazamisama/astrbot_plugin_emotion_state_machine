"""Emotion state engine for ``astrbot_plugin_emotion_state_machine``.

This package is intentionally framework-free — it does not import
``astrbot.*`` and can be unit-tested in isolation. It models bot
emotion as a layered state machine:

- **group emotion** — shared conversation atmosphere (valence,
  arousal, stress, curiosity).
- **user relation** — bot's private relation toward a specific user
  (trust, affection, irritation, familiarity).
- **combined view** — group atmosphere + current sender relation, used
  for prompt injection and the ``/emotion_state`` command output.
- **decay** — both layers slowly move back to baseline over time so
  emotion does not accumulate forever.

Layered architecture (post v0.4.0 refactor):

============================  =========================================
Module                         Responsibility
============================  =========================================
:mod:`.utils`                  Pure helpers: clamp, normalize, prune.
:mod:`.defaults`               Factory defaults: baselines, weights,
                               thresholds, keyword sets.
:mod:`.state`                  Snapshot dataclasses and ``EmotionEvent``.
:mod:`.signals`                Signal taxonomy (names + per-layer
                               weights). Re-exports from ``defaults``.
:mod:`.signals_classify`       Text → signal inference (keyword scan).
:mod:`.appraisal`              Direct weight-delta application (the
                               ``appraisal_mode == "direct"`` path).
:mod:`.labels`                 Discrete label derivation from
                               continuous dimensions.
:mod:`.machine`                Orchestrator: ``EmotionStateMachine``.
:mod:`.prompt`                 Prompt block, formatters, sentinels.
============================  =========================================

Backward compatibility
----------------------

Every symbol previously importable from the pre-v0.4.0 monolithic
``emotion_engine.py`` is re-exported here, so existing
``from emotion_engine import X`` statements (in tests and in external
plugins) continue to work without modification. New code should
prefer importing from the appropriate submodule — for example
``from emotion_engine.labels import derive_group_label`` — but the
flat re-export surface is frozen for the foreseeable future.
"""

from __future__ import annotations

# Package version — keep in sync with metadata.yaml.
# Used by page_api.py for the /health endpoint.
__version__ = "0.9.49"

# Re-exports — every name here must remain importable from
# ``emotion_engine`` for backward compat with the pre-v0.4.0 single-file
# module. The grouping below mirrors the layout in :data:`__all__`.

# ---- utils --------------------------------------------------------
from .utils import (
    _legacy_module_dilution,
    active_user_dilution,
    clamp,
    normalize_scope,
    normalize_user_id,
    prune_active_users,
)

# ---- defaults -----------------------------------------------------
from .defaults import (
    APPRAISAL_MODES,
    APPRAISAL_TO_DIMENSION_GROUP,
    APPRAISAL_TO_DIMENSION_RELATION,
    GROUP_BASELINE,
    GROUP_LABEL_THRESHOLDS,
    GROUP_SIGNAL_WEIGHTS,
    KEYWORD_SIGNALS,
    QUESTION_INDICATORS,
    RELATION_BASELINE,
    RELATION_LABEL_THRESHOLDS,
    RELATION_SIGNAL_WEIGHTS,
    SIGNAL_APPRAISAL_PROFILES,
    SIGNAL_LAYER_WEIGHTS,
)

# ---- state --------------------------------------------------------
from .state import (
    CombinedEmotionView,
    EmotionEvent,
    EmotionSnapshot,
    GroupEmotionSnapshot,
    UserRelationSnapshot,
)

# ---- signals ------------------------------------------------------
from .signals import signal_names

# ---- signals_classify ---------------------------------------------
from .signals_classify import (
    _contains_interrogative,
    _ends_with_question_mark,
    dedupe_signals,
    infer_signals,
)

# ---- appraisal ----------------------------------------------------
from .appraisal import (
    AppraisalEstimator,
    DirectEstimator,
    OCCHeuristicEstimator,
    OCCStaticEstimator,
    apply_weights,
    get_estimator,
)
from .appraisal_heuristics import AppraisalContext, estimate_appraisal

# ---- labels -------------------------------------------------------
from .labels import (
    _eval_label_condition,
    derive_combined_label,
    derive_group_label,
    derive_label,
    derive_relation_label,
)

# ---- machine ------------------------------------------------------
from .machine import EmotionStateMachine

# ---- prompt -------------------------------------------------------
from .prompt import (
    ESM_BLOCK_END,
    ESM_BLOCK_START,
    _bar,
    build_prompt_block,
    compute_pad,
    format_combined_chart,
    format_combined_view,
    format_group_chart,
    format_relation,
    format_relation_chart,
    format_snapshot,
    style_hint_for,
)


# ---- api (v0.7.0+) ------------------------------------------------
from .api import get_full_state

# ---- webui (v0.7.0+) -------------------------------------------------
from .webui import render_webui_page, render_state_json

__all__ = [
    # utils
    "clamp",
    "normalize_scope",
    "normalize_user_id",
    "prune_active_users",
    "active_user_dilution",
    "_legacy_module_dilution",
    # defaults
    "GROUP_BASELINE",
    "RELATION_BASELINE",
    "GROUP_SIGNAL_WEIGHTS",
    "RELATION_SIGNAL_WEIGHTS",
    "SIGNAL_LAYER_WEIGHTS",
    "KEYWORD_SIGNALS",
    "QUESTION_INDICATORS",
    "GROUP_LABEL_THRESHOLDS",
    "RELATION_LABEL_THRESHOLDS",
    "SIGNAL_APPRAISAL_PROFILES",
    "APPRAISAL_TO_DIMENSION_GROUP",
    "APPRAISAL_TO_DIMENSION_RELATION",
    "APPRAISAL_MODES",
    # state
    "EmotionEvent",
    "GroupEmotionSnapshot",
    "UserRelationSnapshot",
    "CombinedEmotionView",
    "EmotionSnapshot",  # backward-compat alias
    # signals
    "signal_names",
    # signals_classify
    "_ends_with_question_mark",
    "_contains_interrogative",
    "infer_signals",
    "dedupe_signals",
    # appraisal
    "apply_weights",
    "AppraisalEstimator",
    "DirectEstimator",
    "OCCStaticEstimator",
    "OCCHeuristicEstimator",
    "get_estimator",
    "AppraisalContext",
    "estimate_appraisal",
    # labels
    "_eval_label_condition",
    "derive_group_label",
    "derive_relation_label",
    "derive_combined_label",
    "derive_label",
    # machine
    "EmotionStateMachine",
    # prompt
    "ESM_BLOCK_START",
    "ESM_BLOCK_END",
    "build_prompt_block",
    "compute_pad",
    "format_snapshot",
    "format_relation",
    "format_combined_view",
    "format_group_chart",
    "format_relation_chart",
    "format_combined_chart",
    "style_hint_for",
    # api
    "get_full_state",
    # webui
    "render_webui_page",
    "render_state_json",
]