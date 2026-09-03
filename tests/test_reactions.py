import pathlib

"""A caller who says "sorry, can you repeat?" and hears the *next* line is
being talked at, not talked to. These cover the three things a caller does
that the seven-turn script has no slot for."""
import asyncio

import pytest

from voicebot.call.engine import CallSession
from voicebot.call.reactions import bridge_kind, wants_callback, wants_repeat
from voicebot.data import personas
from voicebot.runtime.mock import MockBackend


def _run(replies, **kw):
    session = CallSession(personas.get("TH-4471-0093"), MockBackend(), **kw)

    async def go():
        events = []
        async for ev in session.start():
            events.append(ev)
        for r in replies:
            async for ev in session.on_caller(r):
                events.append(ev)
        return events

    return asyncio.run(go()), session


def _agent(events):
    return [e.text for e in events if e.kind == "transcript" and e.speaker == "agent"]


@pytest.mark.parametrize("said", [
    "uh, can you repeat?", "sorry?", "say that again", "what was that",
    "huh", "I didn't catch that", "can you speak louder", "再说一次",
])
def test_repeat_requests_are_recognised(said):
    assert wants_repeat(said)


@pytest.mark.parametrize("said", [
    "Yes, speaking.", "sorry, yes that's me", "what's covered under renovation?",
    "Yes, I received it", "no lah",
])
def test_ordinary_replies_are_not_repeat_requests(said):
    """"sorry, yes that's me" is an answer. Treating the apology as a request
    to repeat would stall the call on turn one."""
    assert not wants_repeat(said)


@pytest.mark.parametrize("said", [
    "I'm driving now", "can you call me later", "not a good time",
    "I'm in a meeting", "现在不方便，等下再打",
])
def test_bad_time_is_recognised(said):
    assert wants_callback(said)


def test_repeat_says_the_same_turn_again_instead_of_advancing():
    events, session = _run(["Yes speaking", "uh, can you repeat?"])
    said = _agent(events)
    assert session.turn == 2, "the script moved on past a caller who missed it"
    assert said[-1].endswith(said[-2].split(". ", 1)[-1][-40:]) or \
        said[-2][-40:] in said[-1], "the repeat is not the line that was missed"
    assert "Of course" in said[-1], "repeated with no acknowledgement at all"


def test_a_second_repeat_apologises_rather_than_saying_of_course_again():
    events, _ = _run(["Yes speaking", "sorry?", "sorry, again?"])
    said = _agent(events)
    assert "Of course" in said[-2] and "Of course" not in said[-1]
    assert "again" in said[-1].lower()


def test_repeating_forever_hands_over_to_a_person():
    """Saying the same sentence a fourth time is not going to work."""
    events, session = _run(["Yes speaking"] + ["sorry?"] * 4)
    handoffs = [e for e in events if e.kind == "handoff"]
    assert handoffs and handoffs[0].reason == "not_understood"
    assert session.handoff is not None


def test_a_bad_time_ends_the_call_without_the_cross_sell():
    events, session = _run(["Yes speaking", "sorry, I'm driving now"])
    assert session.ended
    said = " ".join(_agent(events))
    assert "another time" in said
    assert "Personal Accident" not in said, "pitched a product to someone driving"
    assert any(e.kind == "tool" and e.arg.startswith("bad time") for e in events)


def test_a_bad_time_is_honoured_before_the_identity_gate():
    """Letting someone go is not a disclosure, so it does not wait on the
    right-party check."""
    events, session = _run(["sorry, can you call me later"])
    assert session.ended
    assert "another time" in " ".join(_agent(events))


@pytest.mark.parametrize("said,kind", [
    ("uh, yes, anything?", "impatient"),
    ("Yes, speaking.", "assent"),
    ("uh", "hesitant"),
    ("what's covered under renovation?", None),
])
def test_bridge_reads_the_tone_of_the_reply(said, kind):
    assert bridge_kind(said) == kind


def test_the_next_line_acknowledges_what_the_caller_said():
    events, _ = _run(["uh, yes, anything?"])
    assert _agent(events)[-1].startswith("I'll keep this short.")


def test_the_same_acknowledgement_is_never_used_twice():
    """Hearing "Thank you." open four turns running is its own kind of robotic."""
    events, _ = _run(["Yes speaking", "Yes ok", "Yes", "Yes", "Yes"])
    said = _agent(events)
    assert sum(t.startswith("Thank you.") for t in said) <= 1


