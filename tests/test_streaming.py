"""Streaming synthesis.

A line the cache has never seen — a customer name that was never warmed, an
answer assembled at call time — used to cost its whole synthesis before a word
was heard, because the synthesiser returns nothing until it is finished. These
cover the pieces that let it start playing while the rest is still being made.
"""
import asyncio

from voicebot.call.engine import CallSession
from voicebot.data import personas
from voicebot.events import AgentAudio, Transcript
from voicebot.runtime.base import Speech
from voicebot.runtime.mock import MockBackend
from voicebot.spoken import speech_chunks, speech_seconds

TURN_1 = ("Good afternoon Mr Tan. This is Michael calling from Etiqa Insurance. "
          "Am I speaking with Mr Tan?")
TURN_4 = ("The final premium is 450 dollars for a 1-year plan, with sum insured "
          "of Home Contents 50000 and Renovation 30000. A 10 percent discount "
          "has been applied. I'll send an email to a.tan@example.sg — can I "
          "confirm that's correct?")


def _opening(policy_id="TH-4471-0093", rtf=0.4):
    """The opening line. `rtf` is the measured synthesis rate the session
    believes it is running at — None means it has not measured one yet."""
    session = CallSession(personas.get(policy_id), MockBackend())
    session._rtf = rtf

    async def go():
        return [e async for e in session.start()]
    return asyncio.run(go())


# ------------------------------------------------------------- the chunker

def test_chunking_loses_nothing():
    """The seam is the only thing that may change, never the words."""
    for text, lang in [(TURN_1, "en"), (TURN_4, "en"),
                       ("下午好，陈先生。我是 Etiqa 保险的Michael。请问是陈先生本人吗？", "zh")]:
        rejoined = "".join(speech_chunks(text, lang)).replace(" ", "")
        assert rejoined == text.replace(" ", ""), text


def test_an_email_survives_the_split():
    """The dots in an address are the obvious way to cut a line in half."""
    chunks = speech_chunks(TURN_4, "en")
    assert any("a.tan@example.sg" in c for c in chunks)
    assert not any(c.endswith("a.") or c.endswith("a.tan@example.") for c in chunks)


def test_an_abbreviation_does_not_end_a_chunk():
    """"Mr." ends in a full stop and does not end a sentence — splitting there
    strands a salutation on its own and the voice says it as a word."""
    for c in speech_chunks("Good afternoon Mr. Tan. How are you today?", "en"):
        assert not c.endswith("Mr.")


def test_the_first_chunk_is_short_so_the_first_word_comes_quickly():
    """The whole point: the opening buys back the latency."""
    chunks = speech_chunks(TURN_1, "en")
    assert len(chunks) > 1
    assert speech_seconds(chunks[0], "en") < speech_seconds(TURN_1, "en") / 2


def test_a_short_line_is_left_alone():
    """Synthesis costs a fixed ~0.4 s whatever the length, so cutting a short
    line into pieces is slower than saying it in one."""
    assert speech_chunks("One moment.", "en") == ["One moment."]


def test_mandarin_joins_gain_no_spaces():
    """A space inserted at a Mandarin join is a pause the writer never asked
    for, and the voice takes it."""
    for c in speech_chunks("下午好。我是Michael。请问是陈先生吗？", "zh"):
        assert " 我是" not in c and " 请问" not in c


# -------------------------------------------------------------- the engine

def test_the_opening_line_streams_in_pieces():
    """It carries the customer's name, so it is the one scripted turn that
    cannot be fully warmed for a caller the cache has never seen."""
    audio = [e for e in _opening() if isinstance(e, AgentAudio)]
    assert len(audio) > 1


def test_one_utterance_opens_and_closes_exactly_once():
    """The transport brackets on these flags. Two opens is two loads of the
    player, and the second cuts the first off mid-word."""
    audio = [e for e in _opening() if isinstance(e, AgentAudio)]
    assert [a.start for a in audio].count(True) == 1
    assert [a.final for a in audio].count(True) == 1
    assert audio[0].start and audio[-1].final


def test_the_transcript_arrives_before_any_audio():
    """The console shows the line as it starts being spoken, not after."""
    events = _opening()
    kinds = [type(e).__name__ for e in events]
    assert kinds.index("Transcript") < kinds.index("AgentAudio")


def test_a_streamed_utterance_still_carries_its_audio():
    """Chunking must not quietly drop a piece."""
    audio = [e for e in _opening() if isinstance(e, AgentAudio)]
    assert sum(len(a.pcm) for a in audio) > 16000      # more than half a second
    assert all(a.sample_rate == audio[0].sample_rate for a in audio)


def test_a_slow_machine_does_not_split_the_line():
    """Above 1x, total synthesis exceeds total audio: playback catches up and
    the line drops in the middle. Measured on an M-series Mac, splitting a
    6.3 s line into two gave the first word 2.3 s sooner and then a 3.3 s hole
    in the middle of it, which is the worse of the two."""
    audio = [e for e in _opening(rtf=1.2) if isinstance(e, AgentAudio)]
    assert len(audio) == 1
    assert audio[0].start and audio[0].final


def test_an_unmeasured_machine_does_not_split_the_line():
    """The first live line of a call costs exactly what it always cost."""
    audio = [e for e in _opening(rtf=None) if isinstance(e, AgentAudio)]
    assert len(audio) == 1


def test_a_cache_hit_never_teaches_the_rate():
    """A disk read returns in a millisecond. Believed, it would turn splitting
    on for the very next line, which is the one that has to be synthesised."""
    from voicebot.runtime.base import Speech
    session = CallSession(personas.get("TH-4471-0093"), MockBackend())
    session._observe_rtf(Speech(pcm=b"\x00\x00" * 32000, sample_rate=16000,
                                latency_ms=1, voice_source="cache"), 16000)
    assert session._rtf is None


def test_a_real_render_teaches_the_rate():
    session = CallSession(personas.get("TH-4471-0093"), MockBackend())
    session._observe_rtf(Speech(pcm=b"\x00\x00" * 32000, sample_rate=16000,
                                latency_ms=1000, voice_source="rendered"), 16000)
    assert session._rtf is not None and abs(session._rtf - 0.5) < 0.01


class _CachedBackend(MockBackend):
    """A backend with everything but the last piece already on disk."""
    def cached(self, text, lang, voice=None):
        return "speaking with" not in text


def test_cached_pieces_pay_for_the_one_that_has_to_be_made():
    """The reason a line is worth rewording. The greeting and the
    introduction are the same for every customer and can be warmed; put the
    name last and those cached seconds pay for synthesising it, on a machine
    far too slow to stream a line built entirely from scratch."""
    line = ("Good afternoon there. This is Michael calling from Etiqa Insurance. "
            "I'm doing a servicing call about your home policy renewal. "
            "Am I speaking with Mr Balakrishnan?")
    warmed = CallSession(personas.get("TH-4471-0093"), _CachedBackend())
    bare = CallSession(personas.get("TH-4471-0093"), MockBackend())
    # Slower than real time, where a line built from scratch cannot be split.
    for rtf in (1.0, 1.4, 2.0):
        warmed._rtf = bare._rtf = rtf
        assert len(warmed._stream_plan([line])) > 1, rtf
        assert bare._stream_plan([line]) == [line], rtf


def test_an_unstreamed_clip_is_unchanged():
    """Every other audio path still emits one self-contained event."""
    one = AgentAudio(pcm=b"\x00\x00", sample_rate=16000)
    assert one.start and one.final
