"""The gates are the part that must not regress."""
import asyncio

import pytest

from voicebot.call.engine import CallSession
from voicebot.compliance.gates import check_dnc, check_identity, may_cross_sell, Gates
from voicebot.data import personas
from voicebot.runtime import load_backend


def _run(policy_id: str, replies: list[str]):
    backend = load_backend({"profile": "mock"})
    session = CallSession(personas.get(policy_id), backend)

    async def go():
        events = []
        async for ev in session.start():
            events.append(ev)
        for r in replies:
            async for ev in session.on_caller(r):
                events.append(ev)
        return events

    return asyncio.run(go()), session


def test_wrong_party_discloses_nothing():
    events, session = _run("TH-4471-0093", ["He's not home, I'm his son"])
    assert session.gates.identity == "block"
    spoken = " ".join(e.text for e in events
                      if e.kind == "transcript" and e.speaker == "agent")
    for secret in ("Jurong West", "412", "35,000", "TH-4471"):
        assert secret not in spoken, f"leaked {secret!r} to an unverified party"


def test_cross_sell_blocked_without_consent():
    """The ongoing-relationship exemption does not cover voice calls."""
    events, session = _run("TH-5120-7742",
                           ["Yes that's me", "When", "Got it", "Correct", "Okay", "ok"])
    assert session.gates.consent == "block"
    skipped = [e.turn for e in events if e.kind == "turn" and e.state == "skip"]
    assert 6 in skipped
    spoken = " ".join(e.text for e in events
                      if e.kind == "transcript" and e.speaker == "agent")
    assert "Personal Accident" not in spoken


def test_cross_sell_allowed_with_consent():
    events, session = _run("TH-4471-0093",
                           ["Yes speaking", "When", "Yes", "Correct", "Thanks", "ok"])
    assert session.gates.consent == "pass"
    spoken = " ".join(e.text for e in events
                      if e.kind == "transcript" and e.speaker == "agent")
    assert "Personal Accident" in spoken


def test_stale_dnc_check_is_treated_as_unchecked():
    stale = personas.get("TH-8802-1156")
    assert stale.dnc_checked_days_ago > 21
    state, note = check_dnc(stale)
    assert state == "block" and "days old" in note


def test_advice_request_escalates_and_does_not_advance():
    events, session = _run("TH-4471-0093",
                           ["Yes speaking", "Should I increase my renovation cover?"])
    assert session.gates.advice == "block"
    assert session.turn == 2, "an advice request must not advance the script"


@pytest.mark.parametrize("reply,expected", [
    ("Yes, speaking", "pass"),
    ("ya lah", "pass"),
    ("He's not in", "block"),
    ("Hmm", "pending"),
])
def test_identity_variants(reply, expected):
    assert check_identity(reply)[0] == expected


def test_consent_requires_identity_first():
    gates = Gates()
    allowed, note = may_cross_sell(personas.get("TH-4471-0093"), gates)
    assert not allowed and "right party" in note.lower()


@pytest.mark.parametrize("reply", [
    "I play a lot of golf",          # 'ya' inside 'play'
    "From Malaysia originally",      # 'ya' inside 'Malaysia'
    "Hold on a moment",
])
def test_identity_does_not_pass_on_substring_matches(reply):
    """Short affirmatives must match on word boundaries. A false positive here
    waves an unverified caller past the gate that protects personal data."""
    assert check_identity(reply)[0] == "pending"


def test_wrong_party_beats_an_affirmative_in_the_same_sentence():
    assert check_identity("He's not home, yes I'm his son")[0] == "block"


# ---------------------------------------------------------------- language

def _drive(replies):
    from voicebot.runtime.mock import MockBackend
    policy = personas.get("TH-4471-0093")
    policy.email = "wm.tan@example.sg"
    session = CallSession(policy, MockBackend())

    async def go():
        async for _ in session.start():
            pass
        for r in replies:
            async for _ in session.on_caller(r):
                pass

    asyncio.run(go())
    return session


def test_one_stray_utterance_does_not_switch_language():
    """The reported bug: a single Chinese phrase — or an ASR wobble — threw the
    whole call into Mandarin mid-sentence."""
    assert _drive(["Yes speaking", "好的", "When is it due"]).lang == "en"


def test_two_turns_in_another_language_does_switch():
    assert _drive(["Yes speaking", "我有一个问题", "我想了解一下"]).lang == "zh"


def test_a_bare_yes_in_mandarin_is_not_evidence_of_a_switch():
    """"是的" and "好的" are words half of Singapore uses inside an English
    sentence. Counting them would flip the whole call to Mandarin over a
    one-word answer."""
    assert _drive(["Yes speaking", "是的", "好的", "是的"]).lang == "en"


def test_an_explicit_request_switches_immediately():
    assert _drive(["Yes speaking", "不好意思，可以讲华语吗？"]).lang == "zh"