def test_acknowledgements_do_not_stack_on_consecutive_turns():
    events, _ = _run(["uh, yes, anything?", "uh, ok"])
    said = _agent(events)
    assert not (said[-1].startswith("No problem.") and said[-2].startswith("I'll keep"))


def test_the_acknowledgement_and_the_line_arrive_as_one_clip():
    """Two AgentAudio events would be two loads of the player, and the second
    cuts the first off mid-word."""
    events, _ = _run(["uh, yes, anything?"])
    audio = [e for e in events if e.kind == "audio"]
    turns = [e for e in events if e.kind == "transcript" and e.speaker == "agent"]
    assert len(audio) == len(turns), "an acknowledgement was sent as its own clip"


def test_the_join_does_not_leave_a_gap_where_the_line_sounds_dropped():
    """Both clips arrive with TTS padding — up to half a second on the tail.
    Joined untrimmed, one sentence gets three quarters of a second of dead air
    in the middle of it."""
    from voicebot import pcm

    sr = 16000
    tone = (b"\x00\x40" * (sr // 4))                     # 250 ms of signal
    pad = bytes(sr // 2 * 2)                             # 500 ms of silence
    kept = pcm.trim(pad + tone + pad, sample_rate=sr)
    assert abs(len(kept) / 2 / sr - 0.29) < 0.02, "padding survived the trim"


def test_trim_hands_back_a_clip_that_is_entirely_quiet():
    from voicebot import pcm

    quiet = bytes(1600)
    assert pcm.trim(quiet, sample_rate=16000) == quiet


# --- "can you speak slower?" ---------------------------------------------
# The reported failure: asked once, the script advanced; asked again, more
# emphatically, the script advanced again — so the caller heard the *next*
# line faster than the one they had already missed.

@pytest.mark.parametrize("said", [
    "can you speak slower?", "no, no, no, i mean, can you speak slower?",
    "slower", "too fast", "slow down please", "说慢一点",
])
def test_a_request_to_slow_down_is_recognised(said):
    from voicebot.call.reactions import wants_slower
    assert wants_slower(said)


def test_slowing_down_repeats_the_line_the_caller_missed():
    events, session = _run(["Yes speaking", "can you speak slower?"])
    said = _agent(events)
    assert session.turn == 2, "the script advanced past the line they missed"
    assert session.rate < 1.0
    assert said[-1].startswith("Of course, I'll slow down.")
    assert "servicing call" in said[-1], "slowed down, but said something else"


def test_the_slower_pace_holds_for_the_rest_of_the_call():
    """As it would with a person. Reverting to full speed on the next line is
    what made the caller ask twice."""
    _, session = _run(["Yes speaking", "can you speak slower?", "yes ok"])
    assert session.rate < 1.0 and session.turn == 3


def test_asking_twice_slows_down_further():
    _, session = _run(["Yes speaking", "slower please", "still too fast"])
    assert session.rate < 0.8


def test_slower_audio_is_actually_longer():
    fast, _ = _run(["Yes speaking"])
    slow, _ = _run(["Yes speaking", "can you speak slower?"])
    a = [e for e in fast if e.kind == "audio"][-1]
    b = [e for e in slow if e.kind == "audio"][-1]
    assert len(b.pcm) > len(a.pcm) * 1.1, "the pace changed on paper only"


def test_slowing_down_does_not_move_the_pitch():
    """Resampling would be one line and would turn the agent into a deeper,
    different person — the opposite of what the request asks for."""
    import array
    import math

    from voicebot import pcm

    sr = 16000
    tone = array.array("h", (int(8000 * math.sin(2 * math.pi * 200 * i / sr))
                             for i in range(sr)))
    out = pcm.stretch(tone.tobytes(), 0.8, sr)
    got = array.array("h")
    got.frombytes(out)
    mid = got[sr // 4: sr // 4 + sr // 2]
    crossings = sum(1 for i in range(1, len(mid)) if mid[i - 1] < 0 <= mid[i])
    assert abs(crossings / 0.5 - 200) < 12, f"pitch moved to ~{crossings / 0.5:.0f} Hz"
    assert abs(len(out) / len(tone.tobytes()) - 1.25) < 0.05


# --- a reply we could not read is not agreement ---------------------------

def test_a_stray_chinese_phrase_does_not_advance_an_english_call():
    """The reported case: "不乱来啊" arrived mid-call from a mis-recognition
    and the script moved on as though the caller had agreed."""
    events, session = _run(["Yes speaking", "不乱来啊"])
    assert session.turn == 2, "a line we cannot read moved the script on"
    assert "didn't quite catch that" in _agent(events)[-1]


def test_a_second_language_turn_still_switches_rather_than_stalling():
    """Asking again must not become a loop: the switch logic takes over on the
    second consecutive turn."""
    _, session = _run(["Yes speaking", "我要用华语", "麻烦你用华语"])
    assert session.lang == "zh"


def test_two_chinese_turns_running_switch_rather_than_stall():
    """Asking again must not become a loop. One Chinese line in an English
    call is a mis-recognition; two is a caller who has switched language, and
    the switch logic already handles that."""
    _, session = _run(["Yes speaking", "不乱来啊", "我想问一下"])
    assert session.lang == "zh"


def test_three_unreadable_replies_hand_over_to_a_person():
    """Garbage in the call's own language has no switch to fall back on, so it
    needs its own way out."""
    events, session = _run(["Yes speaking", "ah ah ah", "blah blah", "zzz zzz"])
    assert [e.reason for e in events if e.kind == "handoff"] == ["not_understood"]
    assert session.handoff is not None


def test_a_real_question_is_never_treated_as_unintelligible():
    from voicebot.call.reactions import is_uninterpretable
    for said in ("what's covered under renovation?", "how much is the premium",
                 "no I already renewed", "yes that's right"):
        assert not is_uninterpretable(said), said


# --- picking up the phone -------------------------------------------------

def test_hello_testing_is_answered_not_rejected():
    """Reported: "halo, halo, testing, testing." then "hello hello" both came
    back as "sorry, I didn't quite catch that" — the machine failing the
    easiest turn in the call."""
    events, session = _run(["halo, halo, testing, testing."])
    said = _agent(events)[-1]
    assert "didn't quite catch" not in said
    assert "can you hear me" in said.lower()
    assert "policyholder" in said, "reassured them but never asked the question"
    assert session.turn == 1, "a greeting is not a right-party check"


def test_a_greeting_that_also_confirms_identity_still_passes_the_gate():
    _, session = _run(["hello, yes, speaking"])
    assert session.turn == 2 and session.gates.as_dict()["identity"] == "pass"


def test_the_greeting_reply_is_one_clip():
    events, _ = _run(["hello hello"])
    audio = [e for e in events if e.kind == "audio"]
    turns = [e for e in events if e.kind == "transcript" and e.speaker == "agent"]
    assert len(audio) == len(turns)


# --- questions about their own policy -------------------------------------

def test_a_question_about_the_address_is_answered_from_the_record():
    """Reported: asked twice where the property was, and got the premium line
    both times."""
    events, session = _run(["Yes speaking", "uh, i mean, where is my property address?"])
    said = _agent(events)[-1]
    assert "Jurong West Street 4, #08-212" in said
    assert session.turn == 2, "answering a question is not finishing a turn"


def test_a_question_wrapped_in_a_repeat_request_answers_the_question():
    """"what is the address? can you repeat again?" is both, and repeating the
    due-date line answers neither half."""
    events, _ = _run(["Yes speaking", "uh, no, what is the address? can you repeat again?"])
    assert "Jurong West Street 4" in _agent(events)[-1]


@pytest.mark.parametrize("asked,expected", [
    ("how much is the premium?", "412 dollars"),
    ("when is it due?", "twenty twenty-six"),
    ("which email do you have?", "wm.tan@example.sg"),
    ("what is my policy number?", "TH-4471-0093"),
    ("how much am i covered for?", "35,000"),
])
def test_record_questions(asked, expected):
    events, _ = _run(["Yes speaking", asked])
    assert expected in _agent(events)[-1]


def test_the_due_date_answer_speaks_the_year_the_same_way_the_script_does():
    events, _ = _run(["Yes speaking", "when is it due?"])
    assert "2026" not in _agent(events)[-1]


def test_confirming_the_email_is_not_a_question_about_it():
    """Turn 4 ends "can I confirm that's correct?". Reading the address back
    at someone who just said yes would loop the call."""
    events, session = _run(["Yes speaking", "When", "Yes", "yes that's correct"])
    assert session.turn >= 5, "an answer was mistaken for a question"


def test_nothing_is_disclosed_before_the_right_party_check():
    """The record answers sit behind the identity gate like everything else."""
    events, session = _run(["where is my property address?"])
    said = " ".join(_agent(events))
    assert "Jurong West" not in said
    assert session.gates.as_dict().get("identity") != "pass"


def test_a_garbled_slower_request_still_lands():
    """The recogniser produced "can you speak a a slower?" — a phrase list
    never catches that, and there is no other reason to say the word."""
    from voicebot.call.reactions import wants_slower
    assert wants_slower("ah, can you speak a a slower?")
    _, session = _run(["Yes speaking", "ah, can you speak a a slower?"])
    assert session.rate < 1.0 and session.turn == 2


def test_a_caller_who_only_ever_says_hello_gets_a_person():
    """Someone still saying "hello?" on the third try cannot hear us, and
    saying it a fourth time will not fix that."""
    events, session = _run(["hello?", "hello hello", "halo halo"])
    assert [e.reason for e in events if e.kind == "handoff"] == ["not_understood"]
    assert session.handoff is not None


# --- a yes is a yes, in either language -----------------------------------

@pytest.mark.parametrize("said", ["是的。", "ah yes.", "ya, yes.", "好的", "对",
                                  "okay, sure. thanks."])
def test_a_bare_yes_is_understood_however_it_arrives(said):
    from voicebot.call.reactions import bare_answer
    assert bare_answer(said) == "yes"


@pytest.mark.parametrize("said", ["不乱来啊", "uh, no, what is the address?",
                                  "okay, uh, how much to renew?", "hello hello"])
def test_a_phrase_that_merely_contains_a_particle_is_not_an_answer(said):
    from voicebot.call.reactions import bare_answer
    assert bare_answer(said) is None


def test_mandarin_yes_in_an_english_call_advances_the_script():
    """Reported twice in one call: "是的。" came back as "Sorry, I didn't quite
    catch that." — the bot failing to understand a word it demonstrably
    knows."""
    events, session = _run(["Yes speaking", "是的。"])
    assert session.turn == 3, "a plain yes did not move the call on"
    assert "didn't quite catch" not in " ".join(_agent(events))


# --- questions we asked and then ignored ----------------------------------

def test_accepting_the_adviser_callback_is_acted_on():
    """The bot asked "may I arrange for someone to call you back?", the caller
    said "okay, sure", and it moved to the next scripted line as though the
    question had never been put."""
    events, _ = _run(["Yes speaking", "should i increase my cover?",
                      "okay, sure. thanks."])
    said = " ".join(_agent(events))
    assert "advisers call you back" in said
    assert any(e.kind == "tool" and "adviser callback" in e.arg for e in events)


def test_declining_the_adviser_callback_is_also_heard():
    events, _ = _run(["Yes speaking", "should i increase my cover?", "no, it's ok"])
    said = " ".join(_agent(events))
    assert "No problem at all." in said
    assert not any(e.kind == "tool" and "adviser callback" in e.arg for e in events)


def test_the_call_continues_after_the_callback_question_is_settled():
    _, session = _run(["Yes speaking", "should i increase my cover?", "yes please"])
    assert session.turn == 3


# --- procedural questions are not financial advice ------------------------

def test_how_should_i_proceed_is_answered_not_escalated():
    """"How should I proceed?" is a customer asking what to do next. Turn 5 of
    the script is the answer; escalating it to a licensed adviser derails the
    call over a phrase match on "should i"."""
    events, session = _run(["Yes speaking", "okay, how should i proceed?"])
    said = _agent(events)[-1]
    assert "licensed advisers" not in said
    assert "renew before the due date" in said
    assert session.gates.as_dict().get("advice") != "block"


def test_a_real_advice_question_still_escalates():
    events, _ = _run(["Yes speaking", "should i increase my sum insured?"])
    assert "licensed advisers" in _agent(events)[-1]


# --- not repeating what the caller already asked for ----------------------

def test_a_fact_the_caller_asked_for_is_not_read_back_cold():
    """The caller asked the due date and was told it; two turns later the
    script read the same date out as though the conversation hadn't happened."""
    events, _ = _run(["Yes speaking", "when is it due?", "ok", "ok"])
    due_turn = [t for t in _agent(events) if "received a renewal notice" in t]
    assert due_turn and due_turn[0].startswith("As I mentioned,")


def test_an_unasked_fact_is_delivered_normally():
    events, _ = _run(["Yes speaking", "ok", "ok"])
    due_turn = [t for t in _agent(events) if "received a renewal notice" in t]
    assert due_turn and not due_turn[0].startswith("As I mentioned,")


def test_an_advisory_question_never_gets_a_factual_answer_instead():
    """"Should I increase my sum insured?" names a field of the record.
    Answering with that field answers a question nobody asked and skips the
    gate that exists for the one they did."""
    events, session = _run(["Yes speaking", "should i increase my sum insured?"])
    said = _agent(events)[-1]
    assert "licensed advisers" in said
    assert "35,000" not in said
    assert session.gates.as_dict()["advice"] == "block"


def test_the_fallback_runs_last_so_every_handler_gets_a_chance():
    """It sat near the top and swallowed replies the handlers below it knew
    how to answer: "should i increase my sum insured?" came back as "sorry, I
    didn't quite catch that" instead of reaching the advice gate."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "src/voicebot/call/engine.py").read_text()
    body = src[src.index("async def on_caller"):src.index("# ------------------------------------------------------------ advance")]
    assert body.index("self._routed(text)") > body.index("check_advice(text)"), \
        "the fallback runs before the advice gate again"
    assert body.index("self._routed(text)") > body.index("coverage_lookup(text"), \
        "the fallback runs before the coverage answers again"
    assert body.index("self._routed(text)") > body.index("is_price_request(text)"), \
        "the fallback runs before the price handler again"


def test_a_lead_in_does_not_leave_a_capital_mid_sentence():
    """"As I mentioned, Your due date is..." reads as two sentences jammed
    together."""
    events, _ = _run(["Yes speaking", "when is it due?", "ok", "ok"])
    line = [t for t in _agent(events) if t.startswith("As I mentioned,")][0]
    assert line.startswith("As I mentioned, your due date")


def test_a_brand_name_keeps_its_capital_after_a_lead_in():
    from voicebot.call.engine import _join_case
    assert _join_case("As I mentioned,", "Etiqa will send it") == "Etiqa will send it"
    assert _join_case("As I mentioned,", "Your due date") == "your due date"
    assert _join_case("Of course.", "Your due date") == "Your due date"


def test_as_i_mentioned_is_used_once_not_on_every_turn():
    events, _ = _run(["Yes speaking", "when is it due?", "how much is the premium?",
                      "ok", "ok"])
    assert sum(t.startswith("As I mentioned,") for t in _agent(events)) == 1


# --- "sorry, who are you?" ------------------------------------------------
# Reported: asked twice in one call and answered both times with the next
# scripted line. That is how a servicing call starts sounding like a scam.

@pytest.mark.parametrize("said", [
    "h, yes, this is my home. sorry, who are you?",
    "uh, i, i asking who are you?", "who is this?", "which company are you from",
    "你是谁？",
])
def test_who_are_you_is_recognised(said):
    from voicebot.call.reactions import asks_who_we_are
    assert asks_who_we_are(said)


@pytest.mark.parametrize("said", [
    "好的你有什么事", "呃找我什么事", "what is this about",
    "why are you calling me", "what do you want",
])
def test_what_is_this_about_is_a_different_question(said):
    """"什么事" is not "who are you" — it is "what do you want", and a
    self-introduction leaves the caller none the wiser about why their phone
    rang. Asked twice, it produced the same introduction twice."""
    from voicebot.call.reactions import asks_purpose, asks_who_we_are
    assert asks_purpose(said)
    assert not asks_who_we_are(said), "the purpose is the more useful answer"


def test_the_bot_says_who_it_is_when_asked():
    events, session = _run(["Yes speaking", "sorry, who are you?"])
    said = _agent(events)[-1]
    assert session.agent_name in said and "Etiqa Insurance" in said
    assert "6887 8777" in said, "no way to call back and check"
    assert session.turn == 2, "answering the question is not finishing a turn"


def test_asking_who_we_are_before_the_identity_check_still_asks_the_question():
    """Who we are is not their data — it is answerable at any point — but the
    right-party check still has to happen."""
    events, session = _run(["who is this?"])
    said = _agent(events)[-1]
    assert "Etiqa Insurance" in said
    assert "policyholder" in said
    assert session.gates.as_dict().get("identity") != "pass"


def test_who_are_you_arrives_as_one_clip():
    events, _ = _run(["who is this?"])
    audio = [e for e in events if e.kind == "audio"]
    turns = [e for e in events if e.kind == "transcript" and e.speaker == "agent"]
    assert len(audio) == len(turns)


def test_asking_twice_does_not_repeat_the_same_paragraph():
    """The same self-introduction twice is what makes a bot a bot. The second
    time, say who we are in a sentence and get on with the call."""
    events, _ = _run(["this is my home. sorry, who are you?",
                      "uh, i, i asking who are you?"])
    said = _agent(events)
    assert "6887 8777" in said[-2], "the first ask gets the full identification"
    assert "It's still Michael" in said[-1], "the default voice is Michael"
    assert "Tiq Home Insurance renewal" in said[-1], "never said why we called"


def test_asking_what_this_is_about_gets_the_reason_and_the_question():
    """Answering with a paragraph and then waiting is how a call stalls: the
    caller gets an explanation but not the question they were being asked."""
    events, session = _run(["Yes speaking", "好的你有什么事"])
    said = _agent(events)[-1]
    assert "Tiq Home Insurance renewal" in said
    assert "servicing call" in said, "explained itself and then went quiet"
    assert session.turn == 2


def test_asking_what_this_is_about_twice_does_not_repeat_the_paragraph():
    events, _ = _run(["Yes speaking", "what is this about", "what do you want"])
    said = _agent(events)
    assert "it's due soon" in said[-2]
    assert said[-1].startswith("Just the renewal, nothing else.")


def test_who_are_you_and_what_is_this_about_are_different_answers():
    who, _ = _run(["Yes speaking", "who is this?"])
    why, _ = _run(["Yes speaking", "what is this about"])
    assert "6887 8777" in _agent(who)[-1], "the identity answer lost its number"
    assert "6887 8777" not in _agent(why)[-1], "answered the wrong question"


# --- from one recorded Mandarin call --------------------------------------
# Four separate failures in seven turns, each of which made the bot look like
# it was not listening. Kept together because they compound: the caller ends
# up correcting a promise the bot had already broken.

def test_a_question_asked_in_english_mid_mandarin_call_is_answered():
    """"uh, okay, what happen?" — the caller asking what this call is about.
    The "okay" in it was read as a bare yes and the script moved on without
    answering, in a language they had just switched out of."""
    from voicebot.call.reactions import asks_purpose, bare_answer

    assert bare_answer("uh, okay, what happen?") is None
    assert asks_purpose("uh, okay, what happen?")


def test_not_having_received_the_notice_answers_the_question_we_asked():
    """Turn 3 asks whether the renewal notice arrived. "呃没有收到" — no, I
    didn't get it — was flagged to the CRM and then handed to the model,
    which had no handler for it and told the caller it was outside what this
    call could deal with. They had answered our own question."""
    events, session = _run(["呃对", "好的", "呃没有收到"], lang="zh")
    said = " ".join(_agent(events))
    assert any(e.kind == "tool" and "flag_notice_not_received" in e.tool
               for e in events)
    assert "不在我这通续保电话能处理的范围内" not in said
    assert session.turn == 4, "answering the question did not move the call on"


@pytest.mark.parametrize("said", ["可以可以请安排", "好的好的麻烦你", "可以"])
def test_a_mandarin_yes_of_more_than_two_characters_is_still_a_yes(said):
    """Chinese writes no spaces, so "可以可以请安排" — yes, please arrange it —
    counted seven words, failed the five-word test and read as no answer at
    all."""
    from voicebot.call.reactions import yes_no
    assert yes_no(said) == "yes"


def test_an_accepted_offer_is_never_silently_dropped():
    """The worst thing in the recorded call: the bot offered a customer care
    officer, the caller said "可以可以请安排", and it moved to the premium line
    as though nothing had been asked. Two turns later the caller had to say
    "didn't you say you'd arrange for them to contact me?" — and was ignored
    again."""
    session = CallSession(personas.get("TH-4471-0093"), MockBackend(), lang="zh")
    asyncio.run(_drain(session.start()))
    session._pending = "officer"
    asyncio.run(_drain(session.on_caller("可以可以请安排")))
    assert session.handoff is not None, "the caller accepted and nothing happened"


def test_a_question_the_word_lists_cannot_read_is_held_for_the_model():
    """`_pending` used to be cleared the moment the keyword lists failed on a
    reply. Set aside instead: if the model reads it as an answer, it still
    counts."""
    session = CallSession(personas.get("TH-4471-0093"), MockBackend(), lang="zh")
    session._pending = "officer"
    asyncio.run(_drain(session.on_caller("那你帮我安排一下好了")))
    assert session._pending is None
    assert session._unanswered is None, "a held question must not outlive its turn"


async def _drain(gen):
    return [ev async for ev in gen]
