"""Hallucination filtering.

Both fixtures below were produced by the running system from background noise
and went into a call transcript as if the caller had said them. Everything
downstream — the dialogue engine, the compliance gates, the recording — takes
the recogniser at its word, so this is the only place they can be stopped.
"""
import math
import struct

import pytest

from voicebot.audio_gate import frame_energies, is_plausible, is_speech


def pcm(seconds, amplitude, sample_rate=16000, hz=180):
    """A tone at a given amplitude; 0 amplitude is digital silence."""
    n = int(sample_rate * seconds)
    return struct.pack(f"<{n}h", *(
        int(amplitude * 32767 * math.sin(2 * math.pi * hz * i / sample_rate))
        for i in range(n)))


# ------------------------------------------------------------- pre-ASR

def test_silence_never_reaches_the_recogniser():
    ok, why = is_speech(pcm(2.0, 0.0), 16000)
    assert not ok and "quiet" in why


def test_room_tone_never_reaches_the_recogniser():
    ok, why = is_speech(pcm(2.0, 0.002), 16000)
    assert not ok, f"room tone was accepted: {why}"


def test_a_blip_is_not_a_turn():
    ok, why = is_speech(pcm(0.15, 0.3), 16000)
    assert not ok and "short" in why


def test_actual_speech_gets_through():
    ok, why = is_speech(pcm(1.5, 0.25), 16000)
    assert ok, why


def test_frame_energies_track_amplitude():
    quiet = frame_energies(pcm(0.5, 0.01), 16000)
    loud = frame_energies(pcm(0.5, 0.4), 16000)
    assert max(quiet) < max(loud) / 10


# ------------------------------------------------------------ post-ASR

@pytest.mark.parametrize("text,seconds", [
    ("i'm a little bit scared.", 0.9),                       # observed in a real call
    ("他于二零零二年毕业于香港大学政府及政策研究中心。", 1.1),      # observed in a real call
    ("Thank you for watching!", 2.0),
    ("请不吝点赞 订阅 转发 打赏", 2.0),
])
def test_known_hallucinations_are_rejected(text, seconds):
    ok, why = is_plausible(text, seconds)
    assert not ok, f"{text!r} was accepted"


@pytest.mark.parametrize("text,seconds", [
    ("Yes, speaking.", 1.4),
    ("When is it due?", 1.2),
    ("Yes, I received it.", 1.8),
    ("Aiyah, I thought I already renew last month ah?", 3.2),
    ("好的，谢谢你。", 1.5),
    ("不好意思，可以讲华语吗？", 2.4),
])
def test_real_speech_is_kept(text, seconds):
    ok, why = is_plausible(text, seconds)
    assert ok, f"{text!r} was rejected: {why}"


def test_cjk_gets_a_lower_speech_rate_limit_than_latin():
    """One threshold cannot serve both: a CJK character is a whole syllable,
    so the same characters-per-second that is normal in English is impossible
    in Chinese. The Latin limit let a fabricated Chinese sentence straight
    through."""
    # Something a caller could actually say, so this tests the rate limit and
    # not the hallucination list — the sentence that used to be here is now
    # blocked outright, having turned up twice on a live call.
    chinese = "我想确认一下我的保费和续保日期，还有那个折扣的事情。"      # 24 chars
    assert not is_plausible(chinese, 1.1)[0], "CJK limit is too loose"
    assert is_plausible(chinese, 3.5)[0], "a slow reading should be allowed"

    latin = "a" * 23
    assert is_plausible(latin, 1.1)[0], "23 latin chars in 1.1s is normal speech"


# --- the regression that threw away real replies -------------------------
# The client brackets each turn with silence: pre-roll before the first voiced
# frame and the endpointer's 700 ms of quiet after the last. Judged over that
# padding, a short reply like "yes, speaking" read as 10% voiced and never
# reached the recogniser at all.

def _speechlike(seconds: float, sample_rate: int = 16000) -> bytes:
    """Amplitude-varying tone: loud vowels, quiet consonants, brief gaps —
    the dynamic range that a fixed fraction-of-peak threshold gets wrong."""
    import math
    import struct

    out = bytearray()
    for i in range(int(sample_rate * seconds)):
        t = i / sample_rate
        env = 0.25 + 0.75 * abs(math.sin(2 * math.pi * 3.5 * t))     # syllables
        out += struct.pack("<h", int(9000 * env * math.sin(2 * math.pi * 140 * t)))
    return bytes(out)


def _padded(core: bytes, lead_ms: int, tail_ms: int, sr: int = 16000) -> bytes:
    return bytes(int(sr * lead_ms / 1000) * 2) + core + bytes(int(sr * tail_ms / 1000) * 2)


