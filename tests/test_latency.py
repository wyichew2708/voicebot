import pathlib

"""Latency semantics.

The design claim is that scripted turns are cheap because their audio was
rendered at build time. An earlier version got its latency number by calling
the LLM and discarding the output, which on a real backend would have run a
35B generation per scripted line — inverting the optimisation it was meant to
demonstrate.
"""
import asyncio

from voicebot.call.engine import CallSession
from voicebot.data import personas
from voicebot.runtime import load_backend
from voicebot.runtime.mock import MockBackend


class _SpyBackend(MockBackend):
    def __init__(self):
        super().__init__()
        self.completes = 0
        self.speaks: list[bool] = []
        self.voices: list[str | None] = []

    async def complete(self, system, user, lang):
        self.completes += 1
        return await super().complete(system, user, lang)

    async def speak(self, text, lang, prerendered, voice=None):
        self.speaks.append(prerendered)
        self.voices.append(voice)
        return await super().speak(text, lang, prerendered, voice)


def _drive(policy_id, replies):
    spy = _SpyBackend()
    session = CallSession(personas.get(policy_id), spy)

    async def go():
        async for _ in session.start():
            pass
        for r in replies:
            async for _ in session.on_caller(r):
                pass

    asyncio.run(go())
    return spy, session


def test_scripted_turns_never_invoke_the_llm():
    spy, _ = _drive("TH-4471-0093",
                    ["Yes speaking", "When", "Yes", "Correct", "Thanks", "ok"])
    assert spy.completes == 0, "a pre-rendered turn must not run generation"
    assert spy.speaks, "turns should be timed through speak()"
    assert all(spy.speaks), "every turn on the happy path is pre-rendered"


def test_fixed_off_script_lines_keep_the_call_in_one_voice():
    """The escalation wording never varies, so it comes off the cache like the
    script does. The live model is a different speaker; hearing the escalation
    delivered by someone else is worse than waiting for it."""
    spy, _ = _drive("TH-4471-0093",
                    ["Yes speaking", "Should I increase my cover?"])
    assert spy.completes == 0
    assert all(spy.speaks), "an off-script line dropped to the live voice"


def test_nothing_the_agent_says_uses_the_other_voice():
    """The pre-render model and the live model are not the same speaker. A
    call that uses both changes voice halfway through, which a caller notices
    before anything else. Even the dictated-email read-back — different on
    every call — takes a render rather than the other mouth."""
    spy, _ = _drive("TH-4471-0093",
                    ["Yes speaking", "When", "Yes", "change my email please",
                     "w m dot tan at example dot s g", "yes that's right"])
    assert spy.speaks and all(spy.speaks), "a line was spoken by the live voice"


def test_a_fallback_to_the_live_voice_is_reported_not_hidden():
    """It should only happen when the pre-render model is unavailable, and
    when it does the operator is told rather than left to hear it."""
    import asyncio

    from voicebot.call.engine import CallSession
    from voicebot.runtime.base import Speech

    class _LiveOnly(MockBackend):
        async def speak(self, text, lang, prerendered, voice=None):
            sp = await super().speak(text, lang, prerendered, voice)
            return Speech(pcm=sp.pcm, sample_rate=sp.sample_rate,
                          latency_ms=sp.latency_ms, voice_source="live")

    session = CallSession(personas.get("TH-4471-0093"), _LiveOnly())

    async def go():
        out = []
        async for ev in session.start():
            out.append(ev)
        for r in ("Yes speaking", "sorry, I'm driving now"):
            async for ev in session.on_caller(r):
                out.append(ev)
        return out

    notes = [e.text for e in asyncio.run(go()) if e.kind == "system"]
    assert any("different speaker" in n for n in notes)


