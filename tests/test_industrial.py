"""What the recorded calls said needed fixing.

Each test here carries the caller line that exposed it. Sixty-one recorded
calls, 225 caller turns; the 24 that reached the guardrail were the map.
"""
import asyncio
import inspect

import pytest

from voicebot.call import engine as E
from voicebot.call.engine import CallSession
from voicebot.data import personas
from voicebot.runtime.base import Completion
from voicebot.runtime.mock import MockBackend


class _Router(MockBackend):
    def __init__(self, reply):
        super().__init__(); self.reply = reply; self.prompts = []

    async def complete(self, system, user, lang, max_tokens=None):
        self.prompts.append(user)
        return Completion(text=self.reply, latency_ms=5)


def _session(lang="en", backend=None, guardrail=False):
    s = CallSession(personas.get("TH-4471-0093"), backend or MockBackend(),
                    lang=lang, guardrail=guardrail)
    asyncio.run(_drain(s.start()))
    return s


async def _drain(gen):
    return [ev async for ev in gen]


def _say(s, text):
    return asyncio.run(_drain(s.on_caller(text)))


def _agent(events):
    return " ".join(e.text for e in events if e.kind == "transcript" and e.speaker == "agent")


# --- "are you a robot?" ----------------------------------------------------

@pytest.mark.parametrize("lang,said,marker", [
    ("en", "are you a robot?", "automated assistant"),
    ("en", "am i talking to a machine", "automated assistant"),
    ("zh", "你是机器人吗", "自动语音助理"),
    ("zh", "我在问你是不是机器人", "自动语音助理"),
])
def test_asked_if_it_is_a_machine_the_bot_says_yes(lang, said, marker):
    """Routed to "who are you" it answered with the agent's name and employer
    — true, and a dodge. Asked twice on one recorded call."""
    s = _session(lang)
    _say(s, "yes speaking" if lang == "en" else "是的")
    out = _agent(_say(s, said))
    assert marker in out
    assert s.agent_name not in out.split(marker)[0], \
        "introduced itself instead of answering"


def test_disclosure_does_not_stall_the_call():
    """Disclosed, then the line they were on — not a dead end."""
    s = _session()
    _say(s, "yes speaking")
    out = _agent(_say(s, "is this a recording?"))
    assert "servicing call" in out, "the current line was not put again"


# --- "stop calling me" -----------------------------------------------------

@pytest.mark.parametrize("lang,said", [
    ("en", "please stop calling me"),
    ("en", "take me off your list"),
    ("zh", "可以不要打电话给我了吗"),
])
def test_a_do_not_call_request_is_recorded_not_absorbed(lang, said):
    """"可以不要打电话给我了吗" was taken as declining the cross-sell and
    answered "没问题". A request not to be called is an instruction against
    the policy, and the CRM entry is the caller's protection, not the
    sentence."""
    s = _session(lang)
    _say(s, "yes speaking" if lang == "en" else "是的")
    events = _say(s, said)
    tools = [e for e in events if e.kind == "tool"]
    assert any(t.tool == "crm.dnc_request" for t in tools), "nothing recorded"
    gates = [e for e in events if e.kind == "gate" and e.gate == "consent"]
    assert gates and gates[-1].state == "block"
    ended = [e for e in events if e.kind == "end"]
    assert ended and ended[0].outcome == "do-not-call requested"
    assert s.ended


def test_the_dnc_record_precedes_the_words():
    s = _session()
    _say(s, "yes speaking")
    events = _say(s, "don't call me again")
    first_tool = next(i for i, e in enumerate(events) if e.kind == "tool")
    first_agent = next(i for i, e in enumerate(events)
                       if e.kind == "transcript" and e.speaker == "agent")
    assert first_tool < first_agent


# --- laughter --------------------------------------------------------------

def test_laughter_is_not_off_topic():
    """"哈哈哈哈哈哈哈哈" went to the model, came back off_topic, and the bot
    offered to escalate a chuckle to customer care."""
    s = _session("zh", backend=_Router("off_topic"), guardrail=True)
    _say(s, "是的")
    out = _agent(_say(s, "哈哈哈哈哈哈哈哈"))
    assert "客服专员" not in out, "offered customer care over a laugh"
    assert s._clarifies == 0, "counted as a strike toward not-understood"
    # The Mandarin turn 2 is a statement, so the call simply carries on.
    assert s.turn == 3


def test_laughter_at_a_question_puts_the_question_again():
    """The English turn 2 ends in a question mark: laughter is not an answer
    to it, so it is asked again rather than skipped."""
    s = _session("en", backend=_Router("off_topic"), guardrail=True)
    _say(s, "yes speaking")
    out = _agent(_say(s, "hahaha"))
    assert s.turn == 2
    assert "servicing call" in out


def test_laughter_at_a_pending_question_keeps_the_question():
    s = _session()
    _say(s, "yes speaking")
    s._pending = "officer"
    _say(s, "haha")
    assert s._pending == "officer", "the offer was dropped"


# --- noise the recogniser dressed as a sentence -----------------------------

def test_wrong_script_that_the_model_cannot_place_is_not_a_turn():
    """"阿基米德的浮力原理" on an English call, from a caller who had said
    nothing. Passes the speech-rate gate — it is a normal rate — and the model
    calls it off_topic. That combination is the recogniser talking."""
    s = _session("en", backend=_Router("off_topic"), guardrail=True)
    _say(s, "yes speaking")
    turn = s.turn
    events = _say(s, "阿基米德的浮力原理")
    out = _agent(events)
    assert "customer care" not in out, "offered escalation to noise"
    assert s.turn == turn
    assert s._pending is None
    notes = [e.text for e in events if e.kind == "system"]
    assert any("noise" in n for n in notes)


