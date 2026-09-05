#!/usr/bin/env python3
"""Run the Singapore insurance sentence set through candidate TTS models.

Each candidate is a sidecar (scripts/tts_sidecar.py --engine ...) on its own
port, or an mlx-audio repo rendered in-process on a Mac. Every model gets the
same lines, the same reference clips and the same measurement, and the result
is a page to listen to plus a table of the numbers an ear cannot judge.

    # two sidecars up on the GPU box
    python scripts/tts_sidecar.py --engine chatterbox --port 8802 &
    python scripts/tts_sidecar.py --engine cosyvoice3 --port 8803 &
    python scripts/tts_bench.py chatterbox=http://127.0.0.1:8802 cosyvoice3=http://127.0.0.1:8803

    # with an ASR round-trip for character error rate
    python scripts/tts_bench.py ... --asr-url http://127.0.0.1:8801 --asr-model MERaLiON/MERaLiON-3-3B-ASR

    # the model's own number handling, no spoken layer in front of it
    python scripts/tts_bench.py ... --raw --groups money policy address phone date

    # on a Mac, an mlx-audio build alongside the shipped one
    python scripts/tts_bench.py --mlx mlx-community/chatterbox-multilingual-v3 \\
                                --mlx mlx-community/Fun-CosyVoice3-0.5B-2512-8bit

Output lands in voices/bench/<timestamp>/ with voices/bench/latest pointing at
it. Open index.html. See docs/tts-models.md for which models to try and why.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voicebot import tts_bench as B                        # noqa: E402

OUT = Path("voices/bench")


def _refs(args) -> tuple[dict[str, str], dict[str, str]]:
    refs = dict(B.DEFAULT_REFS)
    if args.ref_en:
        refs["en"] = args.ref_en
    if args.ref_zh:
        refs["zh"] = args.ref_zh
    texts = {}
    for lang, given in (("en", args.ref_text_en), ("zh", args.ref_text_zh)):
        if given:
            texts[lang] = given
        else:
            txt = Path(refs[lang]).with_suffix(".txt")
            if txt.exists():
                texts[lang] = txt.read_text(encoding="utf-8").strip()
    return refs, texts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n\n", 1)[1])
    ap.add_argument("targets", nargs="*", metavar="NAME=URL",
                    help="a running sidecar, e.g. cosyvoice3=http://127.0.0.1:8803")
    ap.add_argument("--mlx", action="append", default=[], metavar="REPO",
                    help="an mlx-audio model to render in-process (Mac); repeatable")
    ap.add_argument("--mlx-voice", default=None, metavar="NAME",
                    help="preset speaker for a non-cloning --mlx model (Kokoro: am_michael, "
                         "VibeVoice: Carter); the reference clips are then not used")
    ap.add_argument("--mlx-lang-codes", default=None, metavar="en=a,zh=z",
                    help="what the --mlx model calls each language (Kokoro needs en=a,zh=z)")
    ap.add_argument("--f5-mlx", action="store_true",
                    help="F5-TTS through the f5-tts-mlx package, in-process (Mac)")
    ap.add_argument("--langs", nargs="*", default=["en", "zh"])
    ap.add_argument("--groups", nargs="*", default=None,
                    help="only these line groups (script, singlish, money, policy, ...)")
    ap.add_argument("--limit", type=int, default=None, help="first N lines only")
    ap.add_argument("--raw", action="store_true",
                    help="send the written line whole — the model's own normalisation")
    ap.add_argument("--ref-en", default=None, help="English reference clip to clone")
    ap.add_argument("--ref-zh", default=None, help="Mandarin reference clip to clone")
    ap.add_argument("--ref-text-en", default=None, help="what the English clip says")
    ap.add_argument("--ref-text-zh", default=None, help="what the Mandarin clip says")
    ap.add_argument("--asr-url", default=None,
                    help="an OpenAI-compatible transcription endpoint (vLLM) for CER")
    ap.add_argument("--asr-model", default="MERaLiON/MERaLiON-3-3B-ASR")
    ap.add_argument("--sample-rate", type=int, default=16000)
    ap.add_argument("--out", default=None, help=f"default {OUT}/<timestamp>")
    args = ap.parse_args()

    if not args.targets and not args.mlx and not args.f5_mlx:
        ap.error("give at least one NAME=URL sidecar, --mlx REPO, or --f5-mlx")
    lang_codes = None
    if args.mlx_lang_codes:
        lang_codes = dict(pair.split("=", 1) for pair in args.mlx_lang_codes.split(","))
    refs, texts = _refs(args)
    lines = B.sentences(tuple(args.langs), tuple(args.groups) if args.groups else None)
    if args.limit:
        lines = lines[:args.limit]
    if not lines:
        ap.error("no lines selected")

    out = Path(args.out) if args.out else OUT / time.strftime("%Y%m%d-%H%M%S")
    targets: list = []
    for spec in args.targets:
        name, _, url = spec.partition("=")
        if not url:
            ap.error(f"target {spec!r} is not NAME=URL")
        targets.append(B.SidecarTarget(name, url, args.sample_rate, refs, texts, raw=args.raw))
    if args.raw and (args.mlx or args.f5_mlx):
        ap.error("--raw is for sidecars; the in-process path always uses the spoken layer")
    for repo in args.mlx:
        targets.append(B.MLXTarget(repo, out / ".mlx-cache", args.sample_rate, refs, texts,
                                   speaker=args.mlx_voice, lang_codes=lang_codes))
    if args.f5_mlx:
        targets.append(B.F5MLXTarget(args.sample_rate, refs, texts))

    transcribe = None
    if args.asr_url:
        from voicebot.runtime.cuda_backend import CUDABackend
        asr = CUDABackend({"sample_rate": args.sample_rate,
                           "asr": {"base_url": args.asr_url, "model": args.asr_model},
                           "tts": {"prerender": {}}})

        async def transcribe(pcm: bytes, sr: int) -> str:      # noqa: E306
            return (await asr.transcribe(pcm, sr)).text

    print(f"{len(lines)} lines × {len(targets)} model(s)"
          f"{' — raw text, no spoken layer' if args.raw else ''} -> {out}")
    if not texts:
        print("  no reference transcripts (voices/refs/*.txt): CosyVoice 3 and F5 clone "
              "better with one, Fish needs one")
    renderings, summary = asyncio.run(B.run(targets, lines, out, transcribe, print))

    latest = OUT / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(out.resolve())
    except OSError:
        pass
    print()
    print(B.markdown_table(summary))
    print(f"\nWrote {out/'index.html'} — open it and listen.")
    failed = sum(1 for r in renderings if not r.ok)
    return 1 if failed == len(renderings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
