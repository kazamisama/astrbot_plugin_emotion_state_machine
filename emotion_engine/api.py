"""Data access API for the emotion state machine WebUI.

Exposes the full in-memory state as JSON-serializable dicts. Designed
to be called by the web route handler or by external plugins that want
a live dump of all groups + relations at once.

The output format is intentionally flat (a list of scope summaries, not
the raw to_dict v3) so the frontend does zero post-processing beyond
rendering. Each scope entry includes the group snapshot fields, PAD,
and a ``users`` list of per-user relation snapshots.
"""

from __future__ import annotations

from .machine import EmotionStateMachine
from .prompt import compute_pad


def get_full_state(machine: EmotionStateMachine) -> dict:
    """Return a JSON-serializable dict of the full emotion state.

    Result shape::

        {
          "scopes": [
            {
              "scope": "group-123",
              "group": {
                "label": "calm",
                "valence": 0.56, "arousal": 0.32,
                "stress": 0.18, "curiosity": 0.38,
                "pad": {"P": 0.56, "A": 0.32, "D": 0.82},
                "active_users": 4,
                "last_signal": "mention",
                "updated_at": 1719200000.0,
                "transitions": 12
              },
              "users": [
                {
                  "user_id": "user-a",
                  "label": "trusted",
                  "trust": 0.75, "affection": 0.68,
                  "irritation": 0.10, "familiarity": 0.25,
                  "last_signal": "praise",
                  "updated_at": 1719200000.0,
                  "transitions": 5
                }
              ]
            }
          ],
          "appraisal_mode": "occ_heuristic",
          "signal_count": 13
        }

    All floating-point values are rounded to 3 decimal places for
    compact JSON output.
    """
    scopes = []
    for scope_name, group_snap in sorted(machine.groups.items()):
        p, a, d = compute_pad(group_snap)
        group = {
            "label": group_snap.label,
            "valence": round(group_snap.valence, 3),
            "arousal": round(group_snap.arousal, 3),
            "stress": round(group_snap.stress, 3),
            "curiosity": round(group_snap.curiosity, 3),
            "pad": {"P": round(p, 3), "A": round(a, 3), "D": round(d, 3)},
            "active_users": len(group_snap.active_users),
            # v0.9.33: list of active user_ids (for frontend highlight)
            "active_user_ids": list(group_snap.active_users.keys()),
            "last_signal": group_snap.last_signal,
            "last_reason": group_snap.last_reason,
            "updated_at": round(group_snap.updated_at, 3),
            "transitions": group_snap.transitions,
        }
        users = []
        bucket = machine.relations.get(scope_name, {})
        for uid, rel_snap in sorted(bucket.items()):
            users.append({
                "user_id": uid,
                "label": rel_snap.label,
                "trust": round(rel_snap.trust, 3),
                "affection": round(rel_snap.affection, 3),
                "irritation": round(rel_snap.irritation, 3),
                "familiarity": round(rel_snap.familiarity, 3),
                "last_signal": rel_snap.last_signal,
                "last_reason": rel_snap.last_reason,
                "updated_at": round(rel_snap.updated_at, 3),
                "transitions": rel_snap.transitions,
            })
        scopes.append({"scope": scope_name, "group": group, "users": users})

    from .signals import signal_names
    return {
        "scopes": scopes,
        "appraisal_mode": machine.appraisal_mode,
        "signal_count": len(signal_names()),
    }
