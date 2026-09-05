"""Pre-rendered audio cache for scripted turns.

Six of the seven turns are fixed wording, so their audio can be generated once
and played from disk. That is what makes the design's latency claim true — and
it is also what lets a slow, better-sounding model voice the scripted portion
without costing anything at call time.

Qwen3-TTS VoiceDesign takes ~1.4 s a line against Kokoro's 0.2 s, which is far
too slow to say live. Generated once at build time it is free, and it can be
*directed* toward a Singaporean voice in a way no preset voice can.
"""
from __future__ import annotations

import hashlib
import logging
import math
import wave
from pathlib import Path
from typing import Any

from ..spoken import segment_by_script

log = logging.getLogger("voicebot.prerender")


def for_language(value: Any, lang: str | None) -> Any:
    """A per-voice setting that may differ by language.

    A scalar applies to every language, which is what every entry was until
    the Mandarin references arrived. A mapping is read by the language of the
    line being rendered — the whole line, so an English address inside a
    Mandarin sentence is spoken by the Mandarin speaker rather than by a
    second voice cutting in at the seam — falling back to `en`, then to
    whatever is listed first, so a voice with only one clip still renders.
    """
    if not isinstance(value, dict):
        return value
    if lang in value:
        return value[lang]
    if "en" in value:
        return value["en"]
    return next(iter(value.values()), None)


