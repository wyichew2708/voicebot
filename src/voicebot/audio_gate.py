"""Reject non-speech before and after the recogniser.

Fed silence or room noise, Whisper-family models do not return an empty
string — they emit fluent, plausible sentences from their training data. Two
real examples from this build: "i'm a little bit scared." and a stray line
about a Hong Kong university research centre. Both arrived mid-call, from
background noise, and both went into the record as if the caller had said them.

Nothing downstream can undo that: the dialogue engine, the compliance gates and
the transcript all take the ASR at its word. So the filtering has to happen on
either side of it.
"""
from __future__ import annotations

import logging
import math
import re
import struct

log = logging.getLogger("voicebot.audio_gate")

# --- pre-ASR -------------------------------------------------------------
MIN_SPEECH_SECONDS = 0.35     # shorter than this is a cough or a door
MIN_RMS = 0.008               # quieter than this is room tone
# A frame counts as voiced at this fraction of the utterance peak. Speech is
# dynamic: vowels peak, consonants and the gaps between words sit far below.
# At 0.35 only the loudest fifth of a real reply counted, so "yes, speaking"
# read as 10% voiced and was thrown away as noise.
VOICED_REL = 0.15
# A real word, not a door slam. Measured against the *buffer's own peak*, so a
# short sharp word loses frames a longer one keeps: a crisp "yes" has one loud
# vowel and a fast decay, and most of it falls under 15% of that peak. At 0.18
# the commonest answer on this script was thrown away — a recorded session
# dropped five replies at 0.12, 0.14, 0.14, 0.16 and 0.16 s voiced, and the
# caller repeated themselves and spoke longer until something got through.
# Turns 2, 3, 4 and 6 all ask a yes/no question, so this is the answer the
# gate has to let past. A door slam is one or two frames — the 0.04 s drop in
# that same log still fails here — and the ratio, RMS and speech-rate checks
# below are what actually keep noise out.
MIN_VOICED_SECONDS = 0.10
MIN_VOICED_RATIO = 0.12       # and not one syllable adrift in padding
# The ratio above is right for a short buffer: "yes" inside half a second of
# room is a real answer that happens to be mostly silence. It is far too
# permissive for a long one — three seconds at 12% voiced is a third of a
# second of sound in a room, which is what the recogniser fills with a
# sentence out of its training data. Conversational speech runs 40-70% voiced,
# so a buffer this long has to look like someone talking.
LONG_BUFFER_SECONDS = 1.2
MIN_VOICED_RATIO_LONG = 0.25

# --- post-ASR ------------------------------------------------------------
# Speech rate is script-dependent and a single threshold cannot serve both.
# A CJK character is a whole syllable — roughly 5-7 a second in normal speech —
# where Latin script runs 15-20 characters a second for the same content. Using
# the Latin limit for Chinese lets a fabricated sentence straight through: the
# hallucination that prompted this was 23 characters in 1.1 s, comfortably
# under 25 chars/s but nearly three times the plausible Chinese rate.
MAX_CPS_LATIN = 25
MAX_CPS_CJK = 10
MIN_AUDIO_FOR_LONG_TEXT = 1.0

#: Fragments the Whisper family emits over silence, from its training data.
HALLUCINATION_PATTERNS = (
    r"thank you for watching",
    r"thanks for watching",
    r"please subscribe",
    r"subtitles? by",
    r"amara\.org",
    r"字幕",
    r"请不吝点赞",
    r"订阅",
    r"转发",
    r"打赏",
    r"明镜与点点栏目",
    # Encyclopaedia register, straight out of the training set. Both of these
    # arrived on a live English call where the caller had said nothing, and
    # two of them in a row switched the call into Mandarin.
    r"该物种",
    r"模式产地",
    r"毕业于",
    r"^本(片|文|条目)",
    r"如无特殊说明",
)
_HALLUCINATION = re.compile("|".join(HALLUCINATION_PATTERNS), re.I)

#: Scripts no caller on this line is speaking. A multilingual recogniser
#: hands back the wrong alphabet on short, noisy clips — "ah, yes" came back
#: as Korean "아 예스" — and the dialogue engine has no way to tell that from
#: a caller who really did switch language.
_IMPOSSIBLE_SCRIPTS = (
    ("Hangul", 0xAC00, 0xD7AF), ("Hangul", 0x1100, 0x11FF),
    ("kana", 0x3040, 0x30FF),
    ("Cyrillic", 0x0400, 0x04FF),
    ("Thai", 0x0E00, 0x0E7F),
    ("Arabic", 0x0600, 0x06FF),
    ("Devanagari", 0x0900, 0x097F),
)


