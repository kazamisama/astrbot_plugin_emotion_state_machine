# ESM Public API Reference

> v0.10.0+ — explicit public API contract for plugin-to-plugin interop.
>
> Other plugins get an ESM instance via `context.get_registered_star("astrbot_plugin_emotion_state_machine")` and call the methods documented here. Methods NOT listed here are implementation details and may change without notice.

## Stability Tiers

| Tier | Meaning |
|---|---|
| **Stable** | Backward-compatible through v0.x. May gain new args; won't break signatures. |
| **New Stable** | Added in v0.10.0; same stability promise going forward. |
| **Experimental** | May change between minor versions. Prefer not to rely on unless you also bump ESM with your plugin. |
| **Deprecated** | Will be removed in v0.11+. Migrate now. |

---

## Reading state

### `get_scope(event) -> str` — Stable

Compute the canonical scope key for an AstrBot event. **Always use this** — other plugins must not derive scope from `event.get_group_id()` directly, or they'll land on a different namespace than the built-in observer.

```python
machine = self.context.get_registered_star("astrbot_plugin_emotion_state_machine")
scope = machine.get_scope(event)
```

### `get_combined_state(scope, user_id="", *, apply_decay=True) -> CombinedEmotionView` — Stable

Full snapshot: group + relation + combined label. **This is the main read API.** Other plugins should prefer this over the lower-level helpers.

### `get_group_state(scope, *, apply_decay=True) -> GroupEmotionSnapshot` — Stable

Just the group atmosphere (valence / arousal / stress / curiosity / PAD). Use when you don't care about per-user relation.

### `get_relation_state(scope, user_id, *, apply_decay=True) -> UserRelationSnapshot` — Stable

Just the per-user relation (trust / affection / irritation / familiarity).

### `render_state_text(scope, user_id="") -> str` — Stable

Human-readable rendering, identical to the `/emotion_state` command output. For debug/log lines.

### `list_signals() -> list[str]` — Stable

All valid signal names. Use to validate before `apply_signal`.

### `is_signal_enabled(signal) -> bool` — Stable

Case-insensitive check against `disabled_signals` config. Returns `False` for unknown signals (defensive default).

### `list_disabled_signals() -> list[str]` — Stable

Currently-disabled signal names (sorted, lowercased).

---

## Building prompt blocks

### `build_prompt_block(scope, user_id="") -> str` — Stable

Raw string for the emotion block. Honors `emotion_block_template` config (aligned with `on_llm_request` in v0.10.0). Returns the same content `on_llm_request` injects.

### `to_text_part(scope, user_id="") -> TextPart` — New Stable (v0.10.0+)

The emotion block as a `TextPart` (with `.mark_as_temp()` chained). Use this from other plugins that build their own `request.extra_user_content_parts` lists. Each plugin's block lands as an independent TextPart rather than getting string-concatenated.

```python
# social_context judge channel
extra_parts.append(esm.to_text_part(scope, user_id))
```

---

## Writing state

### `observe_text(scope, text, *, user_id="", mentioned=False, update_relation=True) -> CombinedEmotionView` — Stable

Infer signals from raw text and apply. Same engine `observe_message` uses.

### `apply_signal(scope, user_id, signal, *, intensity=1.0, reason="external") -> CombinedEmotionView` — Stable

Strict variant — raises `ValueError` on unknown signal. State is persisted on success.

### `try_apply_signal(scope, user_id, signal, *, intensity=1.0, reason="external") -> CombinedEmotionView | None` — Stable

Safe variant — returns `None` on `ValueError` / `TypeError` instead of raising. Use in hot paths.

### `apply_self_reply_signal(event) -> bool` — New Stable (v0.10.0+)

Called by `social_context` (or any proactive-decider plugin) right after deciding the bot should reply without user @/wake. Consults the TalkWillingnessState accumulator and applies a `self_reply` signal iff the model decides to.

