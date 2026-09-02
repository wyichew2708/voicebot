"""Correcting the email is the commonest off-script turn on a renewal call —
turn 4 literally invites it — and the first build ignored it entirely."""
import asyncio

import pytest

from voicebot.call.engine import CallSession
from voicebot.data import personas
from voicebot.data.facts import spoken_email, wants_email_change
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


def test_the_bot_answers_an_email_change_request():
    """The reported bug: it carried on with the script as if nothing was said."""
    events, _ = _run(TO_TURN_4 + ["No, please change my email"])
    assert "email address you'd like us to use" in _agent_text(events)
    assert "crm.flag_email_change" in _tools(events)


def test_a_corrected_address_is_read_back_before_it_is_stored():
    events, session = _run(TO_TURN_4 + ["Change my email",
                                        "j dot chew at outlook dot com"])
    assert "j.chew@outlook.com — is that right?" in _agent_text(events)
    assert session.p.email == "wm.tan@example.sg", "stored before confirmation"


def test_confirmed_address_is_stored_and_the_script_resumes():
    events, session = _run(TO_TURN_4 + ["Change my email",
                                        "j dot chew at outlook dot com", "Yes correct"])
    assert session.p.email == "j.chew@outlook.com"
    assert "crm.update_email" in _tools(events)
    assert session.turn == 5, "the call should continue after the correction"


def test_unintelligible_address_hands_off_rather_than_guessing():
    """A one-character error means the renewal notice goes nowhere. Three
    tries, then a person — and the call stops there.

    It used to carry on with the script after giving up, so the cross-sell
    landed on a customer who had just been told we could not help them.
    """
    events, session = _run(TO_TURN_4 + ["Change my email", "erm hold on",
                                        "actually never mind", "hmm"])
    assert session.p.email == "wm.tan@example.sg", "must not store a guess"
    handoffs = [e for e in events if e.kind == "handoff"]
    assert handoffs and handoffs[0].reason == "data_change"
    assert "new email address" in handoffs[0].outstanding
    said = _agent_text(events)
    assert "Personal Accident" not in said, "pitched a product after failing them"
    assert "off by one character" in said, "never said why we were handing over"


# --- the caller who was still talking ------------------------------------

def test_a_caller_still_spelling_stays_in_the_sub_dialogue():
    """Reported: the bot gave up while the caller was mid-address, and every
    further piece — "and one, two, three, four" — came back as "sorry, I
    didn't quite catch that"."""
    events, session = _run(TO_TURN_4 + ["Change my email",
                                        "r w y i one two three four a l i a s"])
    said = _agent_text(events)
    assert "didn't quite catch" not in said, "treated dictation as an interruption"
    assert session._email_state is not None, "abandoned the caller mid-address"


def test_a_partial_hearing_asks_for_the_missing_part():
    """"Say it again slowly" is the wrong ask when they were saying it slowly."""
    events, _ = _run(TO_TURN_4 + ["Change my email", "r w y i one two three four"])
    assert "part before the at sign" in _agent_text(events)


def test_three_tries_before_a_person_not_two():
    events, session = _run(TO_TURN_4 + ["Change my email", "a b c", "d e f"])
    assert session._email_state is not None, "gave up on the second attempt"
    assert not [e for e in events if e.kind == "handoff"]


def test_every_fragment_the_caller_spelled_reaches_the_colleague():
    """They spelled the address across three turns. Handing over only the tail
    of it makes the colleague start from nothing, which is the whole thing
    this record exists to prevent."""
    events, _ = _run(TO_TURN_4 + ["Change my email", "a b c", "d e f", "g h i"])
    h = [e for e in events if e.kind == "handoff"][0]
    for fragment in ("a b c", "d e f", "g h i"):
        assert fragment in h.outstanding, f"lost {fragment!r}"


# --- from one recorded call ------------------------------------------------

def test_asking_to_change_the_address_does_not_read_back_the_old_one():
    """"Can I request to change my email address?" was answered with "we have
    wm.tan@example.sg on file" — the field named, rather than the change
    asked for. A request to change a field is not a request to read it."""
    events, session = _run(["Yes speaking", "uh, can i request to change my email address?"])
    said = _agent_text(events).lower()
    assert "could you give me the email address" in said
    assert session.p.email not in said.split("could you")[0], "read the old one back"


@pytest.mark.parametrize("said,want", [
    ("w, y, i, c, h, e, w, alias hotmail dot com.", "wyichew@hotmail.com"),
    ("w, y, i, c, h, e, w, at hotmail dot com.", "wyichew@hotmail.com"),
    ("w y i c h e w at hotmail dot com", "wyichew@hotmail.com"),
    ("please use wm.tan@example.sg instead.", "wm.tan@example.sg"),
])
def test_a_spelled_address_survives_the_recogniser(said, want):
    """Two real failures here: "alias" is what the recogniser made of "@",
    and a sentence stop parsed all the way through to "…hotmail.com." which
    then failed its own validation."""
    from voicebot.data.facts import spoken_email
    assert spoken_email(said) == want


def test_being_told_we_should_already_know_does_not_cost_an_attempt():
    """"You should be able to know." — answered with "say it again, slowly",
    which is the machine confirming it is not listening, and it burned one of
    the caller's three tries."""
    from voicebot.call.engine import CallSession
    from voicebot.data import personas
    from voicebot.runtime.mock import MockBackend

    session = CallSession(personas.get("TH-4471-0093"), MockBackend(), guardrail=False)
    said = []

    async def go():
        async for _ in session.start():
            pass
        for reply in ("Yes speaking", "i want to change my email address",
                      "uh, you should be able to know."):
            async for ev in session.on_caller(reply):
                if ev.kind == "transcript" and ev.speaker == "agent":
                    said.append(ev.text)

    asyncio.run(go())
    assert session._email_attempts == 0, "a non-attempt was counted as one"
    assert "would have to come from you" in said[-1]
    assert session.p.email in said[-1], "say which address we actually hold"