def test_prerendered_turns_are_cheaper_than_generated_ones():
    be = load_backend({"profile": "mock"})
    pre = asyncio.run(be.speak("hello", "en", prerendered=True))
    gen = asyncio.run(be.speak("hello", "en", prerendered=False))
    assert pre.latency_ms < gen.latency_ms, \
        "pre-rendered audio must not cost the same as synthesis"
    assert pre.pcm and gen.pcm, "speak() must return playable audio"


def test_typed_mandarin_switches_the_script_locale():
    """The audio path gets a language tag from the ASR; the typed path has to
    detect it, or the mid-call switch never fires in a rehearsed demo."""
    spy, session = _drive("TH-4471-0093",
                          ["Yes speaking", "不好意思，可以讲华语吗？"])
    assert session.lang == "zh"


def test_latency_covers_transcription_when_the_caller_arrives_as_audio():
    """The console labels this 'voice-to-voice', so the clock must start when
    the caller's audio lands, not when synthesis begins."""
    import time

    spy = _SpyBackend()
    session = CallSession(personas.get("TH-4471-0093"), spy)

    async def go():
        async for _ in session.start():
            pass
        started = time.perf_counter() - 0.250          # pretend 250 ms of ASR
        out = []
        async for ev in session.on_caller("Yes speaking", started_at=started):
            out.append(ev)
        return out

    events = asyncio.run(go())
    agent = [e for e in events
             if e.kind == "transcript" and e.speaker == "agent"][0]
    assert agent.latency_ms >= 250, (
        f"reported {agent.latency_ms} ms — transcription time was dropped")


def test_backend_reframes_tts_output_to_the_configured_sample_rate():
    """Kokoro emits 24 kHz. Framing that as 16 kHz plays every line 1.5x slow
    and a fifth too low — audible immediately, and easy to ship unnoticed
    because nothing errors."""
    import inspect

    from voicebot.runtime import mlx_backend

    src = inspect.getsource(mlx_backend.MLXBackend.synthesize)
    assert "resample" in src, "TTS output must be resampled, not assumed"
    assert 'getattr(seg, "sample_rate"' in src, \
        "read the segment's own sample rate rather than hardcoding one"


def test_every_agent_line_is_followed_by_audio():
    """A transcript with no audio behind it is a bot answering the phone in
    silence. The greeting was exactly that bug."""
    from voicebot.events import AgentAudio

    backend = MockBackend()
    session = CallSession(personas.get("TH-4471-0093"), backend)

    async def go():
        events = []
        async for ev in session.start():
            events.append(ev)
        for r in ["Yes speaking", "When", "Yes", "Correct", "Thanks", "ok"]:
            async for ev in session.on_caller(r):
                events.append(ev)
        return events

    events = asyncio.run(go())
    for i, ev in enumerate(events):
        if ev.kind == "transcript" and ev.speaker == "agent":
            nxt = events[i + 1] if i + 1 < len(events) else None
            assert isinstance(nxt, AgentAudio) and nxt.pcm, \
                f"agent line {ev.text[:40]!r} has no audio"


def test_the_chosen_voice_reaches_the_backend():
    """Voice is fixed for the call — swapping speaker mid-conversation is as
    jarring as swapping language."""
    spy = _SpyBackend()
    session = CallSession(personas.get("TH-4471-0093"), spy, voice="female")

    async def go():
        async for _ in session.start():
            pass
        async for _ in session.on_caller("Yes speaking"):
            pass

    asyncio.run(go())
    assert spy.voices, "speak() was never called"
    assert set(spy.voices) == {"female"}, f"voice drifted: {set(spy.voices)}"


def test_each_voice_gets_its_own_cache_entry():
    """Otherwise switching voice would serve the previous speaker's audio."""
    from voicebot import config
    from voicebot.runtime.prerender import PrerenderCache

    pr = config.load("mac-polyglot")["backend"]["tts"]["prerender"]
    cache = PrerenderCache(pr, 16000)
    vids = list(cache.voices())
    assert len(vids) >= 2, "expected at least two selectable voices"
    keys = {cache.key("Good afternoon.", "en", v) for v in vids}
    assert len(keys) == len(vids), "voices would collide on one cache file"