def test_noise_is_not_evidence_for_a_language_switch():
    """Two of these in a row flipped a live English call into Mandarin."""
    s = _session("en", backend=_Router("off_topic"), guardrail=True)
    _say(s, "yes speaking")
    _say(s, "阿基米德的浮力原理")
    assert s._other_lang_turns == 0, "noise counted toward switching language"
    _say(s, "其主要城市有圣保罗和里约热内卢。")
    assert s.lang == "en", "two noise turns switched the call's language"


# --- the wait ---------------------------------------------------------------

def test_the_model_is_started_before_the_filler_is_spoken():
    """The filler covers the wait; it must not add to it."""
    src = inspect.getsource(E.CallSession._routed)
    assert src.index("asyncio.ensure_future") < src.index("THINKING[self.lang]")
    assert src.index("THINKING[self.lang]") < src.index("got = await pending")


def test_the_filler_is_pre_rendered():
    from voicebot.call.engine import prerenderable_lines
    lines = {t for t, _ in prerenderable_lines()}
    for lang in ("en", "zh"):
        assert E.THINKING[lang] in lines
        assert E.BOT_DISCLOSURE[lang] in lines
        assert E.DNC_ACK[lang] in lines
        assert E.COVERAGE_UNKNOWN[lang] in lines


# --- coverage we cannot ground ----------------------------------------------

def test_an_unknown_coverage_question_gets_the_right_sentence():
    """"My roof is leaking, does that count" → "that's not something I can
    help with on a renewal call". It is exactly the kind of thing a renewal
    call should help with; we just will not guess."""
    s = _session("en", backend=_Router("coverage"), guardrail=True)
    _say(s, "yes speaking")
    out = _agent(_say(s, "my roof is leaking, does that count"))
    assert "not something I can help with" not in out
    assert "confirm exactly what's covered" in out
    assert s._pending == "officer"


# --- how calls end -----------------------------------------------------------

def test_every_ending_is_a_known_disposition():
    """A free-text outcome is one typo away from a report that undercounts."""
    import re
    src = inspect.getsource(E)
    for m in re.finditer(r'self\._end\(f?"([^"]+)"', src):
        outcome = m.group(1).split(" · ")[0].split("{")[0].strip()
        assert any(d.startswith(outcome) or outcome.startswith(d)
                   for d in E.DISPOSITIONS), outcome


def test_an_unknown_disposition_is_refused():
    s = _session()
    with pytest.raises(AssertionError):
        asyncio.run(_drain(s._end("something else")))


# --- a yes with words after it is still a yes --------------------------------

@pytest.mark.parametrize("said", ["Yes, I received it.", "Yes, that's the right email.",
                                  "Sure, thank you.", "yes, you are right."])
def test_an_answer_with_words_after_it_never_reaches_the_model(said):
    """All four came off the recording having gone to the guardrail — one to
    two seconds each, and on the live eval a timeout — for a yes."""
    backend = _Router("affirm")
    s = _session("en", backend=backend, guardrail=True)
    _say(s, "yes speaking"); _say(s, "yes")
    before = s.turn
    _say(s, said)
    assert backend.prompts == [], "a plain yes went to the model"
    assert s.turn == before + 1


@pytest.mark.parametrize("said", ["aiyah so expensive lah", "yes but what is the premium",
                                  "Maybe, I'll take a look."])
def test_the_looser_reading_is_still_guarded(said):
    """The reason the strict reading existed. These are not answers to the
    question on the line and must still go to the model."""
    backend = _Router("price")
    s = _session("en", backend=backend, guardrail=True)
    _say(s, "yes speaking"); _say(s, "yes")
    before = s.turn
    _say(s, said)
    # Either a keyword handler took it ("expensive" is a price complaint and
    # is answered as one) or the model was asked. What must not happen is the
    # script moving on as though the caller had said yes.
    assert s.turn == before, "treated as a yes"


def test_a_do_not_call_request_outranks_a_pending_question():
    """While the cross-sell offer was pending, "可以不要打电话给我了吗" was consumed
    as a polite decline of it — "好的，没问题" — and nothing was recorded."""
    s = _session("zh")
    _say(s, "是的")
    s._pending = "cross_sell"
    events = _say(s, "可以不要打电话给我了吗")
    assert any(e.kind == "tool" and e.tool == "crm.dnc_request" for e in events)
    assert "没问题" not in _agent(events)
    assert s.ended


def test_not_receiving_the_notice_is_heard_after_turn_three_too():
    """"呃没有收到" arriving one turn late went to the model and came back as a
    request for customer care. The words say what it is."""
    s = _session("zh")
    for r in ("是的", "好的", "好的"):
        _say(s, r)
    assert s.turn == 4
    events = _say(s, "呃没有收到")
    assert any(e.kind == "tool" and "notice_not_received" in e.tool for e in events)
    out = _agent(events)
    assert "客服专员" not in out
    assert "还没收到" in out


@pytest.mark.parametrize("turn_replies", [(), ("好的",), ("好的", "好的")])
def test_not_receiving_the_notice_is_heard_from_turn_two(turn_replies):
    """On the live eval it arrived at turn 2, because the purpose questions
    ahead of it no longer advance the script. There is nothing else on a
    renewal call to not have received."""
    s = _session("zh")
    _say(s, "是的")
    for r in turn_replies:
        _say(s, r)
    events = _say(s, "呃没有收到")
    assert any(e.kind == "tool" and "notice_not_received" in e.tool for e in events)
    assert "客服专员" not in _agent(events)
    assert s._notice_missing
