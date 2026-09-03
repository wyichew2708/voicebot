"""Handing the call to a person, and knowing when not to sell.

Both of these come from one recorded call. The bot told the customer it could
not update their email address and a colleague would call — and then, in the
very next breath, pitched personal accident cover at them.
"""
import asyncio

import pytest

from voicebot.call import handoff as ho
from voicebot.call.engine import CallSession
from voicebot.compliance.gates import CallState, Gates, may_cross_sell
from voicebot.data import personas
from voicebot.runtime.mock import MockBackend

TO_TURN_4 = ["Yes speaking", "When", "Yes"]


def _run(replies, policy_id="TH-4471-0093"):
    policy = personas.get(policy_id)
    policy.email = "wm.tan@example.sg"
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


def _said(events):
    return " ".join(e.text for e in events
                    if e.kind == "transcript" and e.speaker == "agent")


def _handoffs(events):
    return [e for e in events if e.kind == "handoff"]


# --- the procedure --------------------------------------------------------

def test_asking_for_a_person_is_honoured_immediately():
    events, session = _run(["Yes speaking", "can I speak to a real person?"])
    hs = _handoffs(events)
    assert hs and hs[0].reason == "requested"
    assert session.handoff is not None


def test_a_handoff_says_why_what_happens_and_checks_it_can_reach_them():
    """The three things a callback promise needs to be worth anything."""
    events, _ = _run(["Yes speaking", "get me someone else"])
    said = _said(events)
    assert "colleague call you back" in said, "never said what happens next"
    assert "won't have to repeat yourself" in said, "no reason to believe it"
    assert "number ending" in said, "never checked we can reach them"


def test_the_callback_number_is_read_back_four_digits_at_a_time():
    events, _ = _run(["Yes speaking", "transfer me"])
    assert "4 4 1 7" in _said(events), "read the whole number out, or none of it"


def test_confirming_the_number_closes_the_call():
    events, session = _run(["Yes speaking", "transfer me", "yes that's right"])
    assert session.ended
    assert "within one working day" in _said(events)
    assert any(e.kind == "end" and "colleague" in e.outcome for e in events)


def test_a_wrong_number_is_logged_rather_than_argued_with():
    """Capturing a new number over the same line that just failed us is how
    the callback ends up going nowhere twice."""
    events, session = _run(["Yes speaking", "transfer me", "no, that's my old one"])
    assert session.ended
    assert "check the best number" in _said(events)
    assert any(e.kind == "tool" and "callback number" in e.arg for e in events)


def test_the_record_says_what_is_still_outstanding():
    events, _ = _run(["Yes speaking", "put me through"])
    assert "renewal not confirmed" in _handoffs(events)[0].outstanding


def test_the_script_stops_dead_at_a_handoff():
    events, session = _run(["Yes speaking", "transfer me", "yes"])
    said = _said(events)
    assert "Personal Accident" not in said
    assert "final premium" not in said
    assert session.turn < 7


def test_a_handoff_is_a_typed_event_not_a_note():
    """A telephony layer needs something machine-readable to bridge a leg on,
    and an operator needs one thing to act on."""
    events, _ = _run(["Yes speaking", "transfer me"])
    h = _handoffs(events)[0]
    assert h.code == "handoff.requested"
    assert h.warm is False, "there is no telephony leg on this build"
    assert h.summary


def test_only_one_handoff_per_call():
    events, _ = _run(["Yes speaking", "transfer me", "get me a person", "human please"])
    assert len(_handoffs(events)) == 1


@pytest.mark.parametrize("reason", ho.PRIORITY)
def test_every_reason_has_customer_facing_wording_in_both_languages(reason):
    for lang in ("en", "zh"):
        assert ho.why(reason, lang).strip()


def test_being_told_twice_that_we_are_not_listening_fetches_a_person():
    """Repeating a failed approach is what earns the complaint."""
    events, _ = _run(["Yes speaking", "you didn't hear me", "i already said that"])
    hs = _handoffs(events)
    assert hs and hs[0].reason == "complaint"


def test_the_first_complaint_gets_an_apology_and_a_change_of_approach():
    events, session = _run(["Yes speaking", "uh, you didn't hear me."])
    said = _said(events)
    assert "my fault, not yours" in said
    assert "didn't quite catch" not in said, "answered the complaint by repeating it"
    assert session.rate < 1.0, "tried the same thing again at the same speed"


