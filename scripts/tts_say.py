#!/usr/bin/env python3
"""Say one line in one candidate TTS model, from the command line.

The same switch the console has, without the console: pick a model from
config/tts-models.yaml, give it a line, get a wav and a latency.

    python scripts/tts_say.py --list
    python scripts/tts_say.py --model cosyvoice3 --text "Good afternoon Mr Tan."
    python scripts/tts_say.py --model kokoro --text "您的保单在二月十日到期。" --play
    python scripts/tts_say.py --model vibevoice --text "Hi there [chuckle], got a minute?" --voice male

Runs under the profile's backend — the Mac profile loads mlx-audio models in
this process; the RHEL profile talks to the sidecars named in
backend.tts.sidecars or VOICEBOT_TTS_SIDECARS. Only the TTS side is brought
up: no ASR, no LLM.
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voicebot import config                                   # noqa: E402
from voicebot.voices import CustomVoices                      # noqa: E402

OUT = Path("voices/bench/say")


def _backend(cfg: dict):
    """The profile's backend with only what saying a line needs."""
    profile = cfg.get("profile", "mock")
    backend_cfg = dict(cfg.get("backend", {}))
    backend_cfg["sample_rate"] = cfg.get("audio", {}).get("sample_rate", 16000)
    CustomVoices().merge_into(backend_cfg.setdefault("tts", {}).setdefault("prerender", {}))
    if profile == "mlx":
        from voicebot.runtime.mlx_backend import MLXBackend
        return MLXBackend(backend_cfg)            # no .load(): ASR/LLM stay cold
    if profile == "cuda":
        from voicebot.runtime.cuda_backend import CUDABackend
        return CUDABackend(backend_cfg)
    from voicebot.runtime.mock import MockBackend
    return MockBackend()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n\n", 1)[1])
    ap.add_argument("--profile", default="mac-polyglot")
    ap.add_argument("--list", action="store_true", help="the models and whether each runs here")
    ap.add_argument("--model", default=None, help="id from config/tts-models.yaml")
    ap.add_argument("--text", default=None)
    ap.add_argument("--lang", default=None, help="en or zh (default: guessed from the text)")
    ap.add_argument("--voice", default=None, help="a voice from the profile (default: its default)")
    ap.add_argument("--out", default=None, help=f"wav path (default {OUT}/<model>.wav)")
    ap.add_argument("--play", action="store_true", help="play it (afplay / aplay / ffplay)")
    args = ap.parse_args()

    cfg = config.load(args.profile)
    backend = _backend(cfg)
    lab = getattr(backend, "lab", None)
    if lab is None:
        sys.exit("this backend has no TTS lab")

    if args.list or not args.model:
        rows = lab.models()
        width = max(len(r["id"]) for r in rows) if rows else 8
        print(f"profile {args.profile} ({lab.platform}); models in config/tts-models.yaml:\n")
        for r in rows:
            state = "ready" if r["available"] else f"not runnable: {r['reason']}"
            langs = " ".join(r["languages"]) or "any"
            print(f"  {r['id']:{width}}  {r['label']:26}  {langs:22}  {state}")
        if not args.model:
            print("\n  --model ID --text '...' to hear one")
        return 0

    if not args.text:
        ap.error("--text is required with --model")
    lang = args.lang or ("zh" if any("一" <= c <= "鿿" for c in args.text) else "en")
    voice = args.voice or backend.prerender.default_voice() if hasattr(backend, "prerender") \
        else args.voice
    print(f"{args.model}: {lang} · voice {voice or '-'} · {args.text[:60]!r}")
    t0 = time.perf_counter()
    try:
        sp = asyncio.run(lab.render(args.text, lang, voice, args.model))
    except KeyError:
        sys.exit(f"unknown model {args.model!r}; --list shows them")
    except Exception as exc:
        sys.exit(f"{type(exc).__name__}: {exc}")
    wall = int((time.perf_counter() - t0) * 1000)
    out = Path(args.out) if args.out else OUT / f"{args.model}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sp.sample_rate)
        w.writeframes(sp.pcm)
    seconds = len(sp.pcm) / 2 / sp.sample_rate
    print(f"  {sp.latency_ms} ms to synthesise ({wall} ms wall, first use included) · "
          f"{seconds:.1f} s of audio · RTF {sp.latency_ms / 1000 / seconds if seconds else 0:.2f}")
    print(f"  -> {out}")
    if args.play:
        for player in (("afplay",), ("aplay", "-q"), ("ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet")):
            if shutil.which(player[0]):
                subprocess.run([*player, str(out)], check=False)
                break
        else:
            print("  no audio player found (afplay/aplay/ffplay) — open the wav")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