class PrerenderCache:
    #: How many times to re-draw a line whose pitch lands far from the voice.
    #: A build-time budget: at call time the caller is waiting, so a miss is
    #: rendered once. Four draws turned a 2.6 s miss into 10.6 s of silence
    #: in the middle of a live call.
    RETRIES = 4
    #: Accept a draw within this much of the target, in log units (~6%).
    ACCEPT = 0.06
    #: A pitch measured over fewer frames than this is not worth correcting on.
    MIN_VOICED_FRAMES = 12

    """Content-addressed wav cache. The key covers everything that changes the
    audio, so a reworded script or a different voice instruction simply misses
    and regenerates rather than serving a stale line."""

    def __init__(self, cfg: dict[str, Any], sample_rate: int) -> None:
        self.cfg = cfg or {}
        self.sample_rate = sample_rate
        self.dir = Path(self.cfg.get("cache_dir", "voices/cache"))
        self.dir.mkdir(parents=True, exist_ok=True)
        self._model: Any = None
        self._model_failed = False

    # ------------------------------------------------------------- keying

    # ------------------------------------------------------------- voices

    def lang_code(self, lang: str) -> str:
        """What the model calls this language.

        Not optional. The multilingual checkpoint defaults to English and
        phonemises whatever it is given accordingly, so every Mandarin line
        was rendered by the English front-end — the audio came back as
        English-sounding nonsense at roughly a third of the right duration,
        and nothing in the pipeline noticed because the file was there and
        the right length to play.
        """
        override = self.cfg.get("lang_codes", {})
        return str(override.get(lang, lang)).lower()

    def voices(self) -> dict[str, dict[str, str]]:
        return self.cfg.get("voices", {})

    def default_voice(self) -> str:
        voices = self.voices()
        return self.cfg.get("default_voice") or (next(iter(voices), "") if voices else "")

    def _entry(self, voice: str | None) -> dict[str, str]:
        voices = self.voices()
        name = voice or self.default_voice()
        entry = voices.get(name)
        if entry is None and voices:
            log.warning("unknown voice %r — falling back to %r", name, self.default_voice())
            entry = voices.get(self.default_voice(), {})
        return entry or {}

    def speaker_for(self, voice: str | None = None,
                    lang: str | None = None) -> str | None:
        """Named CustomVoice speaker. A fixed identity, unlike a voice
        description, which samples a new speaker on every call. Per language
        where the entry is a mapping, like the reference clip."""
        return for_language(self._entry(voice).get("speaker"), lang)

    def reference_for(self, voice: str | None = None,
                      lang: str | None = None) -> str | None:
        """Reference wav for a cloning model. A fixed file, so the speaker is
        anchored — cloning still drifts more than a named speaker, but the
        identity is not resampled from scratch on every line.

        Per language where the profile says so. Cloned from an English
        speaker, a Mandarin line came back from the recogniser with every
        character right and none of the punctuation: the tones and phrasing
        were flat, because the voice being imitated had never spoken
        Mandarin. `ref_audio: {en: ..., zh: ...}` clones a Mandarin call from
        a Mandarin speaker, and keeps that speaker for the whole call,
        English fragments included.
        """
        return for_language(self._entry(voice).get("ref_audio"), lang)

    def reference_text_for(self, voice: str | None = None,
                           lang: str | None = None) -> str | None:
        """What is said on the reference clip, where the profile records it.

        Chatterbox never asked. CosyVoice 3 and F5-TTS clone measurably
        better when told, and Fish will not clone without it — so a voice
        may carry `ref_text`, per language like the clip itself. Absent for
        every shipped voice, which keeps every existing cache key as it is.

        Unlike the clip, a transcript does not fall back across languages: a
        Mandarin line that clones the Mandarin clip must not be told the
        English clip's words. The transcript is the one for the clip that
        was actually chosen, or nothing.
        """
        entry = self._entry(voice)
        texts = entry.get("ref_text")
        if not isinstance(texts, dict):
            return texts or None
        refs = entry.get("ref_audio")
        if isinstance(refs, dict):
            # Which language's clip `reference_for` resolved to.
            key = lang if lang in refs else ("en" if "en" in refs else next(iter(refs), None))
        else:
            key = lang
        return texts.get(key) or None

    def gender_for(self, voice: str | None = None) -> str:
        """"male" or "female", for models that pick a preset rather than
        clone. Declared on the voice where the profile says; a recorded voice
        carries none, so its measured pitch decides, with the boundary where
        adult voices actually divide rather than at the midpoint."""
        entry = self._entry(voice)
        declared = str(entry.get("gender") or "").lower()
        if declared in ("male", "female"):
            return declared
        f0 = float(for_language(entry.get("target_f0"), "en") or 0)
        return "female" if f0 >= 165 else "male"

    def target_f0(self, voice: str | None = None, lang: str | None = None) -> float:
        """Pitch every line of this voice is normalised to, in Hz. 0 disables.

        Cloning re-derives the speaker for each line, and short lines give it
        least to anchor on: measured across one call's audio the male voice
        ranged 147-193 Hz, which is nearly four semitones and reads as the
        speaker changing mid-conversation. Rendering is a build step, so this
        costs nothing at call time.

        Per language like the reference: a Mandarin clone lands on its own
        pitch — measured 226 Hz for zf_xiaobei and 135 Hz for zm_yunjian over
        five scripted lines — and dragging it toward the English voice's
        figure would be the very drift this exists to remove.
        """
        entry = self._entry(voice)
        return float(for_language(entry.get("target_f0"), lang)
                     or for_language(self.cfg.get("target_f0"), lang) or 0)

    def rate_for(self, voice: str | None = None, lang: str | None = None) -> float:
        """How much to speed this voice up, 1.0 for as rendered.

        Cloning copies the reference speaker's *delivery*, not just their
        timbre, and the shipped female reference is a slow speaker: measured
        across five scripted lines she read at 134 words a minute against the
        male voice's 176, which is heard as the voice being oddly deliberate
        rather than as a fault. Neither `cfg_weight` nor `exaggeration` moves
        it — swept, 132-138 wpm throughout — and Chatterbox ignores `speed`
        outright. So the pace is corrected after the fact, with the same
        pitch-preserving stretch the "speak slower" path uses. A build-time
        cost; nothing at call time.
        """
        entry = self._entry(voice)
        return float(for_language(entry.get("rate"), lang)
                     or for_language(self.cfg.get("rate"), lang) or 1.0)

    def params_for(self, voice: str | None = None) -> dict[str, Any]:
        """Per-voice generation parameters over profile-wide defaults. A lower
        sampling temperature measurably reduces speaker drift."""
        merged = dict(self.cfg.get("params", {}))
        merged.update(self._entry(voice).get("params", {}) or {})
        return merged

    def instruct_for(self, lang: str, voice: str | None = None) -> str:
        """Voice description, for the VoiceDesign model. Retained so an
        instruct-based profile still works, but it does not hold a speaker
        steady across lines — prefer a named speaker."""
        entry = self._entry(voice)
        if lang in entry:
            return entry[lang]
        return self.cfg.get("instruct", {}).get(lang, "")

    def key(self, text: str, lang: str, voice: str | None = None) -> str:
        # Everything that changes the audio belongs in the key, or a config
        # change silently serves the previous voice.
        # How the line is broken up and spelled changes the audio, so it is
        # in the key — otherwise a fix to the spelling silently serves the
        # rendering it was meant to replace. Empty for anything not split,
        # which keeps every English key stable.
        pieces = segment_by_script(text, lang)
        shape = "" if pieces == [(text, lang)] else repr(pieces)
        parts = [self.cfg.get("model", ""), lang, self.lang_code(lang), shape,
                 self.speaker_for(voice, lang) or "",
                 self.reference_for(voice, lang) or "",
                 self.instruct_for(lang, voice),
                 repr(sorted(self.params_for(voice).items())),
                 f"f0={self.target_f0(voice, lang):.1f}"]
        # Appended only when the voice is actually paced. An empty element
        # still contributes its separator, so a voice back at its natural
        # speed would miss every line it already has on disk and re-render
        # an identical file under a new name.
        rate = self.rate_for(voice, lang)
        if abs(rate - 1.0) > 1e-6:
            parts.append(f"rate={rate:.3f}")
        # Same rule: only a voice that carries a transcript changes its key.
        ref_text = self.reference_text_for(voice, lang)
        if ref_text:
            parts.append(f"ref_text={ref_text}")
        parts.append(text)
        return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:32]

    def path(self, text: str, lang: str, voice: str | None = None) -> Path:
        return self.dir / f"{self.key(text, lang, voice)}.wav"

    # -------------------------------------------------------------- read

    def get(self, text: str, lang: str, voice: str | None = None) -> bytes | None:
        p = self.path(text, lang, voice)
        if not p.exists():
            return None
        try:
            with wave.open(str(p)) as w:
                if w.getframerate() != self.sample_rate or w.getnchannels() != 1:
                    return None            # stale format, regenerate
                return w.readframes(w.getnframes())
        except Exception:                  # pragma: no cover - corrupt file
            return None

    # ------------------------------------------------------------- write

    def _load_model(self) -> Any:
        if self._model is not None or self._model_failed:
            return self._model
        repo = self.cfg.get("model")
        if not repo:
            self._model_failed = True
            return None
        try:
            import mlx_audio.tts.utils as tts
            log.info("loading pre-render model %s", repo)
            self._model = tts.load(repo)
        except Exception as exc:           # pragma: no cover
            log.warning("pre-render model unavailable (%s) — falling back to live TTS", exc)
            self._model_failed = True
        return self._model

    def normalise_pitch(self, pcm: bytes, voice: str | None,
                        lang: str | None = None) -> bytes:
        """Put this line on the same pitch as every other line of this voice.

        Clamped to a fifth of an octave either way: a clip whose pitch could
        not be measured properly must not be dragged somewhere absurd, and a
        clip that is already close needs no help.
        """
        from .. import pcm as P

        target = self.target_f0(voice, lang)
        if not target:
            return pcm
        got, voiced, spread = P.f0_stats(pcm, self.sample_rate)
        if got != got or got <= 0:                 # unvoiced, nothing to match
            return pcm
        # Only correct a clip whose pitch the measurement actually pins down.
        # A line with a handful of voiced frames has a median that does not
        # represent it, and shifting on that number moved clips further from
        # the voice than they started. Spread is *not* the test: real speech
        # runs an interquartile spread of 0.3-0.5 through ordinary intonation,
        # and the median across it is still a good central estimate. The
        # re-draw above is the defence for short lines; leaving them be is
        # better than guessing.
        if voiced < self.MIN_VOICED_FRAMES:
            log.debug("not correcting %.0f Hz: only %d voiced frames (spread %.2f)",
                      got, voiced, spread)
            return pcm
        ratio = min(1.18, max(0.85, target / got))
        if abs(ratio - 1.0) < 0.01:
            return pcm
        log.debug("pitch %.0f Hz -> %.0f Hz (x%.3f)", got, got * ratio, ratio)
        return P.pitch_shift(pcm, ratio, self.sample_rate)

    def render(self, text: str, lang: str, voice: str | None = None,
               attempts: int | None = None) -> bytes | None:
        """Generate and cache one line. Returns None if the pre-render model
        is unavailable, so the caller can fall back to the live voice."""
        model = self._load_model()
        if model is None:
            return None
        import numpy as np
        from mlx_audio.resample import resample_audio_array

        kwargs: dict[str, Any] = {}
        kwargs.update(self.params_for(voice))
        ref = self.reference_for(voice, lang)
        speaker = self.speaker_for(voice, lang)
        if ref:
            kwargs["ref_audio"] = ref          # cloning: anchored to a file
            ref_text = self.reference_text_for(voice, lang)
            if ref_text:
                kwargs["ref_text"] = ref_text  # only for a voice that has one
        elif speaker:
            kwargs["voice"] = speaker          # CustomVoice: fixed identity
        else:
            instruct = self.instruct_for(lang, voice)
            if instruct:
                kwargs["instruct"] = instruct  # VoiceDesign: sampled identity
        # Cloning samples a speaker per line, and some draws land a long way
        # from the voice's own pitch. Correcting a big miss by stretching is
        # audible; drawing again is not, and rendering is a build step. So:
        # take the closest of a few attempts, then correct what little is left.
        from .. import pcm as P

        target = self.target_f0(voice, lang)
        best: bytes | None = None
        best_err = float("inf")
        # A Chinese line with an address, an email or a policy number in it is
        # two languages, and one front-end cannot read both. Each run is
        # rendered by the one that can, then joined.
        pieces = segment_by_script(text, lang)
        tries = attempts if attempts is not None else (self.RETRIES if target else 1)
        for attempt in range(max(1, tries)):
            out = bytearray()
            try:
                for i, (piece, piece_lang) in enumerate(pieces):
                    kw = dict(kwargs, text=piece, lang_code=self.lang_code(piece_lang))
                    part = bytearray()
                    for seg in model.generate(**kw):
                        audio = np.asarray(getattr(seg, "audio", seg),
                                           dtype=np.float32).squeeze()
                        src = int(getattr(seg, "sample_rate", 24000) or 24000)
                        if src != self.sample_rate:
                            audio = np.asarray(
                                resample_audio_array(audio, src, self.sample_rate),
                                dtype=np.float32)
                        part += (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()
                    if len(pieces) > 1:
                        # Mid-sentence seams, so a short one: the padding is
                        # what would otherwise read as a stumble.
                        from .. import pcm as _P
                        part = bytearray(_P.trim(bytes(part), head=(i > 0),
                                                 tail=(i < len(pieces) - 1),
                                                 keep_ms=10,
                                                 sample_rate=self.sample_rate))
                        if i:
                            out += _P.silence(40, self.sample_rate)
                    out += part
            except Exception as exc:       # pragma: no cover
                log.warning("pre-render failed for %r: %s", text[:40], exc)
                return None
            cand = bytes(out)
            if not target:
                best = cand
                break
            got = P.median_f0(cand, self.sample_rate)
            err = abs(math.log(got / target)) if got == got and got > 0 else float("inf")
            if err < best_err:
                best, best_err = cand, err
            if best_err <= self.ACCEPT:    # close enough that the fix is inaudible
                break
            log.debug("re-drawing %r: %.0f Hz against %.0f Hz target (try %d)",
                      text[:32], got, target, attempt + 1)

        # Pace first, pitch second: the stretch preserves pitch, but measuring
        # the result is what the normaliser is for and it should see the audio
        # that will actually be played.
        pcm = best or b""
        rate = self.rate_for(voice, lang)
        if abs(rate - 1.0) > 0.001 and pcm:
            pcm = P.stretch(pcm, rate, self.sample_rate)
        pcm = self.normalise_pitch(pcm, voice, lang)
        p = self.path(text, lang, voice)
        tmp = p.with_suffix(".tmp")
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(self.sample_rate)
            w.writeframes(pcm)
        tmp.replace(p)                     # atomic: never serve a half-written file
        return pcm
