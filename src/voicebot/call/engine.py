"""Call state machine for the Tiq Home renewal script.

Drives the seven scripted turns, enforces the compliance gates between them,
and handles the three things that take a call off-script: a coverage question,
an advice request, and a language switch.

The engine emits events; it never touches audio or models directly. That keeps
it identical across the MLX and CUDA backends.
"""
from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

from ..compliance.gates import (CallState, Gates, check_advice, check_dnc,
                                check_identity, may_cross_sell)
from ..knowledge.policy import Serving, default_serving
from ..data.facts import (RENEWAL_PROCESS, coverage_lookup, is_advice_request,
                          is_price_request, is_procedure_request, policy_answer,
                          policy_topic, price_answer,
                          wants_record_change,
                          wants_email_change)
from ..data.personas import Policy
from ..lang import asks_for as lang_request
from ..lang import detect as detect_lang
from ..lang import is_singlish
from . import handoff as ho
from . import dictation, router
from .reactions import (BRIDGES, SINGLISH_BRIDGES, asks_for_human, asks_purpose,
                        asks_dnc, asks_if_bot, asks_who_we_are, defers_to_us,
                        is_nonverbal, not_received,
                        bare_answer, bridge_kind, denies, is_greeting,
                        is_uninterpretable, script_mismatch, sounds_frustrated,
                        still_dictating, wants_callback, wants_repeat, wants_slower,
                        yes_no)
from .reactions import all_lines as reaction_lines
from ..events import (AgentAudio, CallEnded, Event, GateChange, HandoffRequested,
                      Status, SystemNote, ToolCall, Transcript, TurnChange)
from .. import pcm
from ..spoken import speech_chunks, speech_seconds
from ..runtime.base import Backend
from . import script

ESCALATION = {
    "en": ("That's really a question for one of our licensed advisers — I'd rather "
           "they walked you through it properly. May I arrange for someone to call "
           "you back?"),
    "zh": "这个问题比较适合由我们持牌的顾问为您解答。我可以安排同事回电给您吗？",
}

WRONG_PARTY_REPLY = {
    "en": ("I understand, thank you. I'm not able to discuss account details with "
           "anyone other than the policyholder. Could I ask you to let them know "
           "Etiqa called regarding a policy matter, and that they can reach us on "
           "6887 8777 at their convenience?"),
    "zh": "我明白了，谢谢。我不能与保单持有人以外的人讨论账户详情。可以麻烦您转告他，请他方便时致电 6887 8777。",
}


#: "Why is that?" asked of the cross-sell offer. The caller is asking about
#: the thing we just raised, so it gets an answer and the question again —
#: telling them it is outside what this call can handle, when this call
#: brought it up, is the bot arguing with itself.
CROSS_SELL_WHAT = {
    "en": ("It's about a personal accident policy — twenty seconds, and I'll "
           "skip it if you'd rather. Shall I go on?"),
    "zh": "是关于个人意外险的，二十秒就好，您不想听我就跳过。要我说吗？",
}


LANG_BRIDGE = {
    "zh": "当然可以，我们用华语继续。",
    "en": "Of course, I'll carry on in English.",
}

# Improvised lines only. The seven scripted turns are the client's approved
# wording and are never re-registered — accommodation happens in what the bot
# makes up, not in what compliance signed off.
SINGLISH_VARIANTS = {
    "Sorry, I didn't catch that. Could you say the address again, slowly?":
        "Sorry ah, I didn't catch that one. Can say the address again, slowly?",
    "Sorry, just to confirm — am I speaking with the policyholder?":
        "Sorry ah, just to confirm — I'm speaking with the policyholder itself?",
}

REPEAT_LEAD = {
    "en": ("Of course.", "Sorry about that — let me go through it again."),
    "zh": ("好的，我再说一次。", "不好意思，我再说一遍。"),
}

# Three goes at the same sentence is not a hearing problem we can fix by
# saying it a fourth time.
REPEAT_HANDOFF = {
    "en": ("I don't think the line is doing us any favours. Let me have a "
           "colleague call you back on a better connection — sorry about that."),
    "zh": "这条线路好像不太清楚。我安排同事换个时间再打给您，不好意思。",
}

# Each further request takes another step down, floored so the line never
# turns into a drawl.
SLOWER_STEPS = (0.85, 0.72)

SLOWER_ACK = {
    "en": "Of course, I'll slow down.",
    "zh": "好的，我说慢一点。",
}

SLOWEST_ACK = {
    "en": "Sorry — slower still.",
    "zh": "不好意思，我再说慢一点。",
}

# Someone who picks up and says "hello? testing?" is checking the line works.
# Answering that with "sorry, I didn't catch that" — twice — fails the easiest
# turn in the call.
# The first question anyone asks an unexpected caller, and the bot used to
# answer it with the next line of the script. Twice in one call, in the
# report that prompted this — which is exactly how a servicing call starts
# sounding like a scam call.
# Turn 6 is asked for, not delivered. A twenty-second pitch dropped on
# someone who has just been told we could not help them is the single most
# resented thing an outbound call does.
CROSS_SELL_ASK = {
    "en": "Before I let you go — may I take twenty seconds to mention one "
          "thing that could save you money? Happy to skip it if you'd rather.",
    "zh": "在结束之前，我可以用二十秒跟您说一件可能帮您省钱的事吗？您不想听也没关系。",
}

CROSS_SELL_DECLINED = {
    "en": "Of course, no problem at all.",
    "zh": "好的，没问题。",
}

# Answering "you didn't hear me" with "sorry, I didn't quite catch that" is
# the machine confirming the complaint.
APOLOGY = {
    "en": "You're right, and I'm sorry — that's my fault, not yours.",
    "zh": "您说得对，不好意思，是我的问题。",
}

def who_we_are(lang: str, agent: str) -> str:
    """Who is calling. Carries the agent's name, so it varies with the voice
    and has to be rendered per voice like the opening turn does."""
    if lang == "zh":
        return (f"当然可以。我叫 {agent}，是 Etiqa 保险的，关于您的 Tiq Home 保险保单。"
                "如果您想回电确认，我们的号码是 6887 8777。")
    return (f"Of course — my name is {agent} and I'm calling from Etiqa Insurance, "
            "about your Tiq Home Insurance policy. You can reach us on 6887 8777 "
            "if you'd rather call back.")

# What the call is about, as distinct from who is making it. Followed
# immediately by the line we were on, so the caller gets the question they
# were being asked rather than a paragraph and a pause.
PURPOSE = {
    "en": "I'm calling about your Tiq Home Insurance renewal — it's due soon "
          "and I wanted to confirm a few details with you.",
    "zh": "我这通电话是关于您的 Tiq Home 保险续保，快到期了，想跟您确认几项资料。",
}

# Asked a second time. Repeating the paragraph verbatim is the thing that
# makes a caller certain they are talking to a machine.
PURPOSE_AGAIN = {
    "en": "Just the renewal, nothing else.",
    "zh": "就是续保的事，没别的。",
}

def who_we_are_again(lang: str, agent: str) -> str:
    if lang == "zh":
        return f"还是我，Etiqa 保险的 {agent}。"
    return f"It's still {agent}, from Etiqa Insurance."

ADVISER_BOOKED = {
    "en": "Thank you — I'll have one of our advisers call you back.",
    "zh": "好的，我会安排我们的顾问回电给您。",
}

ADVISER_DECLINED = {
    "en": "No problem at all.",
    "zh": "没问题。",
}

# Said in front of a scripted turn whose subject the caller already asked
# about. Without it the bot reads out the due date it quoted two turns ago as
# though the conversation had not happened.
# Turn 3 asks whether the renewal notice arrived. "No" is an answer with a
# consequence — the customer has not seen the document the rest of the call
# refers to — and gliding past it with "no problem" leaves them none the wiser.
NOTICE_MISSING = {
    "en": "Ah — then let me make sure it reaches you.",
    "zh": "原来还没收到，那我确认一下重新发给您。",
}

ALREADY_SAID = {
    "en": "As I mentioned,",
    "zh": "刚才提过，",
}

GREETING_REPLY = {
    "en": "Yes, hello — can you hear me all right?",
    "zh": "喂，您好，请问听得清楚吗？",
}

# Asked when the caller has raised something this call cannot help with. The
# alternative — "sorry, I didn't quite catch that", over and over — pretends
# the problem is the line rather than the scope.
#: "Are you a robot?" On an outbound insurance call the only acceptable
#: answer is yes. Routed to "who are you" it introduced itself instead —
#: a true sentence answering a different question, asked twice on one call.
#: Disclosed, then the line they were on, so the call does not stall on it.
BOT_DISCLOSURE = {
    "en": ("Yes — I'm an automated assistant, calling on behalf of Etiqa "
           "Insurance. If you'd rather speak to a person, just say so and I'll "
           "arrange it."),
    "zh": "是的，我是 Etiqa 保险的自动语音助理。如果您想跟真人通话，跟我说一声，我马上安排。",
}

#: A request not to be called again is a do-not-call instruction. It is
#: recorded against the policy and the call ends; it is not a polite decline
#: of whatever was just offered, which is what "可以不要打电话给我了吗" became.
DNC_ACK = {
    "en": ("Understood. I've recorded that you don't want to be called, and we "
           "won't call this number again. Sorry for the disturbance, and goodbye."),
    "zh": "明白了。我已经记录下来，我们不会再打这个号码。抱歉打扰了，再见。",
}

