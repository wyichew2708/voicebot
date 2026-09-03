"""Compliance gates.

These are enforced in code, not asked of the model. A prompt instruction is a
request; a gate is a precondition. Each gate fails closed.

The rule that catches people out: under the PDPA Do Not Call provisions the
ongoing-relationship exemption covers text and fax messages only. A
telemarketing *voice* call to a Singapore number still needs clear and
unambiguous consent or a valid DNC check — including to an existing
policyholder of many years. This is why servicing (turns 1-5) and marketing
(turn 6) are gated separately.

Not legal advice. Confirm the reading with counsel before dialling anyone real.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from ..data.personas import Policy
from ..data.facts import is_advice_request

GateState = Literal["pending", "pass", "block"]

DNC_CHECK_VALID_DAYS = 21


@dataclass
class Gates:
    identity: GateState = "pending"
    dnc: GateState = "pending"
    consent: GateState = "pending"
    advice: GateState = "pending"
    notes: dict[str, str] = field(default_factory=dict)

    def set(self, gate: str, state: GateState, note: str = "") -> None:
        setattr(self, gate, state)
        if note:
            self.notes[gate] = note

    def as_dict(self) -> dict[str, str]:
        return {"identity": self.identity, "dnc": self.dnc,
                "consent": self.consent, "advice": self.advice}


# ---------------------------------------------------------------- identity

AFFIRMATIVE = ("yes", "yeah", "ya", "yep", "speaking", "that's me", "thats me",
               "correct", "ya lah", "是", "对", "我是", "是的")
WRONG_PARTY = ("not home", "not in", "he's not", "she's not", "hes not", "shes not",
               "i'm his", "im his", "i'm her", "im her", "his son", "her son",
               "his daughter", "her daughter", "wrong number", "who is this",
               "不在", "打错")


#: Mandarin negates by prefix, so every affirmative is a substring of its own
#: negation: 不是 contains 是, 不对 contains 对, 我不是 contains 是. Plain
#: containment therefore read "no, I'm not" as "yes" and passed the identity
#: gate — on a recorded call the bot answered 呃不是 by reading the caller's
#: property address back to a person who had just said they were not the
#: policyholder. This is the CJK half of the "ya" inside "Malaysia" problem,
#: and the more dangerous half, because the negation is the common case.
NEGATORS = frozenset("不没沒非未无無别別莫勿")


def _contains(haystack: str, needle: str) -> bool:
    """Word-boundary match for latin tokens, negation-aware containment for CJK.

    Short tokens like "ya" must not match inside "Malaysia" or "player" — a
    false positive here waves an unverified caller past the one gate that
    protects personal data. The same is true of 是 inside 不是, which is why
    a CJK match immediately after a negator does not count.
    """
    if needle.isascii():
        return re.search(rf"(?<![a-z]){re.escape(needle)}(?![a-z])", haystack) is not None
    return any(m.start() == 0 or haystack[m.start() - 1] not in NEGATORS
               for m in re.finditer(re.escape(needle), haystack))


def check_identity(reply: str) -> tuple[GateState, str]:
    """Right-party verification. Nothing personal may be disclosed until this
    passes — turn 2 alone reveals the property address."""
    low = reply.lower()
    # Wrong-party wins: "he's not home, yes I'm his son" must never pass.
    if any(_contains(low, w) for w in WRONG_PARTY):
        return "block", "Right-party verification failed — policy details withheld"
    if any(_contains(low, w) for w in AFFIRMATIVE):
        return "pass", ""
    return "pending", ""


# --------------------------------------------------------------------- dnc

def check_dnc(p: Policy) -> tuple[GateState, str]:
    """A DNC check is valid for 21 days. Stale is treated as unchecked."""
    if p.dnc_checked_days_ago > DNC_CHECK_VALID_DAYS:
        return "block", (f"DNC check is {p.dnc_checked_days_ago} days old — "
                         f"valid for {DNC_CHECK_VALID_DAYS}. Re-check required before marketing.")
    if p.dnc_listed:
        return "block", "Number listed on the No Voice Call register"
    return "pass", ""


# ----------------------------------------------------------------- consent

@dataclass
class CallState:
    """How the call has actually gone, as far as the cross-sell is concerned.

    Consent and identity say whether we are *permitted* to pitch. They say
    nothing about whether we should. In one recorded call the bot told the
    customer it could not update their email and a colleague would call —
    and then, in the very next breath, pitched personal accident cover. Every
    field here exists because of a moment like that.
    """
    handing_off: bool = False        # we just told them we could not help
    unresolved: str = ""             # a servicing request we did not finish
    awaiting_adviser: bool = False   # they are already waiting on a callback
    impatient: bool = False          # they told us to hurry, or to go away
    declined: bool = False           # they have already said no to this
    comprehension_failures: int = 0  # we have not been hearing them


def may_cross_sell(p: Policy, gates: Gates,
                   state: CallState | None = None) -> tuple[bool, str]:
    """Turn 6 is marketing, not servicing. Servicing may continue regardless;
    the promotion may not.

    Two separate questions, and the first build only asked the first one:
    whether we are permitted to pitch (consent, DNC, right party), and whether
    pitching is a reasonable thing to do to this person right now.
    """
    if gates.identity != "pass":
        return False, "Cross-sell blocked — right party not verified"

    s = state or CallState()
    if s.handing_off:
        return False, ("Cross-sell blocked — we just told them we could not "
                       "help and a colleague would call")
    if s.awaiting_adviser:
        return False, "Cross-sell blocked — already waiting on an adviser callback"
    if s.unresolved:
        return False, (f"Cross-sell blocked — {s.unresolved} is still "
                       "unresolved; finish the servicing first")
    if s.impatient:
        return False, "Cross-sell blocked — caller asked us to be quick or to go"
    if s.declined:
        return False, "Cross-sell blocked — already declined once on this call"
    if s.comprehension_failures >= 2:
        return False, ("Cross-sell blocked — we have not been hearing them; a "
                       "pitch is the last thing this call needs")

    if p.marketing_consent:
        return True, ""
    dnc_state, dnc_note = check_dnc(p)
    if dnc_state == "pass":
        return True, ""
    return False, ("Cross-sell blocked — the ongoing-relationship exemption "
                   "does not cover voice calls. " + dnc_note)


# ------------------------------------------------------------------ advice

def check_advice(text: str) -> tuple[bool, str]:
    """Advising on an insurance product engages Financial Advisers Act
    obligations. The bot answers factual coverage questions and hands anything
    advisory to a licensed human."""
    if is_advice_request(text):
        return True, ("Advice request detected — routing to a licensed adviser "
                      "rather than answering")
    return False, ""