def test_a_short_reply_survives_the_padding_the_client_adds():
    from voicebot import pcm

    turn = _padded(_speechlike(0.6), 300, 700)
    assert is_speech(pcm.trim(turn, keep_ms=100, sample_rate=16000), 16000)[0], \
        '"yes, speaking" must reach the recogniser'


def test_the_voiced_test_is_a_duration_not_a_share_of_the_buffer():
    """A door slam is one loud frame however much padding surrounds it, and a
    real reply is a real reply however much padding surrounds it. Only the
    absolute voiced time separates them."""
    from voicebot import pcm

    slam = pcm.trim(_padded(_speechlike(0.06), 400, 700), keep_ms=100, sample_rate=16000)
    reply = pcm.trim(_padded(_speechlike(0.6), 400, 700), keep_ms=100, sample_rate=16000)
    assert not is_speech(slam, 16000)[0]
    assert is_speech(reply, 16000)[0]


def test_scattered_clicks_are_still_not_a_turn():
    """Keyboard noise trims to nothing useful: the gaps stay inside."""
    from voicebot import pcm

    click, gap = _speechlike(0.03), bytes(int(16000 * 0.25) * 2)
    noise = (click + gap) * 4
    assert not is_speech(pcm.trim(noise, keep_ms=100, sample_rate=16000), 16000)[0]


# --- the recogniser reaching for the wrong alphabet -----------------------

@pytest.mark.parametrize("text,script", [
    ("아 예스", "Hangul"),
    ("ハロー", "kana"),
    ("привет", "Cyrillic"),
    ("สวัสดี", "Thai"),
])
def test_an_impossible_script_is_not_a_transcript(text, script):
    """A multilingual recogniser hands back the wrong alphabet on short, noisy
    clips — "ah, yes" came back as Korean. The dialogue engine cannot tell
    that from a caller who really did switch language."""
    ok, why = is_plausible(text, 1.2)
    assert not ok and script in why


@pytest.mark.parametrize("text", [
    "yes, speaking", "我要改邮箱", "boleh tolong ulang", "ஹலோ",
    "Tan Wei Ming here", "好的，谢谢",
])
def test_the_four_languages_on_this_line_are_left_alone(text):
    """English, Mandarin, Malay and Tamil are what this line carries."""
    assert is_plausible(text, 2.0)[0], text


# --- keeping one speaker for a whole call ---------------------------------

def _tone(freq: float, seconds: float = 1.0, sr: int = 16000) -> bytes:
    import array
    import math

    return array.array("h", (int(8000 * math.sin(2 * math.pi * freq * i / sr))
                             for i in range(int(sr * seconds)))).tobytes()


@pytest.mark.parametrize("freq,ratio", [(200, 1.2), (200, 0.85), (150, 1.1)])
def test_pitch_shift_moves_the_pitch_and_keeps_the_duration(freq, ratio):
    """Cloning re-derives the speaker per line, so every rendered line is
    normalised onto one pitch. A shift that also changed the length would
    make the agent speak faster as well as higher."""
    from voicebot import pcm

    src = _tone(freq)
    out = pcm.pitch_shift(src, ratio)
    assert abs(pcm.median_f0(out) - freq * ratio) < 3
    assert abs(len(out) / len(src) - 1.0) < 0.05


def test_a_pitch_shift_of_one_is_a_no_op():
    from voicebot import pcm

    src = _tone(180)
    assert pcm.pitch_shift(src, 1.0) == src


def test_median_f0_reports_nothing_for_silence():
    import math

    from voicebot import pcm

    assert math.isnan(pcm.median_f0(bytes(32000)))


def _voiced(freq: float, seconds: float = 1.0, sr: int = 16000) -> bytes:
    """Harmonic-rich, like voiced speech — the case plain autocorrelation gets
    wrong by an octave."""
    import numpy as np

    t = np.arange(int(sr * seconds)) / sr
    x = sum(np.sin(2 * np.pi * freq * k * t) / k for k in range(1, 9))
    return (x / np.abs(x).max() * 8000).astype(np.int16).tobytes()


@pytest.mark.parametrize("freq", [95, 110, 150, 162, 200, 233, 300, 340])
def test_pitch_measurement_does_not_drop_an_octave(freq):
    """Plain autocorrelation picks whichever lag peaks highest, and for speech
    that is regularly twice the true period: a 182 Hz line read as 107 Hz and
    the normaliser then shifted it the wrong way, making the drift worse."""
    from voicebot import pcm

    got = pcm.median_f0(_voiced(freq))
    assert abs(got - freq) / freq < 0.02, f"{freq} Hz measured as {got:.0f} Hz"