#: A coverage question we have no grounded answer to. "That's not something I
#: can help with on a renewal call" is the wrong sentence — it is exactly the
#: kind of thing a renewal call should help with; we just will not guess.
COVERAGE_UNKNOWN = {
    "en": ("Good question — I'd rather a colleague confirm exactly what's "
           "covered there than guess. Shall I arrange for one to call you?"),
    "zh": "这个问题问得好。具体保障范围我不想猜，让同事跟您确认比较准确。要我安排他们联络您吗？",
}

#: Said while the model is deciding. The guardrail costs one to two seconds,
#: and on a phone line that is dead air — the length of silence after which
#: people say "hello?". Played from the cache while the model runs, so it
#: covers the wait rather than adding to it.
THINKING = {
    # Neutral on purpose. "Let me just check that" was followed, when the
    # model timed out, by "sorry, I didn't quite catch that" — a promise and
    # then its contradiction. This one is true whatever comes next.
    "en": "One moment.",
    "zh": "请稍等。",
}

#: Every way a call can end. A closed set, so the calls list and anything
#: downstream can count on the values — a free-text outcome is one typo away
#: from a report that undercounts.
DISPOSITIONS = ("renewal acknowledged", "callback requested",
                "do-not-call requested", "no personal data disclosed, callback logged",
                "handed to a colleague")

OFFICER_OFFER = {
    "en": "That's not something I can help with on a renewal call, but our "
          "customer care officer can. Would you like me to arrange for one to "
          "call you?",
    "zh": "这个不在我这通续保电话能处理的范围内，不过我们的客服专员可以帮您。"
          "需要我安排他们联络您吗？",
}

CLARIFY = {
    "en": "Sorry, I didn't quite catch that. Could you say it again?",
    "zh": "不好意思，我没听清楚。可以请您再说一次吗？",
}

CALLBACK_REPLY = {
    "en": ("Of course — I won't hold you up. I'll note that down and we'll try "
           "you another time. Thank you, and have a good day."),
    "zh": "没问题，我不打扰您了。我会记下来，我们改天再联络您。谢谢，祝您愉快。",
}

MALAY_ESCALATION = (
    "I'm sorry — I can understand you, but I'm not able to hold this conversation "
    "in Malay yet. May I arrange for a Malay-speaking colleague to call you back?"
)


#: Words safe to lowercase after a lead-in that ends in a comma. Anything not
#: on the list keeps its capital — "Etiqa" and "Tiq" open scripted lines, and
#: lowercasing a brand name is worse than an odd capital.
_LOWERABLE = frozenset((
    "the", "your", "this", "that", "it", "it's", "we", "we'll", "i", "i'll",
    "please", "before", "there", "there's", "a", "an", "our", "my", "you",
    "just", "so", "okay",
))


def _join_case(lead: str, text: str) -> str:
    """"As I mentioned, Your due date..." reads as two sentences jammed
    together. Only the transcript is affected — the audio is two clips — but
    the transcript is the record of the call."""
    if not lead.rstrip().endswith(",") or not text:
        return text
    first = text.split(" ", 1)[0].strip(",.;:").lower()
    return text[0].lower() + text[1:] if first in _LOWERABLE else text


def prerenderable_lines(agent_name: str = script.DEFAULT_AGENT) -> list[tuple[str, str]]:
    """(text, lang) for every fixed line the engine speaks with prerendered=True.

    Acknowledgements and repeat lead-ins are short, so synthesising one live
    costs little — but it costs it in the middle of a turn, in front of the
    line it introduces. `make prerender` warms them instead.
    """
    out: list[tuple[str, str]] = list(reaction_lines())
    for lang, leads in REPEAT_LEAD.items():
        out.extend((lead, lang) for lead in leads)
    for table in (ESCALATION, WRONG_PARTY_REPLY,
                  LANG_BRIDGE, REPEAT_HANDOFF, CALLBACK_REPLY,
                  SLOWER_ACK, SLOWEST_ACK, CLARIFY, GREETING_REPLY,
                  ADVISER_BOOKED, ADVISER_DECLINED, ALREADY_SAID,
                  RENEWAL_PROCESS, PURPOSE,
                  PURPOSE_AGAIN,
                  CROSS_SELL_ASK, CROSS_SELL_WHAT, BOT_DISCLOSURE, DNC_ACK,
                  COVERAGE_UNKNOWN, THINKING,
                  CROSS_SELL_DECLINED, APOLOGY, NOTICE_MISSING,
                  OFFICER_OFFER):
        out.extend((text, lang) for lang, text in table.items())
    out.extend(ho.all_lines())
    out.append((MALAY_ESCALATION, "en"))
    # Carry the agent's name, so they belong to one voice rather than to the
    # script. Warming them under the wrong name is a cache miss mid-turn, in
    # front of the question "who is this?" -- the worst possible place for a
    # two-second pause.
    for lang in ("en", "zh"):
        out.append((who_we_are(lang, agent_name), lang))
        out.append((who_we_are_again(lang, agent_name), lang))
    # Both registers of every improvised line. Warming only the Singlish
    # rewordings left the standard forms to render live: the identity re-ask
    # cost 7.6 s mid-call the first time it was needed.
    for standard, singlish in SINGLISH_VARIANTS.items():
        out.append((standard, "en"))
        out.append((singlish, "en"))
    return out


def _looks_tamil(text: str) -> bool:
    """Tamil is one of Singapore's four official languages and the recogniser
    handles it. We cannot speak it, so the only honest answer is a colleague
    who can — the same position we are in with Malay.

    Requires more than one character so a single stray glyph out of the
    recogniser does not end an English call in a transfer. Two was still not
    enough: on a recorded call a caller who had spoken English throughout
    produced "ஆன் நோம்" — two short words, almost certainly a mis-recognition —
    and the call ended in a transfer they never asked for. Handing off is not
    reversible, so it takes the same kind of evidence barge-in does: enough of
    the utterance to be a sentence rather than a fragment.
    """
    tamil = sum(1 for ch in text if "\u0b80" <= ch <= "\u0bff")
    return tamil >= 6


def _looks_malay(text: str) -> bool:
    """Malay speech output is on hold, but MERaLiON still hears Malay. Recognise
    it and escalate rather than blundering on in English."""
    markers = (" saya ", " tidak ", " boleh ", " nak ", " macam mana ", " terima kasih",
               "selamat pagi", "selamat petang", " ada ", " belum ")
    low = f" {text.lower()} "
    return sum(m in low for m in markers) >= 2


