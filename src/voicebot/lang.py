"""Language and register detection.

Two separate judgements, deliberately kept apart:

* **which language** the caller is speaking — drives the script locale, and is
  expensive to get wrong, so switching demands real evidence;
* **which register** they are speaking it in — drives how the bot phrases its
  own improvised lines, and is cheap to get wrong, so a light touch is fine.
"""
from __future__ import annotations

# An explicit ask. One of these is enough on its own.
SWITCH_TO_ZH = (
    "讲华语", "说华语", "讲中文", "说中文", "华语可以吗", "可以讲华语",
    "speak mandarin", "speak chinese", "in mandarin", "in chinese",
    "华语", "中文",
)
SWITCH_TO_EN = (
    "speak english", "in english", "english please", "讲英文", "说英文", "英文",
)

# Singlish markers. Particles carry most of the signal; the syntax patterns
# ("can or not", "got ... anot") are the giveaways that survive transcription
# even when the particles are mangled.
SINGLISH_MARKERS = (
    " lah", " lor", " leh", " meh", " hor", " sia", " ah?", " ah.", " ah ",
    "aiyah", "aiyoh", "wah ", "or not", "anot", "issit", "can can",
    " liao", " already ", " one ah", "steady", "shiok", "alamak",
)


def detect(text: str, default: str = "en") -> str:
    """Script-based language guess for a single utterance."""
    return "zh" if any("一" <= ch <= "鿿" for ch in text) else default


def asks_for(text: str) -> str | None:
    """Did the caller explicitly ask to be spoken to in another language?"""
    low = text.lower()
    if any(k in low for k in SWITCH_TO_ZH):
        return "zh"
    if any(k in low for k in SWITCH_TO_EN):
        return "en"
    return None


def is_singlish(text: str) -> bool:
    """Two markers, so a single "ah" or a stray "already" does not trip it."""
    low = f" {text.lower()} "
    return sum(1 for m in SINGLISH_MARKERS if m in low) >= 2