def test_measuring_then_shifting_lands_on_the_target():
    """The property the pitch normaliser depends on."""
    from voicebot import pcm

    for freq in (140, 190, 250):
        src = _voiced(freq)
        ratio = 180 / pcm.median_f0(src)
        assert abs(pcm.median_f0(pcm.pitch_shift(src, ratio)) - 180) < 5


def _syllabic(freq: float, seconds: float = 1.5, sr: int = 16000) -> bytes:
    """Voiced tone under a syllable envelope — loud vowels, quiet consonants.
    A pure tone has uniform energy and hides an energy-biased frame search."""
    import numpy as np

    t = np.arange(int(sr * seconds)) / sr
    carrier = sum(np.sin(2 * np.pi * freq * k * t) / k for k in range(1, 9))
    env = 0.15 + 0.85 * np.abs(np.sin(2 * np.pi * 3.0 * t))
    x = carrier / np.abs(carrier).max() * env
    return (x * 8000).astype(np.int16).tobytes()


@pytest.mark.parametrize("rate", [0.7, 0.85, 1.2])
def test_time_stretch_does_not_move_the_pitch(rate):
    """The whole point of stretching rather than resampling. The frame search
    scored candidates by raw dot product, which is proportional to their
    energy, so it picked the loudest frame rather than the aligned one — a
    170 Hz clip stretched by 1.15 came out at 154 Hz."""
    from voicebot import pcm

    src = _syllabic(170)
    out = pcm.stretch(src, rate)
    assert abs(pcm.median_f0(out) - 170) < 6, "the stretch moved the pitch"
    assert abs((len(out) / len(src)) - 1 / rate) < 0.05, "wrong duration"


@pytest.mark.parametrize("ratio", [0.85, 1.15])
def test_pitch_shift_lands_on_target_for_speech_like_audio(ratio):
    from voicebot import pcm

    src = _syllabic(170)
    out = pcm.pitch_shift(src, ratio)
    assert abs(pcm.median_f0(out) - 170 * ratio) < 8
    assert abs(len(out) / len(src) - 1.0) < 0.05


def test_f0_stats_reports_how_many_frames_backed_the_estimate():
    """A pitch measured over a handful of frames is not worth correcting on —
    the pre-render pass re-draws those lines instead of stretching them."""
    from voicebot import pcm

    med, n, spread = pcm.f0_stats(_syllabic(170, seconds=2.0))
    assert abs(med - 170) < 6 and n >= 12
    med, n, spread = pcm.f0_stats(bytes(16000))
    assert n == 0 and med != med


def test_the_pitch_correction_skips_a_clip_it_cannot_measure_well():
    from voicebot import config
    from voicebot.runtime.prerender import PrerenderCache

    cfg = config.load("mac-polyglot")
    cache = PrerenderCache(cfg["backend"]["tts"]["prerender"], cfg["audio"]["sample_rate"])
    blip = _syllabic(300, seconds=0.12)          # too little to judge
    assert cache.normalise_pitch(blip, "male") == blip


# --- what the recogniser says when nobody is speaking ---------------------

@pytest.mark.parametrize("said", [
    "他于二零零三年毕业于香港大学政治与行政学系。",
    "该物种的模式产地在印度。",
])
def test_encyclopaedia_register_is_never_a_caller(said):
    """Both of these arrived on a live English call where the caller had said
    nothing, and two in a row switched the call into Mandarin. They are long
    enough that the speech-rate check cannot catch them — 22 characters over
    two seconds is a perfectly ordinary rate."""
    assert is_plausible(said, 2.5) == (False, "known hallucination phrase")


def test_a_long_buffer_has_to_look_like_someone_talking():
    """12% voiced is right for a short buffer: "yes" inside half a second of
    room is a real answer that is mostly silence. Over three seconds it is a
    third of a second of sound in a room — which is what the recogniser fills
    with a sentence out of its training data."""
    import math

    rate = 16000

    def buffer(seconds, voiced_fraction):
        n = int(seconds * rate)
        on = int(n * voiced_fraction)
        out = bytearray()
        for i in range(n):
            amp = 9000 if i < on else 0
            v = int(amp * math.sin(2 * math.pi * 150 * i / rate))
            out += v.to_bytes(2, "little", signed=True)
        return bytes(out)

    ok, why = is_speech(buffer(0.6, 0.30), rate)
    assert ok, why                       # a short, mostly-quiet "yes" still passes
    ok, why = is_speech(buffer(3.0, 0.15), rate)
    assert not ok and "mostly silence" in why
    ok, why = is_speech(buffer(3.0, 0.55), rate)
    assert ok, why                       # someone actually talking