**Contract**:
- Returns `True` iff a `self_reply` signal was actually applied to the bot's state.
- Returns `False` (silently, no exception) for: disabled config, user @-triggered, outside trigger zone, reversal zone, consecutive-apply cap, disabled signal, scope-not-found, internal error.
- Never breaks the caller's flow — failures are caught and logged at `debug`.

The signal applied is `self_reply` (only affects group `arousal` and `curiosity`; does NOT touch relation-layer dimensions — by design, to break the social_context ↔ ESM feedback loop).

```python
# social_context side
esm = self.context.get_registered_star("astrbot_plugin_emotion_state_machine")
if esm and hasattr(esm, "apply_self_reply_signal"):
    await esm.apply_self_reply_signal(event)
```

### `decay(scope, *, now=None) -> GroupEmotionSnapshot` — Experimental

Manually advance the decay clock. Useful for tests and time-traveling replays.

### `reset_scope(scope) -> GroupEmotionSnapshot` — Stable

Reset a scope entirely (group + all relations). Persists. Mirrors `/emotion_reset` command. In v0.10.0+ also drops the per-scope TalkWillingness state.

### `force_decay(scope, *, now=None) -> GroupEmotionSnapshot` — Stable

Force a decay pass + persist. Same as `decay` but always persists.

### `prune_cold_state() -> dict[str, int]` — Stable

Prune cold groups and relations. Returns `{"groups_pruned": int, "relations_pruned": int}`. Persists only when something was actually pruned.

### `set_appraisal_mode(mode) -> None` — Stable

Switch appraisal mode at runtime. `mode` ∈ `"direct"` / `"occ_static"` / `"occ_heuristic"`.

---

## Class-level (not instance-bound)

### `TalkWillingnessState` (module-level) — New Stable (v0.10.0+)

Pure-logic self-reply accumulation state machine. Importable directly without standing up the plugin. See the docstring on the class for the full interface.

```python
from main import TalkWillingnessState
tw = TalkWillingnessState()
W, should_apply, intensity = tw.tick(...)
```

---

## Removed / not exposed

These are implementation details. Don't call them from other plugins:

- `_inject_emotion_block` (module-level helper for `on_llm_request`)
- `_cfg_str` / `_cfg_float` / `_cfg_bool` / `_cfg_int` / `_cfg_list` (config coercion helpers)
- `_resolve_event_persona` / `_scope_id` / `_bot_persona_name` (scope derivation)
- `_save_state` / `_load_state` / `_migrate_scope_ids_if_needed` (persistence)
- `_register_official_page_api_if_available` (route registration)
- `_cleanup_self_reply_tracking` (internal cleanup)
- Any `_` (single-underscore) prefixed method.

---

## Version compatibility matrix

| Plugin combination | Emotion injection | Self-reply signal |
|---|---|---|
| social_context v0.8.11 + ESM v0.9.x | works (string concat) | not available |
| social_context v0.8.11 + ESM v0.10.0+ | works (string concat) | works (social_context doesn't call yet) |
| social_context v0.8.12 + ESM v0.9.x | **broken** (no `to_text_part`) | not available |
| social_context v0.8.12 + ESM v0.10.0+ | works (`to_text_part`) | works (social_context calls `apply_self_reply_signal`) |

**Strong recommendation**: install ESM v0.10.0+ before installing social_context v0.8.12+.

---

## What changed in v0.10.0

### Added
- `to_text_part(scope, user_id) -> TextPart` — counterpart to `build_prompt_block` returning a `TextPart` ready for direct injection.
- `apply_self_reply_signal(event) -> bool` — entry point for proactive-decider plugins to trigger bot self-reflection emotion signals.
- `TalkWillingnessState` module-level class — pure-logic brain-inspired accumulator.
- `self_reply` signal — only affects group `arousal`/`curiosity`, never relation layer.

### Aligned
- `build_prompt_block` now honors `emotion_block_template` (previously bypassed it).

### Internal
- `observe_message` tracks per-scope user message timestamps for the time / turn-density factors.
- `reset_scope` and HTTP `POST /delete/<scope>` drop TalkWillingness state alongside emotion state.
- New `self_reply_settings` config section with 7 tunable thresholds.