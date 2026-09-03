"""Not asking what the caller has already answered.

Off-script questions were always handled — the caller can ask about the
premium, the cover or the process at any point and the call answers and picks
up where it was. What stayed rigid was the sequence: every turn asked its
question whether or not the caller had already answered it one turn earlier.

Reported as the call "following the script" rather than listening, and that is
exactly what it sounded like.
"""
import asyncio

from voicebot.call import script
from voicebot.call.engine import CallSession
from voicebot.data import personas
from voicebot.events import Transcript
from voicebot.runtime.mock import MockBackend

P = personas.get("TH-4471-0093")


def _run(replies, lang="en"):
    session = CallSession(P, MockBackend())

    async def go():
        out = []
        async for ev in session.start():
            out.append(ev)
        for r in replies:
            async for ev in session.on_caller(r, lang=lang):
                out.append(ev)
        return out
    return asyncio.run(go()), session


def _said(events):
    return [e.text for e in events
            if isinstance(e, Transcript) and e.speaker == "agent"]


def test_a_question_answered_early_is_not_asked_again():
    """"I haven't received anything" at turn 2 answers turn 3. Asking it
    anyway is the clearest way a call announces nobody is listening."""
    events, session = _run(["Yes speaking", "no, i haven't received anything.", "ok"])
    assert "notice" in session._answered
    assert not any("received a renewal notice" in t for t in _said(events))


def test_the_disclosure_survives_the_question_going():
    """Only the question goes. Turn 3 states the due date as well, and dropping
    the whole turn would silently drop a disclosure the client requires."""
    events, _ = _run(["Yes speaking", "no, i haven't received anything.", "ok"])
    assert any("due date is" in t for t in _said(events))


def test_the_question_is_still_asked_when_nobody_answered_it():
    events, session = _run(["Yes speaking", "uh yes, that is my home."])
    assert "notice" not in session._answered
    assert any("renewal notice" in t for t in _said(events))


def test_every_turn_that_asks_something_names_what_it_asks():
    """The `ask` key is what lets a turn drop its question. A turn that seeks a
    confirmation without naming it can never be answered early.

    Not always shorter: turn 2 carries its question in the punctuation rather
    than in a sentence of its own, so answering it turns a question into the
    statement of purpose it already was."""
    for turn in script.TURNS:
        if turn.ask is not None:
            plain = script.render(turn.n, P, "en")
            short = script.render(turn.n, P, "en", answered=frozenset({turn.ask}))
            assert short != plain, turn.name
            assert not short.endswith("?"), turn.name


def test_both_forms_are_warmed():
    """A turn spoken in a form nobody rendered is two seconds of silence in the
    middle of the turn this feature exists to shorten."""
    from voicebot.runtime.warm import plan

    jobs = {t for t, _, _ in plan(["en"], ["standard"], [None])}
    for turn in script.TURNS:
        if turn.ask is None:
            continue
        assert script.render(turn.n, P, "en") in jobs
        assert script.render(turn.n, P, "en",
                             answered=frozenset({turn.ask})) in jobs


def test_an_unanswered_turn_renders_exactly_as_before():
    """The default path is untouched: no answered confirmations, no change."""
    for turn in range(1, 8):
        assert (script.render(turn, P, "en")
                == script.render(turn, P, "en", answered=frozenset()))


# --- answering without starting the call over -----------------------------
# Reported: several turns in, the bot greeted the caller a second time. They
# said "why you keep repeating yourself?", which is the right question.

def test_answering_a_question_does_not_repeat_the_greeting():
    """Only the outstanding question comes back. The preamble was heard."""
    events, _ = _run(["what is this about?", "yes speaking"])
    after = _said(events)[1]
    assert "Am I speaking with Mr Tan?" in after
    assert "Good afternoon" not in after
    assert "This is Michael calling from" not in after


def test_a_repeat_request_still_gets_the_whole_line():
    """"Sorry, can you repeat?" wants all of it — the distinction the
    outstanding-question accessor exists to draw."""
    events, _ = _run(["yes speaking", "sorry. can you repeat?"])
    assert any("servicing call" in t for t in _said(events)[-1:])


def test_a_confirmation_is_not_read_as_a_question_about_the_call():
    """"yes, i am certain what happened" is a caller confirming who they are.
    Matched anywhere, "what happened" made it a question about the call, so
    the bot answered one nobody asked and read the opening line again."""
    events, session = _run(["yes, i am certain what happened."])
    assert session.gates.identity == "pass"
    assert "Good afternoon" not in _said(events)[-1]


def test_one_foreign_fragment_does_not_end_the_call():
    """Handing off is not reversible. A caller who has spoken English all call
    and produces two short Tamil-looking words was far more likely misheard."""
    events, _ = _run(["yes speaking", "ஆன் நோம்", "sorry, i mean yes"])
    assert not [e for e in events if e.kind == "handoff"]


def test_two_in_a_row_is_a_language():
    events, _ = _run(["yes speaking", "ஆன் நோம் வணக்கம்", "எனக்கு தமிழ் தெரியும்"])
    handoffs = [e for e in events if e.kind == "handoff"]
    assert handoffs and handoffs[0].reason == "language"