# --- not simply cross-selling --------------------------------------------

def test_the_pitch_is_asked_for_rather_than_delivered():
    events, session = _run(TO_TURN_4 + ["yes correct", "ok"])
    said = _said(events)
    assert "may I take twenty seconds" in said
    assert "Personal Accident" not in said, "pitched before asking"
    assert session._pending == "cross_sell"


def test_declining_the_pitch_ends_it_there():
    events, _ = _run(TO_TURN_4 + ["yes correct", "ok", "no thanks"])
    said = _said(events)
    assert "Personal Accident" not in said
    assert "no problem at all" in said.lower()
    assert any(e.kind == "tool" and "declined" in e.arg for e in events)


def test_accepting_the_pitch_delivers_the_approved_wording_unchanged():
    events, _ = _run(TO_TURN_4 + ["yes correct", "ok", "sure, go ahead"])
    said = _said(events)
    assert "Tiq Personal Accident" in said and "40 percent off" in said


@pytest.mark.parametrize("state,why", [
    (CallState(handing_off=True), "could not help"),
    (CallState(unresolved="an email change"), "unresolved"),
    (CallState(awaiting_adviser=True), "adviser"),
    (CallState(impatient=True), "quick"),
    (CallState(declined=True), "declined"),
    (CallState(comprehension_failures=2), "hearing them"),
])
def test_the_cross_sell_reads_the_call_not_just_the_record(state, why):
    """Consent says whether we are permitted to pitch. It says nothing about
    whether we should."""
    gates = Gates()
    gates.set("identity", "pass")
    allowed, note = may_cross_sell(personas.get("TH-4471-0093"), gates, state)
    assert not allowed and why in note


def test_a_clean_call_still_reaches_the_pitch():
    gates = Gates()
    gates.set("identity", "pass")
    allowed, _ = may_cross_sell(personas.get("TH-4471-0093"), gates, CallState())
    assert allowed


# --- the sub-dialogue keeps its own counters ------------------------------

def test_asking_again_does_not_open_a_second_handoff():
    """A caller who repeats themselves is a caller who has not been told
    clearly enough. Repeating the request must not queue a second colleague."""
    events, _ = _run(TO_TURN_4 + ["I changed my email address",
                                  "may i change my email address?",
                                  "i want to change my email address"])
    assert len(_handoffs(events)) == 1


def test_a_change_request_never_repeats_the_premium_line():
    """It used to answer a complaint mid-dialogue by reciting the premium,
    which is its own kind of not listening. The dialogue is gone; the line it
    must not fall back to is not."""
    events, _ = _run(TO_TURN_4 + ["change my email", "uh, you didn't hear me."])
    said = [e.text for e in events
            if e.kind == "transcript" and e.speaker == "agent"]
    assert "final premium" not in said[-1]


def test_still_talking_is_not_an_answer_about_the_callback_number():
    """Reading "Perfect, they'll be in touch" over someone mid-address
    confirms a number they never answered about."""
    events, session = _run(TO_TURN_4 + ["change my email", "a b c", "d e f",
                                        "g h i", "and one two three four"])
    said = _said(events)
    assert "Perfect" not in said
    assert "check the best number" in said
    assert session.ended


# --- "can I get a discount?" ---------------------------------------------
# The most predictable question on a renewal call. The bot had no answer for
# it: it asked the caller to repeat themselves three times and then handed the
# call over as a line-quality problem.

@pytest.mark.parametrize("said", [
    "uh, can i get a discount?", "is there any discount?",
    "the premium is four one two dollar. can it be cheaper?",
    "too expensive", "can you bring it down a bit", "有折扣吗",
])
def test_a_price_question_is_recognised(said):
    from voicebot.data.facts import is_price_request
    assert is_price_request(said)


def test_a_price_question_is_answered_from_the_record():
    events, _ = _run(TO_TURN_4 + ["uh, can i get a discount?"])
    said = _said(events)
    assert "23.5 percent discount" in said, "did not say what is already applied"
    assert "not able to change the price myself" in said, "implied it could negotiate"
    assert "didn't quite catch" not in said


def test_a_price_question_offers_a_colleague_and_acts_on_yes():
    events, session = _run(TO_TURN_4 + ["can it be cheaper?", "yes please"])
    hs = _handoffs(events)
    assert hs and hs[0].reason == "pricing"
    assert "underwriting" in _said(events)
    assert session.handoff is not None