def test_an_unknown_voice_falls_back_rather_than_rendering_voiceless():
    from voicebot import config
    from voicebot.runtime.prerender import PrerenderCache

    pr = config.load("mac-polyglot")["backend"]["tts"]["prerender"]
    cache = PrerenderCache(pr, 16000)
    default = cache.default_voice()
    assert cache.speaker_for("does_not_exist") == cache.speaker_for(default)
    assert cache.reference_for("does_not_exist") == cache.reference_for(default)


def test_voices_use_a_named_speaker_not_a_sampled_description():
    """The reported bug: a different voice on every line.

    VoiceDesign samples a fresh speaker per call — measured F0 across three
    lines with one instruction and a fixed seed was 100, 119 and 292 Hz, which
    is three different people. A named CustomVoice speaker is a fixed identity.
    """
    from voicebot import config
    from voicebot.runtime.prerender import PrerenderCache

    pr = config.load("mac-polyglot")["backend"]["tts"]["prerender"]
    assert "VoiceDesign" not in pr["model"], \
        "VoiceDesign resamples the speaker on every line"
    cache = PrerenderCache(pr, 16000)
    for vid in cache.voices():
        anchored = cache.speaker_for(vid) or cache.reference_for(vid)
        assert anchored, f"voice {vid!r} is neither a named speaker nor a reference clip"


def test_one_voice_is_one_speaker_across_every_scripted_turn():
    """Whatever else varies between turns, the speaker must not."""
    from voicebot import config
    from voicebot.call import script
    from voicebot.runtime.prerender import PrerenderCache

    pr = config.load("mac-polyglot")["backend"]["tts"]["prerender"]
    cache = PrerenderCache(pr, 16000)
    policy = personas.get("TH-4471-0093")
    for vid in cache.voices():
        anchors = {(cache.speaker_for(vid), cache.reference_for(vid)) for _ in range(1, 8)}
        assert len(anchors) == 1, f"{vid} resolved to {anchors}"
        # and every turn keys to that same speaker
        keys = {cache.key(script.render(n, policy, "en"), "en", vid) for n in range(1, 8)}
        assert len(keys) == 7, "turns should cache separately but share a speaker"


def test_reference_clips_exist_on_disk():
    """A missing reference silently falls back to an unanchored voice, which is
    the drift bug returning by another route."""
    from pathlib import Path
    from voicebot import config
    from voicebot.runtime.prerender import PrerenderCache

    pr = config.load("mac-polyglot")["backend"]["tts"]["prerender"]
    cache = PrerenderCache(pr, 16000)
    for vid in cache.voices():
        ref = cache.reference_for(vid)
        if ref:
            assert Path(ref).is_file(), f"{vid}: reference {ref} is missing"


def test_generation_params_are_part_of_the_cache_key():
    """Changing temperature changes the audio, so it must invalidate."""
    from voicebot import config
    from voicebot.runtime.prerender import PrerenderCache

    pr = dict(config.load("mac-polyglot")["backend"]["tts"]["prerender"])
    a = PrerenderCache({**pr, "params": {"temperature": 0.5}}, 16000).key("hi", "en", "male")
    b = PrerenderCache({**pr, "params": {"temperature": 0.9}}, 16000).key("hi", "en", "male")
    assert a != b, "a parameter change would serve stale audio"


def test_every_fixed_line_the_engine_speaks_is_warmable():
    """A line spoken as a lead-in always comes from the cache. One that the
    pre-render pass does not know about misses and synthesises mid-turn, in
    front of the line it introduces."""
    from voicebot.call import engine

    warm = {text for text, _ in engine.prerenderable_lines()}
    for table in (engine.GREETING_REPLY, engine.CLARIFY, engine.SLOWER_ACK,
                  engine.SLOWEST_ACK, engine.CALLBACK_REPLY):
        for line in table.values():
            assert line in warm, f"not warmed: {line[:40]!r}"