class CallSession:
    """One call. Not reusable — construct a new session per call."""

    def __init__(self, policy: Policy, backend: Backend, lang: str | None = None,
                 part_of_day: str = "afternoon", register: str = "standard",
                 voice: str | None = None, guardrail: bool = True,
                 guardrail_timeout_ms: int = 1500,
                 knowledge: "Serving | None" = None) -> None:
        # Start of the current turn, used to report time-to-first-audio the way
        # the console labels it: from the caller finishing to the agent
        # speaking, not just the cost of synthesis.
        self._turn_started: float | None = None
        self.p = policy
        self.backend = backend
        self.lang = lang or policy.language
        self.part_of_day = part_of_day
        self.gates = Gates()
        self.turn = 0
        self.ended = False
        self._awaiting_identity = False
        # Chosen up front, or softened mid-call if the caller is clearly
        # speaking Singlish. Once set to singlish it stays there — flipping
        # register mid-call is as jarring as flipping language.
        self.register = register
        # Which pre-rendered voice speaks the scripted turns. Fixed for the
        # call: swapping speaker mid-conversation is as jarring as swapping
        # language.
        self.voice = voice
        # The name the bot gives, matched to the voice speaking. Fixed for the
        # call, like the voice itself.
        self.agent_name = script.agent_name_for(voice)
        # How this deployment may use the knowledge bundle. The demo speaks
        # unsourced placeholder wording; anything heading for a real customer
        # refuses it and offers a colleague instead. Resolved once per call so
        # a config reload cannot change the rules halfway through one.
        self.knowledge = knowledge if knowledge is not None else default_serving()
        # Consecutive turns heard in another language. One is not evidence.
        self._other_lang_turns = 0
        # Email correction sub-dialogue: None | "listening" | "confirming"
        #: Whether the read-back has already been put a second time.
        self._confirm_reasked = False
        #: Consent to hear the cross-sell, given explicitly. Nothing else
        #: unlocks turn 6's wording — including a request to repeat.
        self._cross_sell_ok = False
        self._cross_sell_asked_twice = False
        self._lang_turns_before = 0
        # What the caller last said, and what we have already said back. Both
        # feed the acknowledgement that opens the next line.
        self._last_caller: str | None = None
        self._bridges_used: set[str] = set()
        self._bridged_last = False
        self._repeats = 0
        self._clarifies = 0
        self._slower_steps = 0
        # A yes/no question we asked and are waiting on: "may I arrange for
        # someone to call you back?" used to be asked and then ignored.
        self._pending: str | None = None
        #: A question we asked whose answer this turn did not obviously carry.
        #: Held for exactly one turn, for the guardrail to settle.
        self._unanswered: str | None = None
        # Facts the caller asked for before the script reached them.
        self._answered: set[str] = set()
        #: Consecutive replies that looked like a language we cannot speak.
        #: Reset by anything we could read, so one artefact does not count.
        self._foreign = 0
        self._said_already = False
        # Set if any line had to fall back to the live voice — a different
        # speaker from the rest of the call.
        self._fell_back = False
        # Measured synthesis rate, seconds of work per second of audio.
        # None until this call has actually synthesised something.
        self._rtf: float | None = None
        #: Confirmations the caller has already given, by the `ask` name on
        #: the turn that would otherwise seek them. A turn whose question is
        #: in here is spoken without it. Nothing is skipped — the disclosure
        #: still happens — but the call stops asking what it has been told.
        self._answered: set[str] = set()
        #: Set once the call is being given to a person. Blocks the rest of
        #: the script, and the cross-sell above all.
        self.handoff: ho.Handoff | None = None
        self._frustrations = 0
        self._declined_cross_sell = False
        self._advice_raised = False
        #: Everything we heard while trying to take an address down, so a
        #: colleague picking the call up has the caller's own words.
        self._notice_missing = False
        self._identified = 0
        self._purposes = 0
        # The model runs in one place: after every keyword handler has
        # declined. Off by configuration, the call falls back to asking the
        # caller to repeat themselves, exactly as before.
        self.guardrail = guardrail
        self.guardrail_timeout_ms = guardrail_timeout_ms
        # Playback rate. 1.0 until the caller asks us to slow down, then it
        # stays down for the rest of the call — as it would with a person.
        self.rate = 1.0

    # ------------------------------------------------------------- helpers

    def _say(self, turn: int, latency_ms: int | None = None) -> Transcript:
        return Transcript(
            speaker="agent",
            text=script.render(turn, self.p, self.lang, self.part_of_day,
                               agent_name=self.agent_name, register=self.register,
                             answered=frozenset(self._answered)),
            lang=self.lang.upper(),
            source=script.source_label(turn),
            latency_ms=latency_ms,
        )

    def _elapsed_ms(self, extra: int) -> int:
        """Voice-to-voice: everything since the caller stopped, plus the audio
        we just produced. In the typed path there is no ASR or endpointing to
        include, so the figure is correspondingly smaller — that is the mode
        being honest, not the metric being wrong."""
        if self._turn_started is None:
            return extra
        return int((time.perf_counter() - self._turn_started) * 1000)

    def _accommodate(self, text: str) -> str:
        if self.register == "singlish" and self.lang == "en":
            return SINGLISH_VARIANTS.get(text, SINGLISH_BRIDGES.get(text, text))
        return text

    async def _voice(self, *parts: str | None) -> tuple[str, bytes, int, int]:
        """One clip for what is really one utterance.

        Each part is cached separately — that is the whole point, since most
        of them repeat every call — but they reach the caller as a single
        piece of audio. Two AgentAudio events would be two loads of the
        player, and the second cuts the first off mid-word: an acknowledgement
        that talks over the line it introduces, a handoff explanation cut off
        by the question that follows it.
        """
        said = [self._accommodate(x) for x in parts if x]
        if not said:
            return "", b"", 16000, 0
        clips, rate, ms = [], None, 0
        for one in said:
            sp = await self.backend.speak(one, self.lang, prerendered=True,
                                          voice=self.voice)
            self._note_voice(sp)
            if rate is None:
                rate = sp.sample_rate
            elif sp.sample_rate != rate:
                continue                     # never mix rates; drop the odd one
            clips.append(sp.pcm)
            ms += sp.latency_ms
        if not clips:
            return said[-1], b"", 16000, ms

        # Every clip carries TTS padding — up to half a second on the tail.
        # Left in, the joins sound like the line dropped between one half of a
        # sentence and the next.
        joined = bytearray()
        for i, clip in enumerate(clips):
            joined += pcm.trim(clip, head=(i > 0), tail=(i < len(clips) - 1),
                               sample_rate=rate)
            if i < len(clips) - 1:
                joined += pcm.silence(120, rate)

        text = said[0]
        for nxt in said[1:]:
            text = f"{text} {_join_case(text, nxt)}"
        return text, self._paced(bytes(joined), rate), rate, ms

    def _note_voice(self, sp) -> None:
        """Remember when a line came out of a different mouth.

        The pre-render model and the live model are not the same speaker. A
        call that uses both changes voice mid-conversation, which is the one
        thing a caller notices before anything else we do here. It is only
        supposed to happen when the pre-render model is unavailable, so when
        it does the operator is told rather than left to hear it.
        """
        src = getattr(sp, "voice_source", "cache")
        if src == "live":
            self._fell_back = True

    def _paced(self, buf: bytes, sample_rate: int) -> bytes:
        """Apply the caller's requested pace.

        Stretched rather than resampled: resampling would drop the pitch and
        turn the agent into a different, slower person, which is not what
        "can you speak slower" asks for.
        """
        return pcm.stretch_cached(buf, self.rate, sample_rate)

    def _bridge(self) -> str | None:
        """A short acknowledgement, when the caller's reply earned one.

        Rationed: acknowledging every single turn is its own kind of robotic,
        and hearing the same three words twice in one call is worse than
        hearing none.
        """
        kind = bridge_kind(self._last_caller or "")
        if kind is None or self._bridged_last or kind in self._bridges_used:
            self._bridged_last = False
            return None
        line = BRIDGES[kind].get(self.lang)
        if not line:
            return None
        self._bridges_used.add(kind)
        self._bridged_last = True
        return line

    def _utterance_text(self, parts: list[str]) -> str:
        """How the parts read as one line, without synthesising anything."""
        if not parts:
            return ""
        text = parts[0]
        for nxt in parts[1:]:
            text = f"{text} {_join_case(text, nxt)}"
        return text

    async def _voice_stream(self, *parts: str | None
                            ) -> AsyncIterator[tuple[bytes, int, int, bool, bool]]:
        """One utterance as (pcm, rate, latency_ms, first, final) pieces.

        `_voice` waits for every part before the caller hears anything, which
        is the right trade when all of them are cache hits and wrong when any
        of them is not: synthesis returns nothing until the whole line is
        done, so a six-second line is four seconds of silence.

        Cut into chunks the first is heard in about a second, and because
        generation runs at roughly 0.8x real time the rest are made while the
        earlier ones play. That is what lets an unforeseen line — a name that
        was never pre-rendered, an answer assembled at call time — be spoken
        at conversational speed without a template behind it.

        The whole plan is known before the first chunk is synthesised, so each
        piece can be told whether it closes the utterance without holding it
        back to look at the next one.
        """
        said = [self._accommodate(x) for x in parts if x]
        # Chunk only when synthesis has been measured fast enough to sustain
        # it. Above 1x the arithmetic is against us: total synthesis exceeds
        # total audio, so playback catches up and the line gaps in the middle,
        # which is worse than the slower start chunking was meant to fix.
        plan = self._stream_plan(said)
        if not plan:
            return
        # Queued up front rather than one at a time. The backend synthesises on
        # a single worker so they run in order either way, but submitting now
        # means chunk i+1 starts the instant i's synthesis ends, instead of
        # waiting for i to be trimmed, paced and pushed out over the socket.
        jobs = [asyncio.ensure_future(
            self.backend.speak(chunk, self.lang, prerendered=True, voice=self.voice))
            for chunk in plan]
        rate: int | None = None
        try:
            for i, job in enumerate(jobs):
                sp = await job
                if rate is None:
                    rate = sp.sample_rate
                yield self._chunk_out(sp, i, len(plan), rate)
        finally:
            # Barge-in abandons this generator mid-line; nothing should keep
            # synthesising a sentence the caller has already talked over.
            for job in jobs:
                if not job.done():
                    job.cancel()

    #: What a second of audio is assumed to cost before this call has measured
    #: it. Pessimistic on purpose: an unmeasured machine splits a line only
    #: when the pieces ahead of the synthesised one are already on disk, and so
    #: cost nothing to play.
    STREAM_RTF_ASSUMED = 1.5

    def _cached(self, chunk: str) -> bool:
        probe = getattr(self.backend, "cached", None)
        if probe is None:
            return False
        try:
            return bool(probe(chunk, self.lang, self.voice))
        except Exception:                   # pragma: no cover - never fatal
            return False

    def _stream_plan(self, said: list[str]) -> list[str]:
        """How to cut this utterance up — or `said` unchanged, to send it whole.

        Splitting pays only while each piece is finished before the audio ahead
        of it stops playing. A piece already on disk costs nothing and hands
        its whole duration to the pieces after it; a piece that has to be made
        spends `rtf` seconds of that budget for every second it adds. If any
        piece would run the budget out, the line drops in the middle, and sent
        whole it merely starts late — so whole is the better of the two.

        The first piece is exempt. Waiting for it is the head latency this is
        trying to shorten, not a hole in the middle of a sentence.

        This is what makes a line worth rewording. "Good afternoon. This is
        Michael calling from Etiqa Insurance." is the same for every customer
        and can be warmed; put the name last and those four cached seconds pay
        for synthesising the name, on a machine far too slow to stream a line
        made entirely from scratch.
        """
        chunks = [c for part in said for c in speech_chunks(part, self.lang)]
        if len(chunks) <= len(said):
            return list(said)
        rtf = self._rtf if self._rtf is not None else self.STREAM_RTF_ASSUMED
        budget = 0.0
        for i, chunk in enumerate(chunks):
            seconds = speech_seconds(chunk, self.lang)
            cost = 0.0 if self._cached(chunk) else rtf * seconds
            if i:
                if cost > budget:
                    return list(said)
                budget -= cost
            budget += seconds
        return chunks

    def _observe_rtf(self, sp, rate: int) -> None:
        """Learn the synthesis rate from a line we actually synthesised.

        Cache hits are excluded deliberately: they return in a millisecond and
        would report a rate no synthesiser can meet, turning chunking on for
        the one line that then has to be made from scratch.
        """
        if getattr(sp, "voice_source", "cache") == "cache":
            return
        seconds = len(sp.pcm) / 2 / max(1, rate)
        if seconds <= 0.2:                 # too short to measure anything by
            return
        seen = (sp.latency_ms / 1000) / seconds
        # Weighted to the recent past: a box that has just become busy should
        # stop splitting lines before it has gapped several of them.
        self._rtf = seen if self._rtf is None else (0.5 * self._rtf + 0.5 * seen)

    def _chunk_out(self, sp, i: int, total: int, rate: int
                   ) -> tuple[bytes, int, int, bool, bool]:
        """One synthesised chunk, ready to send."""
        self._note_voice(sp)
        self._observe_rtf(sp, rate)
        # Never mix rates inside one utterance. Substituting silence rather
        # than skipping keeps the closing piece the closing piece.
        buf = sp.pcm if sp.sample_rate == rate else b""
        # Same trimming as the joined path: TTS padding left in at a seam
        # sounds like the line dropped between one half and the next.
        buf = pcm.trim(buf, head=(i > 0), tail=(i < total - 1), sample_rate=rate)
        if i:
            buf = pcm.silence(120, rate) + buf
        return (self._paced(buf, rate), rate, sp.latency_ms,
                i == 0, i == total - 1)

    async def _generated(self, *parts: str | None) -> AsyncIterator[Event]:
        """An unscripted line, in the same voice as every other line.

        Everything the agent says goes through the pre-render model, including
        text that is different on every call — a dictated email read back
        costs a render, which is a second or two once. The live model is a
        different speaker, and a call that changes voice halfway through is
        worse than a call that pauses.

        Streamed, because this is the path that carries the lines no cache can
        hold. The latency reported is time to the *first* audio, which is what
        the caller actually waits through.
        """
        said = [self._accommodate(x) for x in parts if x]
        full = self._utterance_text(said)
        ms = 0
        async for buf, sr, chunk_ms, first, final in self._voice_stream(*parts):
            ms += chunk_ms
            if first:
                yield Transcript(speaker="agent", text=full,
                                 lang=self.lang.upper(), source="pre-rendered",
                                 latency_ms=self._elapsed_ms(ms))
            yield AgentAudio(pcm=buf, sample_rate=sr, start=first, final=final)

    # --------------------------------------------------------------- start

    async def start(self) -> AsyncIterator[Event]:
        yield Status(text=f"Connected — {self.p.name}")
        self.turn = 1
        self._awaiting_identity = True
        yield TurnChange(turn=1, state="active")
        # The opening line needs audio like every other turn — without this the
        # bot answers the phone in silence.
        text = script.render(1, self.p, self.lang, self.part_of_day,
                             agent_name=self.agent_name, register=self.register,
                             answered=frozenset(self._answered))
        # Streamed: this line carries the customer's name, so it is the one
        # scripted turn that cannot be fully warmed for an unknown caller.
        ms = 0
        async for buf, sr, chunk_ms, first, final in self._voice_stream(text):
            ms += chunk_ms
            if first:
                yield self._say(1, latency_ms=ms)
            yield AgentAudio(pcm=buf, sample_rate=sr, start=first, final=final)

    async def unheard(self) -> AsyncIterator[Event]:
        """The caller said something and the gate could not use it.

        Silence is the one reply that makes a caller repeat themselves louder
        and longer, which is exactly what a gate tuned slightly too tight
        produces: a recorded session dropped five answers in a row — 0.12 to
        0.16 s of voiced audio each, every one of them a "yes" — and said
        nothing while the caller worked out that talking for longer was what
        got through.

        Counted as a comprehension failure like any other, because it is one,
        and because two of them should already be stopping the cross-sell.
        """
        self._clarifies += 1
        async for ev in self._generated(CLARIFY[self.lang]):
            yield ev

    # ------------------------------------------------------- caller speaks

    async def on_caller(self, text: str, lang: str | None = None,
                        started_at: float | None = None) -> AsyncIterator[Event]:
        """`started_at` is a perf_counter() taken when the caller's audio
        arrived, so the reported latency covers transcription too."""
        if self.ended:
            return
        self._turn_started = started_at if started_at is not None else time.perf_counter()

        heard = (lang or detect_lang(text, default=self.lang)).lower()
        self._last_caller = text
        # A held question is valid for the turn it was held in and no longer,
        # whichever handler ends up taking that turn. A yes two turns later
        # is a yes to whatever was asked then.
        self._unanswered = None
        yield Transcript(speaker="caller", text=text, lang=heard.upper())

        # Mirror the caller's register in improvised lines. Cheap to get wrong,
        # so it needs no confirmation — unlike the language itself.
        if self.register == "standard" and is_singlish(text):
            self.register = "singlish"

        # Switching language mid-call is disruptive, so it takes evidence:
        # either the caller asks, or they use the other language twice running.
        # Flipping on one utterance meant a stray word — or an ASR wobble —
        # threw the whole call into Mandarin.
        requested = lang_request(text)
        self._lang_turns_before = self._other_lang_turns
        switch_to: str | None = None
        if requested and requested != self.lang:
            switch_to = requested
        elif heard != self.lang and heard in ("en", "zh") and bare_answer(text) is None:
            # "是的" is a word half of Singapore uses in an English sentence.
            # Counting it as evidence would flip the whole call to Mandarin.
            self._other_lang_turns += 1
            if self._other_lang_turns >= 2:
                switch_to = heard
        else:
            self._other_lang_turns = 0

        if switch_to:
            was, self.lang = self.lang, switch_to
            self._other_lang_turns = 0
            yield SystemNote(
                text=f"Language switch {was.upper()} → {self.lang.upper()}"
                     f" ({'asked for it' if requested else 'two turns running'})",
                ok=True)
            # Say so, rather than silently continuing in a different language.
            async for ev in self._generated(LANG_BRIDGE[self.lang]):
                yield ev

        # Before any question we have pending: "stop calling me" is not an
        # answer to the cross-sell offer, and while that offer was pending it
        # was being consumed as a polite decline — "好的，没问题" — with nothing
        # recorded. An instruction not to call outranks whatever we asked.
        if asks_dnc(text):
            async for ev in self._do_not_call():
                yield ev
            return

        # We asked a yes/no question. Hearing the answer and carrying on as
        # though we had not is worse than never having asked.
        # A question we asked is settled by an answer to it, not by whatever
        # the caller happens to say next. "Aiyah so expensive lah" is a
        # complaint about the price; taken as an answer, it agreed to a
        # callback nobody had asked for.
        # "cross_sell" is excluded for the same reason as "reachable": its own
        # branch has to see every reply, answer or not. Set aside here, a
        # question about the offer fell through to the rest of the chain and
        # the call simply moved past a consent question nobody had answered.
        if self._pending and self._pending not in ("reachable", "cross_sell"):
            if yes_no(text) is None:
                # Set aside, not dropped. The keyword lists are not the last
                # word on whether this was a yes: "可以可以请安排" — yes,
                # please arrange it — read as no answer at all, and an offer
                # to have a colleague call the customer was silently
                # abandoned. `_routed` hands it back if the model sees an
                # answer the word lists missed.
                self._unanswered, self._pending = self._pending, None

        if self._pending == "reachable":
            # Someone still spelling their address has not heard the question
            # yet. Reading "Perfect, they'll be in touch" over the top of it
            # confirms a number they never answered about.
            if still_dictating(text):
                async for ev in self._finish_handoff(reachable=None):
                    yield ev
                return
            self._pending = None
            async for ev in self._finish_handoff(not denies(text)):
                yield ev
            return

        if self._pending == "officer":
            self._pending = None
            if not denies(text):
                async for ev in self._hand_off(
                        "off_topic", "Caller raised something outside the "
                                     "renewal — asked for customer care",
                        outstanding=self._outstanding(), explain=False):
                    yield ev
                return
            async for ev in self._generated(ADVISER_DECLINED[self.lang]):
                yield ev
            async for ev in self._advance():
                yield ev
            return

        if self._pending == "pricing_review":
            self._pending = None
            if not denies(text):
                async for ev in self._hand_off(
                        "pricing", "Caller asked for a lower premium — wants it "
                                   "reviewed", outstanding=self._outstanding()):
                    yield ev
                return
            async for ev in self._generated(ADVISER_DECLINED[self.lang]):
                yield ev
            async for ev in self._advance():
                yield ev
            return

        if self._pending == "cross_sell":
            self._pending = None
            # A question about the offer is not consent to hear it. Say what
            # it is and put the question again — once.
            if yes_no(text) is None and not wants_callback(text):
                if not self._cross_sell_asked_twice:
                    self._cross_sell_asked_twice = True
                    self._pending = "cross_sell"
                    async for ev in self._generated(CROSS_SELL_WHAT[self.lang]):
                        yield ev
                    return
                text = "no"                     # asked twice, answered neither
            if denies(text) or wants_callback(text):
                self._declined_cross_sell = True
                yield GateChange(gate="consent", state="block",
                                 note="Declined when asked — not pitched")
                yield ToolCall(tool="crm.log_attempt", arg="cross-sell · declined")
                async for ev in self._generated(CROSS_SELL_DECLINED[self.lang]):
                    yield ev
                async for ev in self._advance(from_turn=6):
                    yield ev
                return
            # Only an explicit yes delivers a pitch. "Not a no" is not
            # consent to be marketed to, and the register this call runs under
            # is the one where that distinction is the whole point.
            if yes_no(text) != "yes":
                self._declined_cross_sell = True
                yield GateChange(gate="consent", state="block",
                                 note="No clear yes — not pitched")
                yield ToolCall(tool="crm.log_attempt", arg="cross-sell · not agreed")
                async for ev in self._advance(from_turn=6):
                    yield ev
                return
            self._cross_sell_ok = True
            async for ev in self._say_turn(6):
                yield ev
            return

        if self._pending == "adviser_callback":
            # `yes_no`, not `bare_answer`: the gate above has already decided
            # this reply answers the question, and a stricter reading here
            # only means dropping the answer on the floor. "no, it's ok" is a
            # decline, not an unreadable line.
            answer = yes_no(text)
            self._pending = None
            if answer is not None:
                if answer == "yes":
                    yield ToolCall(tool="crm.log_attempt",
                                   arg="adviser callback · requested")
                    async for ev in self._generated(ADVISER_BOOKED[self.lang]):
                        yield ev
                else:
                    async for ev in self._generated(ADVISER_DECLINED[self.lang]):
                        yield ev
                async for ev in self._advance():
                    yield ev
                return

        if asks_if_bot(text):
            async for ev in self._generated(BOT_DISCLOSURE[self.lang],
                                            self._outstanding_question()):
                yield ev
            return

        # Laughter, a sigh, a "hmm". Sound with no words in it is not a turn:
        # not off-topic (which offered customer care over a chuckle), and not
        # a strike toward the not-understood handoff. The question stands.
        # Except while an address is being dictated: there the sub-dialogue
        # owns every reply, and "hmm" is one of the three tries it allows
        # before handing the change to a person — re-reading the premium
        # line over the top of it would be the wrong question entirely.
        if is_nonverbal(text):
            if self._unanswered:
                self._pending, self._unanswered = self._unanswered, None
                async for ev in self._generated(CLARIFY[self.lang]):
                    yield ev
                return
            line = self._outstanding_question()
            if line.rstrip().endswith(("?", "？", "吗", "嗎")):
                async for ev in self._generated(line):
                    yield ev
                return
            async for ev in self._advance():
                yield ev
            return

        if asks_for_human(text):
            async for ev in self._hand_off(
                    "requested", "Caller asked to speak to a person",
                    outstanding=f"renewal not confirmed (turn {self.turn} of 7)"):
                yield ev
            return

        if _looks_tamil(text):
            # Once is a fragment; twice is a language. A caller who has been
            # speaking English all call and produces one Tamil-looking line is
            # far more likely to have been misheard than to have switched, and
            # the cost of being wrong is a call ended in a transfer nobody
            # asked for. So the first one asks again, in the language the call
            # is already in, and only a second in a row hands over.
            self._foreign += 1
            if self._foreign < 2:
                async for ev in self._generated(CLARIFY[self.lang]):
                    yield ev
                return
            yield SystemNote(text="Tamil detected · understanding available, "
                                  "speech output not built — handing to a colleague")
            async for ev in self._hand_off(
                    "language", "Caller speaks Tamil — needs a Tamil-speaking "
                                "colleague", outstanding=self._outstanding(),
                    tongue="ta"):
                yield ev
            return

        if _looks_malay(text):
            async for ev in self._malay_escalation():
                yield ev
            return
        # Anything we could read resets the count: two Tamil-looking lines with
        # an English one between them is a recogniser slipping, not a caller
        # changing language.
        self._foreign = 0

        # ---- reacting to the caller, before the script gets its turn ----
        # These run ahead of the identity gate on purpose: repeating the line
        # we just said discloses nothing new, and someone who says they are
        # driving should be let go whoever they are.
        if wants_callback(text):
            async for ev in self._callback_close():
                yield ev
            return

        # Before wants_repeat: "say that again slower" is both, and slowing
        # down is the half that stops it being asked a third time.
        if wants_slower(text):
            async for ev in self._slow_down():
                yield ev
            return

        # "Sorry, who are you?" — answered before anything else, whether or
        # not we know yet who they are. Nothing here is their data: it is who
        # we are, which they are entitled to ask for at any point.
        # "What is this about?" — the reason for the call, then the line we
        # were on, so they get the question they were being asked.
        if asks_purpose(text):
            self._clarifies = 0
            async for ev in self._say_purpose():
                yield ev
            return

        if asks_who_we_are(text):
            self._clarifies = 0
            self._identified += 1
            if self._identified > 1:
                # The same paragraph twice is what makes a bot a bot. Say who
                # we are in a sentence, then get on with the call.
                async for ev in self._say_purpose(who_we_are_again(self.lang, self.agent_name)):
                    yield ev
                return
            if self._awaiting_identity:
                # One clip. Two AgentAudio events would be two loads of the
                # player, and the second cuts the first off mid-word.
                async for ev in self._generated(
                        who_we_are(self.lang, self.agent_name),
                        "Sorry, just to confirm — am I speaking with the policyholder?"):
                    yield ev
            else:
                async for ev in self._generated(who_we_are(self.lang, self.agent_name)):
                    yield ev
            return

        # A question about their own policy, answered from the record. This
        # sits ahead of the repeat check because "what is the address? can you
        # repeat again?" is both, and repeating the *due date* line answers
        # neither half of it.
        # Never for an advisory question: "should I increase my sum insured?"
        # names a field of the record, but answering it with that field is
        # answering a question nobody asked and skipping the gate that exists
        # for the one they did.
        # Nor for a request to *change* one: "can I change my email address?"
        # names the field, and answering with the field read the caller their
        # old address instead of opening the change they asked for.
        if (not self._awaiting_identity
                and not is_advice_request(text) and not is_price_request(text)
                and wants_record_change(text) is None):
            found = policy_answer(text, self.p, self.lang)
            if found is not None:
                answer, tool = found
                # We understood them. Three clarifies spread across an
                # otherwise productive call used to add up to a line-quality
                # handoff.
                self._clarifies = 0
                self._answered.add(policy_topic(text) or "")
                yield ToolCall(tool=tool, arg=self.p.policy_id)
                async for ev in self._generated(answer):
                    yield ev
                return

        if wants_repeat(text):
            async for ev in self._repeat():
                yield ev
            return
        self._repeats = 0

        # "You didn't hear me." Repeating the same approach is what earned the
        # complaint; the second time, stop trying and fetch a person.
        #
        # Not while a sub-dialogue is open: the thing they could not hear was
        # that sub-dialogue's question, and repeating the scripted turn
        # instead is its own kind of not listening. `_handle_email` has its
        # own version of this.
        if sounds_frustrated(text):
            self._frustrations += 1
            if self._frustrations >= 2:
                async for ev in self._hand_off(
                        "complaint", "Caller told us twice we were not "
                                     "understanding them",
                        outstanding=self._outstanding()):
                    yield ev
                return
            self.rate = min(self.rate, SLOWER_STEPS[0])
            async for ev in self._repeat(lead=APOLOGY[self.lang]):
                yield ev
            return

        # "Hello? Testing?" while we are still waiting to know who this is.
        # Not when the greeting also answers the question ("hello, yes,
        # speaking") — the identity gate below handles that properly.
        if (self._awaiting_identity and is_greeting(text)
                and check_identity(text)[0] != "pass"):
            # Shares the clarify counter: someone still saying "hello?" on
            # the third try cannot hear us, and saying it a fourth time will
            # not fix that.
            self._clarifies += 1
            if self._clarifies > 2:
                async for ev in self._hand_off(
                        "not_understood",
                        "Caller could only be heard saying hello — line quality",
                        outstanding=self._outstanding()):
                    yield ev
                return
            # One clip: the reassurance and the question are one breath, and
            # two AgentAudio events would cut the first off mid-word.
            async for ev in self._generated(
                    GREETING_REPLY[self.lang],
                    "Sorry, just to confirm — am I speaking with the policyholder?"):
                yield ev
            return


        # ---- gate 1: right party -------------------------------------
        if self._awaiting_identity:
            state, note = check_identity(text)
            if state == "block":
                async for ev in self._wrong_party(note):
                    yield ev
                return
            if state == "pending":
                # Ask once more rather than guessing. Still no disclosure.
                async for ev in self._generated(
                        "Sorry, just to confirm — am I speaking with the policyholder?"):
                    yield ev
                return
            self._awaiting_identity = False
            self.gates.set("identity", "pass")
            yield GateChange(gate="identity", state="pass")
            yield ToolCall(tool="crm.lookup", arg=self.p.policy_id)
            dnc_state, dnc_note = check_dnc(self.p)
            yield ToolCall(tool="dnc.check", arg="No Voice Call register")
            self.gates.set("dnc", dnc_state, dnc_note)
            yield GateChange(gate="dnc", state=dnc_state, note=dnc_note)
            async for ev in self._advance():
                yield ev
            return

        # ---- sub-dialogue: correcting the email -----------------------
        # Checked before everything above it can claim the utterance: "r w y i
        # one two three four" is letters and digits, which every other handler
        # reads as noise. The caller kept dictating after the bot had given up
        # on them, and each further piece of their address came back as "sorry,
        # I didn't quite catch that".
        change = wants_record_change(text)
        if change:
            async for ev in self._change_request(change, text):
                yield ev
            return

        # ---- off-script: "can it be cheaper?" -------------------------
        # The most predictable question on a renewal call, and the bot had no
        # answer for it: it asked the caller to repeat themselves three times
        # and then handed the call over as a line-quality problem.
        if is_price_request(text):
            self._clarifies = 0
            yield ToolCall(tool="policy.discount", arg=f"{self.p.discount_pct}% applied")
            self._pending = "pricing_review"
            async for ev in self._generated(price_answer(self.p, self.lang)):
                yield ev
            return

        # ---- off-script: "what do I do next?" -------------------------
        # Procedural, not advisory. The script's own turn 5 is the answer.
        if is_procedure_request(text):
            async for ev in self._generated(RENEWAL_PROCESS[self.lang]):
                yield ev
            self._answered.add("process")
            return

        # ---- off-script: advice request -------------------------------
        is_advice, note = check_advice(text)
        if is_advice:
            self.gates.set("advice", "block", note)
            yield GateChange(gate="advice", state="block", note=note)
            async for ev in self._generated(ESCALATION[self.lang]):
                yield ev
            self._pending = "adviser_callback"
            self._advice_raised = True
            return

        # ---- off-script: factual coverage question --------------------
        # The citation goes into the call record. A coverage answer nobody can
        # trace afterwards is barely better than one that was guessed.
        found = coverage_lookup(text, self.lang, self.knowledge)
        if found is not None:
            self._clarifies = 0
            yield ToolCall(tool="policy.coverage_lookup", arg=found.citation)
            async for ev in self._generated(found.text):
                yield ev
            return

        # "Didn't receive it" is about the renewal notice whenever it is said,
        # not only when the turn counter happens to read 3: on the live eval
        # a turn that had been handled differently left "呃没有收到" arriving
        # one turn later, where it went to the model and came back as a
        # request for customer care. The words say what it is.
        # From turn 2 on: once the purpose is stated there is nothing else on
        # this call to not have received. At turn 3 the question was just
        # asked, so the call moves on (the acknowledgement rides on the next
        # line); anywhere else, acknowledge and put the current line again.
        if self.turn >= 2 and not self._notice_missing and not_received(text):
            self._notice_missing = True
            # They have answered turn 3's question, one turn early. Asking it
            # anyway is the clearest way a call announces nobody is listening.
            self._answered.add("notice")
            yield ToolCall(tool="crm.flag_notice_not_received", arg=self.p.policy_id)
            self._clarifies = 0
            if self.turn == 3:
                async for ev in self._advance():
                    yield ev
                return
            async for ev in self._generated(NOTICE_MISSING[self.lang],
                                            self._outstanding_question()):
                yield ev
            return

        # Turn 3 asked whether the renewal notice arrived; a "no" there is a
        # fact about this customer, not a refusal.
        if self.turn == 3 and denies(text):
            self._answered.add("notice")
            # And it *answers the question*. Flagged but not answered, this
            # fell through to the model, which had no handler for it and
            # offered a customer care officer instead — telling a caller who
            # had just answered our own question that it was outside what
            # this call could deal with.
            self._notice_missing = True
            yield ToolCall(tool="crm.flag_notice_not_received", arg=self.p.policy_id)
            self._clarifies = 0
            async for ev in self._advance():
                yield ev
            return

        # Last: everything above declined this reply.
        #
        # A plain yes or no is an answer to whatever we just asked, in any
        # language, and moves the script on for nothing. Anything else is a
        # sentence no handler recognised — which used to be treated as
        # agreement, and is how "what's the weather like" advanced a renewal
        # call. Those go to the model, which picks a handler that already
        # exists; it never writes a word the caller hears.
        # A yes by the looser reading, a no only by the strict one. "Yes, I
        # received it." and "Sure, thank you." are answers to the question on
        # the line, and the strict reading sent them to the model — a
        # two-second wait, on the live eval a timeout, for a yes. But the
        # looser reading of *no* rests on a negation particle being present,
        # and "不乱来啊" has one without being an answer to anything: read as
        # a no it moved an English call on, which is the regression the strict
        # reading was written to prevent.
        if not (yes_no(text) == "yes" or bare_answer(text) == "no"):
            async for ev in self._routed(text):
                yield ev
            return
        self._clarifies = 0

        async for ev in self._advance():
            yield ev

    # ------------------------------------------------------------ advance

    async def _advance(self, from_turn: int | None = None) -> AsyncIterator[Event]:
        # A handoff ends the script. Everything after it — including, in one
        # recorded call, a product pitch delivered straight after telling the
        # customer we could not help — is exactly what a caller remembers.
        if self.handoff is not None:
            return

        here = self.turn if from_turn is None else from_turn
        yield TurnChange(turn=here, state="done")
        nxt = here + 1

        if nxt == 4:
            yield ToolCall(tool="policy.fetch", arg="premium + sums insured")

        if nxt == 6:
            allowed, note = may_cross_sell(self.p, self.gates, self._call_state())
            state = "pass" if allowed else "block"
            self.gates.set("consent", state, note)
            # The GateChange already carries the note; the console renders it.
            yield GateChange(gate="consent", state=state, note=note)
            if not allowed:
                yield TurnChange(turn=6, state="skip")
                nxt = 7
            else:
                # Asked, not delivered. The pitch itself is the client's
                # approved wording and is unchanged; what is new is that the
                # caller gets to decline it in one word.
                self.turn = 6
                yield TurnChange(turn=6, state="active")
                self._pending = "cross_sell"
                async for ev in self._generated(CROSS_SELL_ASK[self.lang]):
                    yield ev
                return

        if nxt > 7:
            async for ev in self._end("renewal acknowledged"):
                yield ev
            return

        async for ev in self._say_turn(nxt):
            yield ev

    async def _say_turn(self, nxt: int) -> AsyncIterator[Event]:
        self.turn = nxt
        if nxt != 6:
            yield TurnChange(turn=nxt, state="active")
        text = script.render(nxt, self.p, self.lang, self.part_of_day,
                             agent_name=self.agent_name, register=self.register,
                             answered=frozenset(self._answered))
        # Turn 3 is the due date, turn 4 the premium. If the caller already
        # asked for one, reading it out cold sounds like nobody was listening.
        lead = self._bridge()
        # They just told us the renewal notice never arrived. Turn 4 says we
        # will email it, which is the right answer — but only if we show we
        # heard the "no" first.
        if nxt == 4 and self._notice_missing:
            lead = NOTICE_MISSING[self.lang]
        # Once per call: twice running is its own kind of tic.
        if ({3: "due", 4: "premium"}.get(nxt) in self._answered
                and not self._said_already):
            lead = ALREADY_SAID[self.lang]
            self._said_already = True

        # If the caller changed their email, turn 4's text is new and cannot
        # have been warmed. Speaking it as [cached body] + [email sentence]
        # means only the short tail is rendered — two seconds rather than
        # eight. The acknowledgement gives way to the body: one lead slot,
        # and the body is the part that has to be there.
        split = script.split_on_email(text) if nxt == 4 else None
        if split is not None:
            head, tail = split
            full, buf, sr, ms = await self._voice(lead, head, tail)
        else:
            full, buf, sr, ms = await self._voice(lead, text)
        yield Transcript(speaker="agent", text=full, lang=self.lang.upper(),
                         source=script.source_label(nxt),
                         latency_ms=self._elapsed_ms(ms))
        yield AgentAudio(pcm=buf, sample_rate=sr)

        if nxt == 6:
            async for ev in self._advance(from_turn=6):
                yield ev
        elif nxt == 7:
            async for ev in self._end("renewal acknowledged"):
                yield ev

    def _outstanding(self) -> str:
        """What a colleague picking this up still has to do."""
        if self.turn < 7:
            return f"renewal not confirmed (reached turn {self.turn} of 7)"
        return ""

    # ------------------------------------------------------------- handoff

    def _call_state(self) -> CallState:
        """How the call has gone, for the one gate that should care."""
        return CallState(
            handing_off=self.handoff is not None,
            unresolved="",
            awaiting_adviser=self._advice_raised,
            impatient=self._bridges_used and "impatient" in self._bridges_used,
            declined=self._declined_cross_sell,
            comprehension_failures=self._clarifies + self._frustrations,
        )

    async def _change_request(self, kind: str, text: str) -> AsyncIterator[Event]:
        """Any change to the customer's details or to their cover goes to a
        person, and none of it is captured here.

        The bot used to try. It asked for the new address, read back what it
        thought it had heard, and on a recorded call turned "w y i a" into
        yi@hotmail.com — then made it worse on the retry, because a caller
        spelling something out more carefully is a caller the recogniser has
        already failed once. A voice line is not a form.

        Nothing is written to the record either way: what goes across is that a
        change was asked for and what the caller said, so a colleague who can
        verify them and type it correctly finishes the job.
        """
        policy = kind == "policy"
        yield ToolCall(
            tool="crm.flag_policy_change" if policy else "crm.flag_profile_change",
            arg=self.p.policy_id)
        summary = ("Policy change requested — routed to customer care" if policy
                   else "Change to the details on file requested — routed to "
                        "customer care")
        outstanding = f"caller said: {text[:200]}"
        # If they volunteered the address in the same breath, read it for the
        # colleague. A suggestion, marked as one: the model gets these right
        # far more often than not, and is confidently wrong often enough that
        # nobody should type it in without checking. Never spoken, never
        # written — it exists to save the colleague a minute.
        if not policy and dictation.might_be_dictation(text):
            heard = await dictation.email(self.backend, [text],
                                          timeout_ms=self.guardrail_timeout_ms)
            if heard.email:
                outstanding += f" · unverified reading: {heard.email}"
        async for ev in self._hand_off(
                "policy_change" if policy else "data_change", summary,
                outstanding=outstanding):
            yield ev

    async def _hand_off(self, reason: ho.Reason, summary: str,
                        outstanding: str = "",
                        tongue: str | None = None,
                        explain: bool = True) -> AsyncIterator[Event]:
        """Give the call to a person, as a procedure.

        Name the reason in the customer's terms, say what happens next and by
        when, check we can actually reach them, record it so the colleague
        does not start from nothing — and then stop. The stopping is the part
        the first build got wrong: it said the line and carried on with the
        script, pitching a product to a customer it had just failed.
        """
        if self.handoff is not None:
            return
        self.handoff = ho.Handoff(
            reason=reason, summary=summary, outstanding=outstanding,
            collected={"policy": self.p.policy_id, "name": self.p.name,
                       "verified": self.gates.as_dict().get("identity", "pending")})
        yield HandoffRequested(reason=reason, code=self.handoff.code, summary=summary,
                               warm=self.handoff.warm, outstanding=outstanding)
        yield ToolCall(tool="crm.handoff",
                       arg=f"{self.handoff.code} · {outstanding or 'nothing outstanding'}")
        for t in range(self.turn + 1, 8):
            yield TurnChange(turn=t, state="skip")
        # Reason, action and the contactability check are one breath — asking
        # "is that the best number?" out of a separate clip would cut the
        # sentence before it in half.
        self._pending = "reachable"
        async for ev in self._generated(
                ho.why(reason, self.lang, tongue) if explain else None,
                ho.action(self.lang, self.handoff.warm),
                ho.reachable(self.p.phone, self.lang)):
            yield ev

    async def _finish_handoff(self, reachable: bool | None) -> AsyncIterator[Event]:
        """`reachable=None` means they never answered — they were still
        talking. Do not claim a confirmation nobody gave."""
        self._pending = None
        table = ho.REACHABLE_YES if reachable else ho.REACHABLE_NO
        if not reachable:
            yield ToolCall(tool="crm.log_attempt", arg="callback number · to be confirmed")
        async for ev in self._generated(table[self.lang]):
            yield ev
        reason = self.handoff.reason if self.handoff else "unknown"
        async for ev in self._end(f"handed to a colleague · {reason}"):
            yield ev

    # ----------------------------------------------------------- reactions

    def _current_line(self) -> str:
        """The line we are on — which on turn 6 is not the turn's own wording.

        Turn 6 *is* the pitch. While the consent question is outstanding the
        line the caller is actually on is the question, and re-speaking the
        turn instead delivers a marketing pitch to someone who has not agreed
        to hear one. That is how "what are you trying to say just now?"
        produced the whole promotion.
        """
        if self.turn == 6 and not self._cross_sell_ok:
            return CROSS_SELL_ASK[self.lang]
        return script.render(self.turn, self.p, self.lang, self.part_of_day,
                             agent_name=self.agent_name, register=self.register,
                             answered=frozenset(self._answered))

    def _outstanding_question(self) -> str:
        """The question the caller still owes us an answer to.

        Different from `_current_line`, which is the whole turn. "Sorry, can
        you repeat?" wants all of it; "what is this about?" wants the answer
        and then the question — not the greeting and the introduction over
        again. Re-speaking the whole turn is how a caller several turns into a
        call gets greeted a second time, and asks what anyone would: "why you
        keep repeating yourself?"
        """
        return script.question_of(self._current_line())

    async def _repeat(self, lead: str | None = None) -> AsyncIterator[Event]:
        """Say the current line again instead of pressing on.

        Marching to the next turn when someone has just said "sorry, can you
        repeat?" is the moment a caller stops treating the call as a
        conversation.
        """
        self._repeats += 1
        if self._repeats > 3:
            async for ev in self._hand_off(
                    "not_understood",
                    f"Same line asked for {self._repeats - 1} times — caller "
                    "cannot hear us",
                    outstanding=self._outstanding()):
                yield ev
            return
        leads = REPEAT_LEAD[self.lang]
        lead = lead or (leads[0] if self._repeats == 1 else leads[1])
        text = self._current_line()
        full, buf, sr, ms = await self._voice(lead, text)
        yield Transcript(speaker="agent", text=full, lang=self.lang.upper(),
                         source=script.source_label(self.turn) + " · repeat",
                         latency_ms=self._elapsed_ms(ms))
        yield AgentAudio(pcm=buf, sample_rate=sr)

    async def _slow_down(self) -> AsyncIterator[Event]:
        """Take the pace down and say the line again at it.

        Advancing here was the worst of the failures: the caller asked twice,
        more emphatically the second time, and heard the *next* line faster
        than the one they had already missed.
        """
        step = min(self._slower_steps, len(SLOWER_STEPS) - 1)
        self.rate = SLOWER_STEPS[step]
        lead = (SLOWER_ACK if self._slower_steps == 0 else SLOWEST_ACK)[self.lang]
        self._slower_steps += 1
        yield SystemNote(text=f"Caller asked for a slower pace — {self.rate:.2f}x "
                              "for the rest of the call", ok=True)
        text = self._current_line()
        full, buf, sr, ms = await self._voice(lead, text)
        yield Transcript(speaker="agent", text=full, lang=self.lang.upper(),
                         source=script.source_label(self.turn) + f" · {self.rate:.2f}x",
                         latency_ms=self._elapsed_ms(ms))
        yield AgentAudio(pcm=buf, sample_rate=sr)

    async def _routed(self, text: str) -> AsyncIterator[Event]:
        """Last resort before "say that again": let the model pick a handler.

        Every branch below already exists and is already tested. The model
        chooses between them; it never contributes a word of what is said.
        """
        held, self._unanswered = self._unanswered, None
        if not self.guardrail:
            async for ev in self._without_guardrail(text):
                yield ev
            return

        # Start the model, then talk over the wait. On a phone line one to two
        # seconds of nothing is where the caller says "hello?"; a short cached
        # line covers it, and the model is already running underneath.
        pending = asyncio.ensure_future(
            router.route(self.backend, text, self.turn, self.lang,
                         timeout_ms=self.guardrail_timeout_ms))
        async for ev in self._generated(THINKING[self.lang]):
            yield ev
        got = await pending
        yield SystemNote(
            text=f"Guardrail routed an unrecognised reply to {got.label!r} "
                 f"in {got.latency_ms} ms" if got.trusted else
                 "Guardrail unavailable or answered off-menu — asking again",
            ok=got.trusted)

        if not got.trusted:
            # No verdict. A reply in the wrong script for this call, with no
            # request to switch, is presumed noise rather than handed to the
            # fallback chain — where, on the live eval, "其主要城市有圣保罗和里约
            # 热内卢" switched an English call into Mandarin because the model
            # had timed out and the noise rule never ran. The model gets the
            # chance to vindicate such a line; absent one, it does not count.
            # Without a verdict the switch evidence is *kept*: two turns
            # running in the other language change the call's language by
            # design, and a caller who simply carries on in Mandarin must not
            # be stalled because the model was slow. Only a trusted off-topic
            # verdict, below, erases a turn from that count.
            if script_mismatch(text, self.lang) and not lang_request(text):
                yield SystemNote(text="Recogniser output in the wrong script for this "
                                      "call, and no verdict from the model — treated "
                                      "as noise, not as a turn", ok=False)
                async for ev in self._generated(CLARIFY[self.lang]):
                    yield ev
                return
            async for ev in self._without_guardrail(text):
                yield ev
            return
        # A reply in the wrong script for this call that the model can make
        # nothing of is the recogniser talking, not the caller: "阿基米德的浮力
        #原理" on an English call, from someone who had said nothing. Not a
        # turn — and not evidence for a language switch either.
        if (got.label in ("off_topic", "unclear") and script_mismatch(text, self.lang)
                and not lang_request(text)):
            self._other_lang_turns = self._lang_turns_before
            yield SystemNote(text="Recogniser output in the wrong script for this "
                                  "call, and the model made nothing of it — treated "
                                  "as noise, not as a turn", ok=False)
            async for ev in self._generated(CLARIFY[self.lang]):
                yield ev
            return
        if got.label == "unclear":
            async for ev in self._clarify():
                yield ev
            return

        self._clarifies = 0
        if held and got.label in ("affirm", "deny"):
            # We asked something and the word lists could not tell whether
            # this was the answer; the model can. An offer to have a person
            # call the customer, accepted and then forgotten, is the worst
            # thing this call does — worse than asking twice.
            self._pending = held
            async for ev in self.on_caller("yes" if got.label == "affirm" else "no"):
                yield ev
            return
        if got.label == "off_topic":
            self._pending = "officer"
            async for ev in self._generated(OFFICER_OFFER[self.lang]):
                yield ev
            return
        if got.label == "human":
            async for ev in self._hand_off(
                    "requested", "Caller asked to speak to a person",
                    outstanding=self._outstanding()):
                yield ev
            return
        if got.label == "bot":
            async for ev in self._generated(BOT_DISCLOSURE[self.lang],
                                            self._outstanding_question()):
                yield ev
            return
        if got.label == "dnc":
            async for ev in self._do_not_call():
                yield ev
            return
        if got.label == "complaint":
            async for ev in self._hand_off(
                    "complaint", "Caller said we were not helping",
                    outstanding=self._outstanding()):
                yield ev
            return
        if got.label == "bad_time":
            async for ev in self._callback_close():
                yield ev
            return
        if got.label == "slower":
            async for ev in self._slow_down():
                yield ev
            return
        if got.label == "repeat":
            async for ev in self._repeat():
                yield ev
            return
        if got.label == "who_are_you":
            self._identified += 1
            if self._identified > 1:
                async for ev in self._say_purpose(who_we_are_again(self.lang, self.agent_name)):
                    yield ev
            else:
                async for ev in self._generated(who_we_are(self.lang, self.agent_name)):
                    yield ev
            return
        if got.label == "purpose":
            async for ev in self._say_purpose():
                yield ev
            return
        if got.label == "price":
            yield ToolCall(tool="policy.discount", arg=f"{self.p.discount_pct}% applied")
            self._pending = "pricing_review"
            async for ev in self._generated(price_answer(self.p, self.lang)):
                yield ev
            return
        if got.label == "procedure":
            async for ev in self._generated(RENEWAL_PROCESS[self.lang]):
                yield ev
            self._answered.add("process")
            return
        if got.label == "advice":
            self.gates.set("advice", "block", "Advice request — routed by guardrail")
            yield GateChange(gate="advice", state="block",
                             note="Advice request — routed by guardrail")
            async for ev in self._generated(ESCALATION[self.lang]):
                yield ev
            self._pending = "adviser_callback"
            self._advice_raised = True
            return
        if got.label == "email_change" and not self._awaiting_identity:
            async for ev in self._change_request(
                    wants_record_change(text) or "data", text):
                yield ev
            return
        if got.label == "coverage":
            found = coverage_lookup(text, self.lang, self.knowledge)
            if found is not None:
                yield ToolCall(tool="policy.coverage_lookup", arg=found.citation)
                async for ev in self._generated(found.text):
                    yield ev
                return
            # We can tell it is a coverage question and still not have the
            # answer grounded. Improvising one is exactly what this build does
            # not do — but "not something I can help with" is the wrong
            # sentence for a question a renewal call exists to answer.
            self._pending = "officer"
            async for ev in self._generated(COVERAGE_UNKNOWN[self.lang]):
                yield ev
            return
        if got.label == "policy_fact" and not self._awaiting_identity:
            found = policy_answer(text, self.p, self.lang)
            if found is not None:
                answer, tool = found
                yield ToolCall(tool=tool, arg=self.p.policy_id)
                async for ev in self._generated(answer):
                    yield ev
                return
            self._pending = "officer"
            async for ev in self._generated(OFFICER_OFFER[self.lang]):
                yield ev
            return

        # affirm / deny, or a label whose handler needs a verified caller.
        async for ev in self._advance():
            yield ev

    async def _without_guardrail(self, text: str) -> AsyncIterator[Event]:
        """What the call does when the model is off, slow, or unavailable.

        A reply nothing can read is still asked about again. A reply that
        merely went unrecognised moves the script on, which is what the call
        did before the guardrail existed — the model is there to do better
        than that, not to be a single point of failure that turns every
        unrecognised sentence into "sorry, say that again".
        """
        if script_mismatch(text, self.lang) or is_uninterpretable(text):
            async for ev in self._clarify():
                yield ev
            return
        self._clarifies = 0
        async for ev in self._advance():
            yield ev

    async def _say_purpose(self, lead: str | None = None) -> AsyncIterator[Event]:
        """Why we are calling, then the line we were on.

        Answering "what is this about?" with a paragraph and then waiting is
        how a call stalls: the caller gets an explanation but not the question
        they were being asked.
        """
        self._purposes += 1
        why = (PURPOSE if self._purposes == 1 else PURPOSE_AGAIN)[self.lang]
        line = self._outstanding_question()
        full, buf, sr, ms = await self._voice(lead, why, line)
        yield Transcript(speaker="agent", text=full, lang=self.lang.upper(),
                         source=script.source_label(self.turn) + " · purpose",
                         latency_ms=self._elapsed_ms(ms))
        yield AgentAudio(pcm=buf, sample_rate=sr)

    async def _clarify(self) -> AsyncIterator[Event]:
        """Ask again rather than treating a reply we did not understand as a
        reply we did."""
        self._clarifies += 1
        if self._clarifies > 2:
            async for ev in self._hand_off(
                    "not_understood",
                    "Three replies running could not be made out",
                    outstanding=self._outstanding()):
                yield ev
            return
        yield SystemNote(text="Reply not understood — asking again rather than "
                              "advancing the script", ok=False)
        async for ev in self._generated(CLARIFY[self.lang]):
            yield ev

    async def _callback_close(self) -> AsyncIterator[Event]:
        """They said it is a bad moment. Take them at their word."""
        yield SystemNote(text="Caller asked for another time — closing without "
                              "the renewal or the cross-sell", ok=True)
        for t in range(self.turn + 1, 8):
            yield TurnChange(turn=t, state="skip")
        async for ev in self._generated(CALLBACK_REPLY[self.lang]):
            yield ev
        yield ToolCall(tool="crm.log_attempt", arg="bad time · callback requested")
        async for ev in self._end("callback requested"):
            yield ev

    # --------------------------------------------------- email correction

    async def _wrong_party(self, note: str) -> AsyncIterator[Event]:
        self.gates.set("identity", "block", note)
        yield GateChange(gate="identity", state="block", note=note)
        yield SystemNote(text="Disclosure blocked: property address, premium and "
                              "policy number withheld from unverified party")
        for t in range(2, 7):
            yield TurnChange(turn=t, state="skip")
        async for ev in self._generated(WRONG_PARTY_REPLY[self.lang]):
            yield ev
        yield ToolCall(tool="crm.log_attempt", arg="wrong_party · callback requested")
        async for ev in self._end("no personal data disclosed, callback logged"):
            yield ev

    async def _malay_escalation(self) -> AsyncIterator[Event]:  # noqa: D401
        yield SystemNote(text="Malay detected · understanding available, speech "
                              "output on hold — handing to a colleague")
        async for ev in self._hand_off(
                "language", "Caller speaks Malay — needs a Malay-speaking colleague",
                outstanding=self._outstanding(), tongue="ms"):
            yield ev

    async def _do_not_call(self) -> AsyncIterator[Event]:
        """Record the instruction, say so, stop.

        Recorded first, spoken second: the caller's protection is the CRM
        entry, not the sentence. The renewal itself is unaffected — the notice
        goes by email regardless — so nothing is left half-done by ending here.
        """
        yield ToolCall(tool="crm.dnc_request", arg=f"{self.p.policy_id} · {self.p.phone}")
        self.gates.set("consent", "block", "Caller asked not to be called")
        yield GateChange(gate="consent", state="block",
                         note="Caller asked not to be called — recorded")
        for t in range(self.turn + 1, 8):
            yield TurnChange(turn=t, state="skip")
        async for ev in self._generated(DNC_ACK[self.lang]):
            yield ev
        async for ev in self._end("do-not-call requested"):
            yield ev

    async def _end(self, outcome: str) -> AsyncIterator[Event]:
        assert any(outcome.startswith(d) for d in DISPOSITIONS), \
            f"outcome {outcome!r} is not a known disposition"
        if self.ended:
            return
        self.ended = True
        if self._fell_back:
            yield SystemNote(
                text="Pre-render model unavailable for at least one line — that "
                     "line was spoken by the live voice, which is a different "
                     "speaker", ok=False)
        yield TurnChange(turn=7, state="done")
        yield CallEnded(text=f"Call complete · outcome: {outcome}", outcome=outcome)
