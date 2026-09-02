"""Voices an admin recorded, on top of the ones the profile ships.

The pre-render model clones from a reference clip, so "add a voice" is
"add a wav and measure its pitch". Nothing else about the pipeline changes:
a custom voice is an entry in the same `voices` map the config file fills,
with the same three keys the shipped voices use.

Two things are deliberate.

**They live beside the clips, not in the profile.** `config/*.yaml` is code,
reviewed and deployed; a recording someone made on a Tuesday afternoon is
data. Writing it back into the profile would put a colleague's voice in a
pull request.

**The pitch is measured, not asked for.** Cloning re-derives the speaker per
line and wanders — the shipped male voice ranged 147-193 Hz across one call
before this existed. Every voice needs a figure to normalise to, and the only
honest source for it is the recording itself.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
import wave
from pathlib import Path
from typing import Any

from . import pcm as P

log = logging.getLogger("voicebot.voices")

#: Shorter than this and the clone has too little to work from; longer and the
#: admin is reading an essay to a web page. Twenty to thirty seconds of
#: ordinary speech is what the Chatterbox authors ask for.
MIN_SECONDS = 8.0
MAX_SECONDS = 90.0
#: The clip has to be speech. A median pitch taken over a handful of voiced
#: frames does not describe a voice, and normalising every line to a number
#: measured from a cough is worse than not normalising at all.
MIN_VOICED_FRAMES = 60
#: Below this the recording is room tone, whatever the level meter showed.
MIN_PEAK = 0.02
#: A reference clip is the speaker's identity. Resampling it costs quality we
#: cannot get back, so the browser sends it at the rate the shipped clips use.
REFERENCE_RATE = 24000


class VoiceError(ValueError):
    """Something about the recording makes it unusable as a reference."""


def _slug(label: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return out[:24] or "voice"


class CustomVoices:
    def __init__(self, root: Path | str = "voices") -> None:
        self.root = Path(root)
        self.refs = self.root / "refs"
        self.store = self.root / "custom.json"

    # --------------------------------------------------------------- read

    def all(self) -> dict[str, dict[str, Any]]:
        if not self.store.exists():
            return {}
        try:
            raw = json.loads(self.store.read_text())
        except (OSError, ValueError) as exc:
            log.warning("could not read %s (%s) — no custom voices", self.store, exc)
            return {}
        return {v["id"]: v for v in raw.get("voices", []) if self._usable(v)}

    def _usable(self, entry: dict) -> bool:
        """A voice whose clip has gone is not a voice. Rendering against a
        missing reference does not fail — it silently produces a different
        speaker, which is the one outcome this whole file exists to prevent."""
        ref = entry.get("ref_audio")
        if ref and Path(ref).exists():
            return True
        log.warning("dropping voice %r: reference %r is gone", entry.get("id"), ref)
        return False

    def merge_into(self, prerender_cfg: dict[str, Any]) -> dict[str, Any]:
        """Add these to a profile's voice map, in place.

        The profile wins on a name collision: a shipped voice is part of the
        deployment and a recording must not be able to shadow it.
        """
        voices = prerender_cfg.setdefault("voices", {})
        for vid, entry in self.all().items():
            if vid in voices:
                log.warning("custom voice %r shadows a profile voice — ignored", vid)
                continue
            voices[vid] = {"label": entry["label"],
                           "ref_audio": entry["ref_audio"],
                           "target_f0": entry["target_f0"]}
        return prerender_cfg

    # -------------------------------------------------------------- write

    def add(self, label: str, audio: bytes, sample_rate: int) -> dict[str, Any]:
        """Store one recording as a reference clip. Raises VoiceError."""
        label = (label or "").strip()
        if not label:
            raise VoiceError("Give the voice a name.")
        if len(label) > 40:
            raise VoiceError("Keep the name under 40 characters.")

        seconds = len(audio) / 2 / sample_rate
        if seconds < MIN_SECONDS:
            raise VoiceError(
                f"That is {seconds:.0f} seconds. The clone needs at least "
                f"{MIN_SECONDS:.0f} — read another sentence or two.")
        if seconds > MAX_SECONDS:
            raise VoiceError(f"That is {seconds:.0f} seconds; the limit is "
                             f"{MAX_SECONDS:.0f}.")

        peak = P.peak(audio)
        if peak < MIN_PEAK:
            raise VoiceError("That recording is almost silent. Check the right "
                             "microphone is selected and try again.")

        f0, voiced, spread = P.f0_stats(audio, sample_rate)
        if f0 != f0 or f0 <= 0 or voiced < MIN_VOICED_FRAMES:
            # Say which of the three things went wrong, since they need
            # different fixes: too little speech in a long clip is pauses,
            # a quiet clip is the microphone, and neither is "try again".
            speech = voiced * 0.021 if voiced else 0.0
            raise VoiceError(
                f"Only {speech:.0f} of those {seconds:.0f} seconds were speech, "
                f"and the clone needs about {MIN_VOICED_FRAMES * 0.021:.0f}. "
                "Read the whole script straight through without pausing between "
                "sentences, in a normal speaking voice — whispering and long "
                "gaps both read as silence.")

        vid = self._free_id(_slug(label))
        self.refs.mkdir(parents=True, exist_ok=True)
        path = self.refs / f"{vid}.wav"
        tmp = path.with_suffix(".tmp")
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sample_rate)
            w.writeframes(audio)
        tmp.replace(path)                      # never a half-written reference

        entry = {
            "id": vid,
            "label": label,
            "ref_audio": str(path),
            "measured_f0": round(float(f0), 1),
            "semitones": 0.0,
            "target_f0": round(float(f0), 1),
            "seconds": round(seconds, 1),
            "spread": round(float(spread), 2),
            "created": int(time.time()),
        }
        self._write(list(self.all().values()) + [entry])
        log.info("added voice %r: %.1f s, %.0f Hz over %d voiced frames",
                 vid, seconds, f0, voiced)
        return entry

    def set_pitch(self, vid: str, semitones: float) -> dict[str, Any]:
        """Shift the whole voice up or down, in semitones from its own pitch.

        Every rendered line is normalised to `target_f0`, so this moves the
        voice as a body rather than colouring one line — which is the only way
        to change tone without the speaker seeming to change mid-call.
        """
        semitones = max(-6.0, min(6.0, float(semitones)))
        rows = list(self.all().values())
        for entry in rows:
            if entry["id"] == vid:
                entry["semitones"] = round(semitones, 2)
                entry["target_f0"] = round(entry["measured_f0"] * 2 ** (semitones / 12), 1)
                self._write(rows)
                return entry
        raise VoiceError(f"No such voice: {vid}")

    def remove(self, vid: str) -> bool:
        rows = list(self.all().values())
        keep = [e for e in rows if e["id"] != vid]
        if len(keep) == len(rows):
            return False
        for e in rows:
            if e["id"] == vid:
                Path(e["ref_audio"]).unlink(missing_ok=True)
        self._write(keep)
        return True

    # ------------------------------------------------------------- detail

    def _free_id(self, base: str) -> str:
        taken = set(self.all())
        if base not in taken:
            return base
        return f"{base}-{uuid.uuid4().hex[:4]}"

    def _write(self, rows: list[dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.store.with_suffix(".tmp")
        tmp.write_text(json.dumps({"voices": rows}, indent=2, ensure_ascii=False))
        tmp.replace(self.store)