def test_both_registers_of_every_improvised_line_are_warmed():
    """Warming only the Singlish rewordings left the standard forms to render
    live — the identity re-ask cost 7.6 s mid-call the first time it ran."""
    from voicebot.call import engine

    warm = {text for text, _ in engine.prerenderable_lines()}
    for standard, singlish in engine.SINGLISH_VARIANTS.items():
        assert standard in warm, f"standard form not warmed: {standard[:40]!r}"
        assert singlish in warm, f"singlish form not warmed: {singlish[:40]!r}"


def test_the_two_cloning_profiles_normalise_to_the_same_pitch():
    """The cache is keyed on the target, so a mismatch between the Mac and the
    server would make every line rendered on one miss on the other."""
    from voicebot import config
    from voicebot.runtime.prerender import PrerenderCache

    seen = {}
    for profile in ("mac-polyglot", "rhel"):
        cfg = config.load(profile)
        pr = cfg.get("backend", {}).get("tts", {}).get("prerender", {})
        cache = PrerenderCache(pr, cfg["audio"]["sample_rate"])
        for v in cache.voices():
            seen.setdefault(v, set()).add(cache.target_f0(v))
    for v, targets in seen.items():
        assert len(targets) == 1, f"{v} normalises differently per profile: {targets}"
        assert 0 not in targets, f"{v} has no pitch target — drift is unmanaged"


def test_a_line_far_from_the_voices_pitch_is_drawn_again_not_stretched():
    """Cloning samples a speaker per line, and some draws land a long way off.
    Correcting a big miss by stretching is audible; drawing again is not, and
    rendering is a build step."""
    from voicebot.runtime.prerender import PrerenderCache

    assert PrerenderCache.RETRIES > 1
    assert 0 < PrerenderCache.ACCEPT < 0.12, \
        "accepting a wider miss puts the stretch back in the audible range"


def test_the_pitch_correction_is_clamped_so_a_bad_measurement_cannot_run_away():
    from voicebot import config
    from voicebot.runtime.prerender import PrerenderCache

    cfg = config.load("mac-polyglot")
    cache = PrerenderCache(cfg["backend"]["tts"]["prerender"], cfg["audio"]["sample_rate"])
    quiet = bytes(16000)                       # nothing to measure
    assert cache.normalise_pitch(quiet, "male") == quiet


def test_a_cache_miss_on_a_live_call_renders_once():
    """The retry budget is for the build. On a call the caller is sitting in
    the silence: four draws turned a 2.6 s miss into 10.6 s."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "src/voicebot/runtime/mlx_backend.py").read_text()
    assert "self.prerender.render, text, lang, voice, 1" in src, \
        "a live cache miss uses the build-time retry budget again"


def test_a_voice_at_its_natural_speed_keeps_the_keys_it_already_had():
    """`rate` is in the cache key, because changing the pace changes the
    audio. But at 1.0 it must contribute nothing at all — an empty element
    still carries its separator, and that alone turned every line of both
    voices into a miss and cost an hour of re-rendering identical files."""
    from voicebot.runtime.prerender import PrerenderCache

    base = {"model": "m", "voices": {"female": {"ref_audio": "f.wav",
                                                "target_f0": 233}}}
    paced = {"model": "m", "voices": {"female": {"ref_audio": "f.wav",
                                                 "target_f0": 233, "rate": 1.0}}}
    a = PrerenderCache(base, 16000).key("hello", "en", "female")
    b = PrerenderCache(paced, 16000).key("hello", "en", "female")
    assert a == b, "declaring the default pace changed the key"

    faster = {"model": "m", "voices": {"female": {"ref_audio": "f.wav",
                                                  "target_f0": 233, "rate": 1.25}}}
    assert PrerenderCache(faster, 16000).key("hello", "en", "female") != a