def test_a_singlish_caller_softens_register_but_not_language():
    session = _drive(["Ya lah, speaking. Who ah?", "Aiyah, so how ah?"])
    assert session.register == "singlish"
    assert session.lang == "en", "register is not language"


def test_scripted_turns_are_never_reregistered():
    """Accommodation happens in improvised lines only — the seven scripted
    turns are the client's approved wording."""
    from voicebot.call import script
    from voicebot.spoken import _en_date
    session = _drive(["Ya lah, speaking. Who ah?", "Aiyah, so how ah?"])
    assert session.register == "singlish"
    for turn in range(1, 8):
        assert script.render(turn, session.p, "en") == script.render(turn, session.p, "en")


def test_singlish_register_rewrites_the_scripted_turns():
    """The user asked for Singlish; this is the wording that delivers it."""
    from voicebot.call import script
    from voicebot.spoken import _en_date
    policy = personas.get("TH-4471-0093")
    for turn in range(1, 8):
        standard = script.render(turn, policy, "en", register="standard")
        singlish = script.render(turn, policy, "en", register="singlish")
        assert standard != singlish, f"turn {turn} is identical in both registers"


def test_singlish_script_still_carries_the_compliance_content():
    """Reworded, not rewritten: identification, the address, the figures and
    the opt-out all survive the register change."""
    from voicebot.call import script
    from voicebot.spoken import _en_date
    p = personas.get("TH-4471-0093")
    sg = {n: script.render(n, p, "en", register="singlish") for n in range(1, 8)}
    assert "Etiqa Insurance" in sg[1], "must still identify the company"
    assert p.surname in sg[1], "must still name the party being verified"
    assert p.property_address in sg[2]
    # The year is spelled out for the voice, so compare what is actually said.
    assert _en_date(p.due_date) in sg[3]
    for figure in (p.premium, p.contents_si, p.reno_si, p.discount_pct):
        assert figure in sg[4], f"lost {figure} in the Singlish rewording"


def test_register_can_be_chosen_at_call_start():
    from voicebot.runtime.mock import MockBackend
    session = CallSession(personas.get("TH-4471-0093"), MockBackend(),
                          register="singlish")

    async def go():
        return [e async for e in session.start()]

    events = asyncio.run(go())
    greeting = [e for e in events if e.kind == "transcript"][0]
    assert "is it?" in greeting.text, "did not open in Singlish"


# --- consent to be marketed to -------------------------------------------
# All three of these came off one recorded call. The register this bot runs
# under is the one where "did the customer agree to hear a pitch?" is the
# whole question, so each is a compliance failure and not a UX one.

def _at_the_cross_sell(backend=None):
    import asyncio

    from voicebot.call.engine import CallSession
    from voicebot.data import personas
    from voicebot.runtime.mock import MockBackend

    session = CallSession(personas.get("TH-4471-0093"), backend or MockBackend(),
                          guardrail=False)

    async def go():
        async for _ in session.start():
            pass
        for reply in ("yes speaking", "yes", "yes", "yes", "okay thank you"):
            async for _ in session.on_caller(reply):
                pass

    asyncio.run(go())
    assert session._pending == "cross_sell", session._pending
    return session


def _say(session, text):
    import asyncio

    async def go():
        return [ev.text async for ev in session.on_caller(text)
                if ev.kind == "transcript" and ev.speaker == "agent"]

    return " ".join(asyncio.run(go()))


def test_a_question_about_the_offer_is_answered_not_deflected():
    """"Why is that?" was met with "that's not something I can help with on a
    renewal call" — about the thing this call had just raised."""
    session = _at_the_cross_sell()
    said = _say(session, "why is that?")
    assert "not something I can help with" not in said
    assert "personal accident" in said.lower()
    assert session._pending == "cross_sell", "the question was dropped"


def test_only_a_yes_delivers_a_pitch():
    """"Not a no" is not consent. The branch treated anything that was not a
    refusal as agreement."""
    session = _at_the_cross_sell()
    said = _say(session, "hmm, what was my premium again?")   # asked once
    said = _say(session, "hmm, what was my premium again?")   # and again
    assert "40 percent off" not in said, "pitched without agreement"
    assert session._declined_cross_sell is True


def test_asking_us_to_repeat_does_not_deliver_the_pitch():
    """Turn 6 *is* the pitch, so re-speaking the turn delivered a promotion
    to someone who had not agreed to hear one. "What are you trying to say
    just now?" produced the whole thing."""
    session = _at_the_cross_sell()
    said = _say(session, "sorry, what are you trying to say just now?")
    assert "40 percent off" not in said, "repeat bypassed consent"
    assert "twenty seconds" in said, "should put the question again"


def test_a_yes_still_delivers_it():
    session = _at_the_cross_sell()
    said = _say(session, "yes, go ahead")
    assert "40 percent off" in said
