"""Change requests, and why the bot no longer tries to take one down.

Correcting the email is the commonest off-script turn on a renewal call — turn
4 literally invites it — and the first build ignored it entirely. The second
tried to capture it, which was worse: asked to change an address to
"w y i a @ hotmail.com", the bot heard yi@hotmail.com, read that back, and on
the retry heard "a. liars" for "a, alias". A voice line is not a form.

Any change to the customer's details or to their cover now goes to customer
care, with what the caller said attached. Nothing is written to the record
here."""
import asyncio

import pytest

from voicebot.call.engine import CallSession
from voicebot.data import personas
from voicebot.data.facts import (spoken_email, wants_email_change,
                                 wants_record_change)
from voicebot.runtime.mock import MockBackend

TO_TURN_4 = ["Yes speaking", "When", "Yes"]


def _run(replies):
    policy = personas.get("TH-4471-0093")
    policy.email = "wm.tan@example.sg"          # module-level fixture; reset it
    session = CallSession(policy, MockBackend())

    async def go():
        events = []
        async for ev in session.start():
            events.append(ev)
        for r in replies:
            async for ev in session.on_caller(r):
                events.append(ev)
        return events

    return asyncio.run(go()), session


def _agent_text(events):
    return " ".join(e.text for e in events
                    if e.kind == "transcript" and e.speaker == "agent")


def _tools(events):
    return [e.tool for e in events if e.kind == "tool"]


@pytest.mark.parametrize("said,expected", [
    ("w m dot tan at example dot s g", "wm.tan@example.sg"),
    ("my new one is jimmy.chew at gmail dot com", "jimmy.chew@gmail.com"),
    ("weiming underscore tan at hotmail dot com", "weiming_tan@hotmail.com"),
    ("please use wm.tan@example.sg instead", "wm.tan@example.sg"),
    ("erm I think so", None),
])
def test_spoken_email_parsing(said, expected):
    assert spoken_email(said) == expected


@pytest.mark.parametrize("said", [
    "No, please change my email", "can you send it to another email",
    "that's the wrong email", "uh no i changed my email address",
    "may i change my email address?", "my email is wrong",
])
def test_change_requests_are_recognised(said):
    """Stems, not whole phrases: "changed my email address" and "may I change
    my email address?" both went unheard against a fixed phrase list, and the
    caller had to ask three times before anything happened."""
    assert wants_email_change(said)


@pytest.mark.parametrize("said", ["no", "uh, no.", "nope", "不对"])
def test_a_bare_no_is_a_change_request_only_after_the_read_back(said):
    """"No" answers the confirmation question. Anywhere else in the call it
    means something else entirely, and opening the email sub-dialogue on it
    would hijack the conversation."""
    assert wants_email_change(said, after_confirm=True)
    assert not wants_email_change(said)


def test_renew_is_not_a_request_to_change_anything():
    """Substring matching made "renew" match the stem "new"."""
    assert not wants_email_change("yes I will renew it")
    assert not wants_email_change("please look through the email and renew")


def test_plain_confirmation_is_not_a_change_request():
    assert not wants_email_change("Yup, that's right")


@pytest.mark.parametrize("said,want", [
    ("w, y, i, c, h, e, w, alias hotmail dot com.", "wyichew@hotmail.com"),
    ("w, y, i, c, h, e, w, at hotmail dot com.", "wyichew@hotmail.com"),
    ("w y i c h e w at hotmail dot com", "wyichew@hotmail.com"),
    ("please use wm.tan@example.sg instead.", "wm.tan@example.sg"),
])
def test_a_spelled_address_survives_the_recogniser(said, want):
    """Retained because the parser is still the best available reading of a
    spelled address, and a future channel where the customer can *see* what was
    captured could use it. Nothing on a voice call does."""
    assert spoken_email(said) == want


# --- what a change request does now ---------------------------------------

@pytest.mark.parametrize("said,kind", [
    ("oh no, i changed my email address", "data"),
    ("i want to update my address", "data"),
    ("my phone number is wrong", "data"),
    ("我要改电邮", "data"),
    ("please cancel my policy", "policy"),
    ("can i add my wife to the policy", "policy"),
    ("i want to increase my sum insured", "policy"),
    ("我要取消保单", "policy"),
])
def test_change_requests_are_classified(said, kind):
    assert wants_record_change(said) == kind