def _foreign_script(text: str) -> str | None:
    """The script of the text, when it is one we cannot be hearing.

    English, Mandarin, Malay and Tamil are what this line carries. Anything
    else is the recogniser guessing, not the caller.
    """
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return None
    for name, lo, hi in _IMPOSSIBLE_SCRIPTS:
        n = sum(1 for ch in letters if lo <= ord(ch) <= hi)
        if n >= max(1, len(letters) * 0.5):
            return name
    return None


#: Frames of quiet this short do not end a word. A vowel dips below 15% of its
#: own peak between syllables and across a stop consonant, and splitting the
#: run there would measure "yes" as two fragments of nothing.
VOICED_BRIDGE_FRAMES = 1


def longest_voiced_run(flags: list[bool]) -> int:
    """The longest contiguous stretch of voiced frames, bridging brief dips.

    Total voiced duration cannot tell a word from keyboard noise: four clicks
    of 30 ms with quarter-second gaps between them measure the same 0.12 s as
    a short "yes", and one of them is an answer. A word is a single run; noise
    is scattered. Judging the run rather than the total is what lets the floor
    sit low enough to accept the commonest reply on this script without
    admitting the clicks.
    """
    longest = run = gap = 0
    for flag in flags:
        if flag:
            run += gap + 1
            gap = 0
        elif run and gap < VOICED_BRIDGE_FRAMES:
            gap += 1                       # a dip, not the end of the word
        else:
            longest = max(longest, run)
            run = gap = 0
    return max(longest, run)


def frame_energies(pcm: bytes, sample_rate: int, frame_ms: int = 20) -> list[float]:
    """RMS per frame, 0..1."""
    n = max(1, int(sample_rate * frame_ms / 1000))
    out: list[float] = []
    total = len(pcm) // 2
    for start in range(0, total - n + 1, n):
        chunk = struct.unpack_from(f"<{n}h", pcm, start * 2)
        acc = sum(float(s) * s for s in chunk)
        out.append(math.sqrt(acc / n) / 32768.0)
    return out


def is_speech(pcm: bytes, sample_rate: int) -> tuple[bool, str]:
    """Does this buffer plausibly contain speech?

    Returns (ok, reason). The reason is logged rather than shown to the
    operator — a caller who mumbles should see 'didn't catch that', not a
    diagnostic.
    """
    seconds = len(pcm) / 2 / sample_rate
    if seconds < MIN_SPEECH_SECONDS:
        return False, f"too short ({seconds:.2f}s)"

    energies = frame_energies(pcm, sample_rate)
    if not energies:
        return False, "no frames"

    peak = max(energies)
    flags = [e > max(MIN_RMS, peak * VOICED_REL) for e in energies]
    voiced = sum(flags)
    ratio = voiced / len(energies)
    voiced_seconds = longest_voiced_run(flags) * 0.02
    overall = math.sqrt(sum(e * e for e in energies) / len(energies))

    if overall < MIN_RMS:
        return False, f"too quiet (rms {overall:.4f})"
    # Absolute duration is the test that means something: a door slam is one
    # loud frame however the rest of the buffer is padded. The ratio only
    # backs it up, and only because the caller's audio is trimmed first.
    if voiced_seconds < MIN_VOICED_SECONDS:
        return False, f"only {voiced_seconds:.2f}s voiced in a run"
    floor = (MIN_VOICED_RATIO_LONG if seconds >= LONG_BUFFER_SECONDS
             else MIN_VOICED_RATIO)
    if ratio < floor:
        return False, f"mostly silence ({ratio:.0%} voiced over {seconds:.1f}s)"
    return True, ""


def is_plausible(text: str, audio_seconds: float) -> tuple[bool, str]:
    """Could this text have come out of that much audio?

    The speech-rate check is the strong one: a model that invents a sentence
    over half a second of noise produces an impossible number of characters
    per second, and that holds regardless of language or accent.
    """
    stripped = text.strip()
    if not stripped:
        return False, "empty"

    if _HALLUCINATION.search(stripped):
        return False, "known hallucination phrase"

    script = _foreign_script(stripped)
    if script is not None:
        return False, f"recognised as {script} — not a language on this line"

    cjk = sum(1 for ch in stripped if "\u4e00" <= ch <= "\u9fff")
    is_cjk = cjk >= len(stripped) * 0.3
    limit = MAX_CPS_CJK if is_cjk else MAX_CPS_LATIN

    if audio_seconds > 0:
        cps = len(stripped) / audio_seconds
        if cps > limit:
            return False, (f"impossible speech rate ({cps:.0f} chars/s, "
                           f"{'CJK' if is_cjk else 'latin'} limit {limit})")

    # A long sentence out of a very short clip is the same failure seen from
    # the other side.
    long_for = 20 if is_cjk else 40
    if audio_seconds < MIN_AUDIO_FOR_LONG_TEXT and len(stripped) > long_for:
        return False, f"{len(stripped)} chars from {audio_seconds:.2f}s"

    return True, ""