def test_declining_the_pricing_review_carries_on_with_the_call():
    events, session = _run(TO_TURN_4 + ["any discount?", "no it's fine"])
    assert not _handoffs(events)
    assert session.turn > 4, "the call stalled after the question was settled"


def test_three_price_questions_do_not_add_up_to_a_line_problem():
    """Reported: the same question asked three times, each answered with
    "sorry, I didn't quite catch that", then escalated as line quality."""
    events, session = _run(TO_TURN_4 + ["can i get a discount?", "no",
                                        "is there any discount?", "no",
                                        "what about a discount", "no"])
    assert not [e for e in _handoffs(events) if e.reason == "not_understood"]
    assert session.handoff is None


def test_answering_a_question_clears_the_not_understood_tally():
    """Three clarifies spread across an otherwise productive call used to add
    up to a handoff."""
    events, session = _run(["Yes speaking", "zzz zzz", "how much is the premium?",
                            "blah blah", "when is it due?", "mmm mmm"])
    assert not _handoffs(events)
    assert session.handoff is None


# --- Tamil ----------------------------------------------------------------

def test_tamil_is_handed_to_someone_who_speaks_it():
    """One of Singapore's four official languages, and the recogniser handles
    it. We cannot speak it, so the only honest answer is a colleague who can."""
    # Twice, because once is a fragment: a caller who has spoken English all
    # call and produces one Tamil-looking line was far more likely misheard,
    # and a handoff is not reversible.
    events, session = _run(["Yes speaking", "என்னி திச்காம் வணக்கம்",
                            "எனக்கு தமிழ் தெரியும்"])
    hs = _handoffs(events)
    assert hs and hs[0].reason == "language"
    assert "Tamil" in hs[0].summary
    assert session.handoff is not None


def test_a_single_stray_glyph_does_not_end_the_call():
    from voicebot.call.engine import _looks_tamil
    assert not _looks_tamil("ok ஒ")
    assert _looks_tamil("வணக்கம்")


# --- a "no" that means something -----------------------------------------

def test_saying_the_notice_never_arrived_is_heard():
    """Turn 3 asks whether the renewal notice arrived. "No" is a fact about
    this customer, not a refusal, and gliding past it leaves them none the
    wiser about the document the rest of the call refers to."""
    events, _ = _run(["Yes speaking", "yes", "uh, no."])
    said = _said(events)
    assert "let me make sure it reaches you" in said
    assert any(e.kind == "tool" and e.tool == "crm.flag_notice_not_received"
               for e in events)


def test_the_language_handoff_names_the_language_we_actually_heard():
    """Hard-coded wording told a Tamil caller, in English, that we could not
    speak Malay."""
    events, _ = _run(["Yes speaking", "என்னி திச்காம் வணக்கம்",
                      "எனக்கு தமிழ் தெரியும்"])
    said = _said(events)
    assert "in Tamil yet" in said and "Malay" not in said


def test_the_malay_handoff_still_says_malay():
    events, _ = _run(["Yes speaking", "saya tidak boleh cakap"])
    assert "in Malay yet" in _said(events)


def test_a_price_question_that_also_quotes_the_premium_is_still_a_price_question():
    """"The premium is 412 dollars, can it be cheaper?" was answered by
    reading the premium back at them."""
    events, _ = _run(TO_TURN_4 + ["the premium is four one two dollar. "
                                  "can it be cheaper?"])
    said = _said(events)
    assert "not able to change the price myself" in said


def test_the_reason_is_not_repeated_to_someone_who_just_agreed_to_it():
    """The officer offer opens with the same sentence the handoff explains
    with. Said twice in two turns, to a caller who has just said yes to it,
    it reads as the bot not listening to itself."""
    import asyncio

    from voicebot.call.engine import CallSession, OFFICER_OFFER
    from voicebot.data import personas
    from voicebot.runtime.mock import MockBackend

    session = CallSession(personas.get("TH-4471-0093"), MockBackend(), lang="zh")

    async def go():
        async for _ in session.start():
            pass
        async for _ in session.on_caller("呃对"):
            pass
        session._pending = "officer"
        return [ev.text async for ev in session.on_caller("可以可以请安排")
                if ev.kind == "transcript" and ev.speaker == "agent"]

    said = " ".join(asyncio.run(go()))
    assert session.handoff is not None
    opener = OFFICER_OFFER["zh"].split("，")[0]
    assert opener not in said, f"said it again: {said}"
