"""Default settings.

Same keys as the Reminders agent's config.json so the existing file can seed the
app. Keys that only made sense for Reminders (list names, note separators) are
dropped; "hints" is new and controls how many words the quiz hides per stage.
"""
from __future__ import annotations

import copy
import json
from typing import Any

DEFAULTS: dict[str, Any] = {
    "cadence": {
        "daily_repeats": 7,
        "weekly_repeats": 4,
        "monthly_repeats": 24,
    },
    "mastered": {
        # Months between Mastered checks: 3, then 6, then 12, then every
        # yearly_interval months after that.
        "review_months": [3, 6, 12],
        "yearly_interval": 12,
    },
    "auto_add": {
        "every_n_days": 7,  # 0 = never add automatically
        "topic_default": "The Book of Mormon",
        "chatgpt_fallback": False,  # ask ChatGPT for a verse when Backlog is empty
    },
    "obfuscation": {
        "enabled": True,  # word hints on/off
        # The agent's Monthly note schedule. Kept for reference; the quiz uses
        # hints.monthly below instead.
        "schedule": [1.0, 0.75, 0.5, 0.35, 0.2],
        "min_word_len": 3,
        "keep_first_last": False,
    },
    "hints": {
        # Fraction of words left visible, interpolated across each stage, so
        # hints keep shrinking from one stage to the next. 0 = no hint text.
        "daily": [1.0, 0.4],
        "weekly": [0.35, 0.2],
        "monthly": [0.35, 0.0],
        "mastered": [0.0],
    },
    "quiz": {
        # How to answer "Which verse is this?": "type", "choice" (multiple
        # choice from the same book), or "mix" (alternate between them).
        "answer_style": "mix",
    },
    "notify": {
        # Phone notifications (Web Push): a morning summary, and an evening
        # nudge only if verses are still due.
        "enabled": True,
        "morning": "07:00",
        "evening_enabled": True,
        "evening": "21:00",
    },
    "due_time": {"hour": 22, "minute": 0},
}

# Keys from the agent's config.json that the web app ignores.
_AGENT_ONLY_KEYS = {"lists", "maintenance"}
_AGENT_ONLY_OBF_KEYS = {"separator", "respect_punctuation", "buffer_lines", "buffer_token"}


def merged(overrides: dict | None) -> dict:
    """Return DEFAULTS deep-merged with overrides (unknown keys are kept)."""
    out = copy.deepcopy(DEFAULTS)

    def _merge(dst: dict, src: dict) -> None:
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                _merge(dst[k], v)
            else:
                dst[k] = copy.deepcopy(v)

    _merge(out, overrides or {})
    return out


def from_agent_config(path: str) -> dict:
    """Read the Reminders agent's config.json and keep the keys the app uses."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    raw = {k: v for k, v in raw.items() if k not in _AGENT_ONLY_KEYS}
    if isinstance(raw.get("obfuscation"), dict):
        raw["obfuscation"] = {
            k: v for k, v in raw["obfuscation"].items() if k not in _AGENT_ONLY_OBF_KEYS
        }
    return merged(raw)
