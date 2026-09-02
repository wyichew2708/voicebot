"""Handing the call to a person, as a procedure rather than a sentence.

The first build "escalated" by saying a line and then carrying on with the
script — including, in one recorded call, pitching a product immediately after
telling the customer we could not help them. A handoff is not a line. It is a
state the call enters, and it changes what happens next.

What a handoff has to do, in order:

  1. Name the reason in the customer's own terms. "I can't get your new email
     address down accurately over this line" — not "escalating".
  2. Say what will happen and by when, so the customer is not left guessing
     whether anyone will actually call.
  3. Confirm we can reach them. A callback promised to a number nobody
     answers is worse than no callback.
  4. Record it in a form a colleague can pick up: why, what we already have,
     and what is still outstanding.
  5. Stop. No further script turns, and above all no cross-sell.

This module is deliberately transport-agnostic. On this build a handoff is a
*callback* — there is no telephony leg to bridge — but `Handoff.warm` and the
event it rides on are the seam a real warm transfer plugs into, and the
customer-facing wording already distinguishes the two.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Reason = Literal["advice", "data_change", "language", "not_understood",
                 "off_topic", "pricing", "requested", "complaint"]

#: Ranked by how badly the customer needs a person. When more than one applies
#: — and they often do, because a frustrated caller is also a misheard one —
#: the earliest wins, so the record names the cause rather than the symptom.
PRIORITY: tuple[Reason, ...] = ("complaint", "requested", "advice", "pricing",
                                "data_change", "language", "off_topic",
                                "not_understood")


@dataclass
class Handoff:
    """One transfer, from the moment it is decided to the moment it is logged."""

    reason: Reason
    #: What the colleague needs to know, in the operator's words.
    summary: str
    #: True once a telephony leg can actually be bridged. Until then the
    #: customer is promised a callback, and told so.
    warm: bool = False
    #: Facts already gathered, so the colleague does not start from nothing.
    collected: dict[str, str] = field(default_factory=dict)
    #: Still outstanding when we gave up.
    outstanding: str = ""

    @property
    def code(self) -> str:
        return f"handoff.{self.reason}"


# What the customer hears. Each one names the reason, states the action and
# the window, and ends with the contactability check — the three things a
# callback promise needs to be worth anything.
WHY = {
    "advice": {
        "en": "That really needs one of our licensed advisers rather than me.",
        "zh": "这个问题需要由我们持牌的顾问来为您解答，我不方便回答。",
    },
    "data_change": {
        "en": "I don't want to risk getting your email address wrong — if it's "
              "off by one character the renewal notice won't reach you at all.",
        "zh": "我不想弄错您的电邮地址。只要错一个字母，续保通知就寄不到您那里。",
    },
    "pricing": {
        "en": "Pricing is set by our underwriting team rather than by me, so I "
              "can't move it on this call.",
        "zh": "保费是由我们的核保部门决定的，我在这通电话里没办法调整。",
    },
    # {tongue} is filled from what we actually heard. Hard-coding Malay here
    # meant a Tamil caller was told, in English, that we could not speak Malay.
    "off_topic": {
        "en": "That's outside what I can help with on a renewal call.",
        "zh": "这个不在我这通续保电话能处理的范围内。",
    },
    "language": {
        "en": "I can understand you, but I'm not able to hold this conversation "
              "in {tongue} yet.",
        "zh": "我可以听懂，但我还没办法用{tongue}跟您对话。",
    },
    "not_understood": {
        "en": "The line isn't doing us any favours — I'm not catching you "
              "properly and I don't want to guess at your details.",
        "zh": "这条线路不太清楚，我怕听错您的资料。",
    },
    "requested": {
        "en": "Of course.",
        "zh": "当然可以。",
    },
    "complaint": {
        "en": "I'm sorry — I haven't handled this well.",
        "zh": "很抱歉，是我处理得不好。",
    },
}

ACTION = {
    "en": ("Let me have a colleague call you back — they'll have everything "
           "we've covered, so you won't have to repeat yourself."),
    "zh": "我安排同事回电给您。刚才谈的内容都会记录下来，您不用再说一遍。",
}

ACTION_WARM = {
    "en": "Let me put you through to a colleague now — one moment.",
    "zh": "我现在帮您转接同事，请稍等。",
}

#: Asked as its own turn: a callback promised to a number nobody answers is
#: worse than no callback at all.
REACHABLE = {
    "en": "We'll call the number ending {last4} — is that the best one for you?",
    "zh": "我们会打给尾号 {last4} 的号码，请问这个号码方便吗？",
}

REACHABLE_YES = {
    "en": "Perfect. They'll be in touch within one working day.",
    "zh": "好的，同事会在一个工作日内联络您。",
}

REACHABLE_NO = {
    "en": "No problem — I'll note that, and they'll check the best number with "
          "you when they call.",
    "zh": "没问题，我会记下来，同事联络您时会再跟您确认号码。",
}


#: How each language we can hear but not speak is named to the caller.
TONGUE = {
    "ms": {"en": "Malay", "zh": "马来语"},
    "ta": {"en": "Tamil", "zh": "淡米尔语"},
}


def why(reason: Reason, lang: str, tongue: str | None = None) -> str:
    text = WHY[reason].get(lang, WHY[reason]["en"])
    if "{tongue}" in text:
        named = TONGUE.get(tongue or "ms", TONGUE["ms"])
        text = text.format(tongue=named.get(lang, named["en"]))
    return text


def language_lines() -> list[tuple[str, str]]:
    """Every filled-in form of the language line, for the pre-render pass."""
    return [(why("language", lang, tongue), lang)
            for tongue in TONGUE for lang in ("en", "zh")]


def action(lang: str, warm: bool = False) -> str:
    table = ACTION_WARM if warm else ACTION
    return table.get(lang, table["en"])


def reachable(phone: str, lang: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    last4 = " ".join(digits[-4:]) or "on file"
    table = REACHABLE
    return table.get(lang, table["en"]).format(last4=last4)


def all_lines() -> list[tuple[str, str]]:
    """(text, lang) for the fixed half of every handoff, so the pre-render
    pass warms them. The reachability line carries a phone number and is
    warmed per persona by the render script instead."""
    out: list[tuple[str, str]] = []
    for table in (*WHY.values(), ACTION, ACTION_WARM, REACHABLE_YES, REACHABLE_NO):
        out.extend((text, lang) for lang, text in table.items()
                   if "{tongue}" not in text)
    out.extend(language_lines())
    return out