@pytest.mark.parametrize("said", [
    "yes the address is correct", "what does the policy cover",
    "where is my property address?", "please renew by the due date",
    "how much is the premium?", "Yup, that's right",
])
def test_a_confirmation_is_not_a_change_request(said):
    """The expensive false positive: a caller agreeing with turn 2 must not be
    routed away for it. This is why the broadened detector drops "correct" and
    "fix" from the words it will act on."""
    assert wants_record_change(said) is None


def test_an_email_change_goes_to_customer_care():
    events, session = _run(["Yes speaking", "oh no, i changed my email address"])
    handoffs = [e for e in events if e.kind == "handoff"]
    assert handoffs and handoffs[0].reason == "data_change"
    assert "crm.flag_profile_change" in _tools(events)


def test_a_policy_change_goes_to_customer_care():
    events, _ = _run(["Yes speaking", "actually i want to cancel my policy"])
    handoffs = [e for e in events if e.kind == "handoff"]
    assert handoffs and handoffs[0].reason == "policy_change"
    assert "crm.flag_policy_change" in _tools(events)


def test_nothing_is_asked_for_and_nothing_is_written():
    """The two things the old sub-dialogue did that this must not: ask the
    caller to dictate, and write what it thought it heard to the record."""
    before = personas.get("TH-4471-0093").email
    events, session = _run(["Yes speaking", "please change my email address"])
    said = _agent_text(events)
    assert "say it slowly" not in said.lower()
    assert "read it back" not in said.lower()
    assert "crm.update_email" not in _tools(events)
    assert session.p.email == before


def test_the_old_address_is_not_read_back():
    """Reported: asked to change the address, the bot recited the one it
    already had."""
    events, session = _run(["Yes speaking", "my email is wrong"])
    assert session.p.email not in _agent_text(events)


def test_what_the_caller_said_reaches_the_colleague():
    """The colleague has to finish this, so they get the caller's own words
    rather than a summary of them."""
    events, _ = _run(["Yes speaking", "please send it to my other address"])
    handoffs = [e for e in events if e.kind == "handoff"]
    assert handoffs and "other address" in handoffs[0].outstanding


def test_the_cross_sell_does_not_follow_a_change_request():
    """A pitch immediately after telling someone we could not help them is the
    failure this whole handoff procedure exists to prevent."""
    events, _ = _run(["Yes speaking", "i need to change my address", "yes"])
    assert "Personal Accident" not in _agent_text(events)


def test_a_volunteered_address_reaches_the_colleague_as_a_suggestion():
    """The model reads these well — wyi@hotmail.com from "w y i alias hotmail
    dot com" in under two seconds — so the colleague should not have to work it
    out again from the raw transcript. Marked unverified, because on
    "…dot z. o. m." the same model returns wyi@hotmail.zom and is sure of it."""
    class _Reader(MockBackend):
        async def complete(self, system, user, lang, max_tokens=None):
            from voicebot.runtime.base import Completion
            return Completion(text="wyi@hotmail.com", latency_ms=900)

    session = CallSession(personas.get("TH-4471-0093"), _Reader())

    async def go():
        events = []
        async for ev in session.start():
            events.append(ev)
        for r in ["Yes speaking",
                  "change my email to w y i alias hotmail dot com"]:
            async for ev in session.on_caller(r):
                events.append(ev)
        return events

    events = asyncio.run(go())
    handoffs = [e for e in events if e.kind == "handoff"]
    assert handoffs, "a change request must still hand off"
    assert "unverified reading: wyi@hotmail.com" in handoffs[0].outstanding
    # Suggested to a colleague, never spoken and never stored.
    assert "wyi@hotmail.com" not in _agent_text(events)
    assert session.p.email != "wyi@hotmail.com"


def test_a_bare_change_request_asks_the_model_nothing():
    """"I changed my email address" has no address in it. Asking anyway costs
    the caller a second of silence for a guaranteed NONE."""
    class _Counting(MockBackend):
        calls = 0

        async def complete(self, system, user, lang, max_tokens=None):
            type(self).calls += 1
            return await super().complete(system, user, lang)

    backend = _Counting()
    session = CallSession(personas.get("TH-4471-0093"), backend)

    async def go():
        async for _ in session.start():
            pass
        for r in ["Yes speaking", "oh no, i changed my email address"]:
            async for _ in session.on_caller(r):
                pass

    asyncio.run(go())
    assert _Counting.calls == 0
