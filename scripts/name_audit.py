#!/usr/bin/env python3
"""Render candidate spellings of a surname and build a page to listen to.

The lexicon in `voices/names.yaml` cannot be written from a desk. Whether
"Yaw" says Yeo properly is a question about one voice saying one word, and the
only instrument that settles it is an ear. What this does is remove the tedium
around that: it renders every candidate in the real voice, transcribes each
back as a rough filter, and lays them out so a person can play them in order
and pick one.

The recogniser nominates; a person decides. Asked to judge these it returned
Hangul for "Ng" and for "Yaw", which is useful as a signal that something is
wrong and useless as a verdict.

    python scripts/name_audit.py Tan Ng Wong Yeo
    python scripts/name_audit.py --from-personas
    open voices/audit/index.html
"""
from __future__ import annotations

import argparse
import asyncio
import html
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from voicebot.config import load                        # noqa: E402
from voicebot.runtime.mlx_backend import MLXBackend     # noqa: E402

OUT = Path("voices/audit")

#: Ways the same sound gets written. Not a guess at the right answer — a spread
#: wide enough that one of them usually is, because the failures are not
#: subtle: "Yeo" is read as "ye", not as a near miss of "yaw".
def candidates(name: str) -> list[str]:
    n = name.strip()
    low = n.lower()
    out = [n]
    subs = [
        ("t", "d"), ("k", "g"), ("p", "b"),          # unaspirated stops
        ("oo", "u"), ("ee", "i"), ("eo", "aw"), ("eo", "o"),
        ("ng", "ung"), ("ng", "eng"), ("ao", "ow"), ("ai", "eye"),
        ("ua", "wah"), ("ia", "yah"), ("oh", "aw"), ("ew", "yoo"),
    ]
    for a, b in subs:
        if low.startswith(a):
            out.append(b.capitalize() + n[len(a):])
        if low.endswith(a) and len(low) > len(a):
            out.append(n[:-len(a)] + b)
        if a in low[1:-1] if len(low) > 2 else False:
            out.append(low.replace(a, b, 1).capitalize())
    # Vowel lengthening, the commonest fix: Tan -> Tahn.
    for v in "aeiou":
        if v in low:
            out.append(low.replace(v, v + "h", 1).capitalize())
    seen, uniq = set(), []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq[:8]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--from-personas", action="store_true",
                    help="audit every surname in the demo call list")
    ap.add_argument("--profile", default="mac-polyglot")
    ap.add_argument("--voice", default=None)
    ap.add_argument("--carrier", default="Mister {}.",
                    help="the sentence the name is heard inside")
    args = ap.parse_args()

    names = list(args.names)
    if args.from_personas:
        from voicebot.data import personas
        names += [p.surname for p in personas.all_policies()]
    names = sorted({n for n in names if n})
    if not names:
        ap.error("give at least one surname, or --from-personas")

    cfg = load(args.profile)["backend"]
    backend = MLXBackend(cfg)
    backend.load()
    cache = backend.prerender
    sr = cfg.get("sample_rate", 16000)
    OUT.mkdir(parents=True, exist_ok=True)

    rows: list[tuple[str, list[tuple[str, str, str]]]] = []
    for name in names:
        got: list[tuple[str, str, str]] = []
        for cand in candidates(name):
            text = args.carrier.format(cand)
            pcm = cache.render(text, "en", args.voice, 1)
            if not pcm:
                print(f"  {name}/{cand}: no pre-render model", file=sys.stderr)
                continue
            path = OUT / f"{name}-{cand}.wav".replace("/", "_")
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
                w.writeframes(pcm)
            heard = (await backend.transcribe(pcm, sr)).text.strip()
            got.append((cand, path.name, heard))
            print(f"  {name:8} {cand:10} heard {heard!r}", flush=True)
        rows.append((name, got))

    body = []
    for name, got in rows:
        body.append(f"<h2>{html.escape(name)}</h2><table>")
        body.append("<tr><th>spelling</th><th>listen</th>"
                    "<th>recogniser heard</th></tr>")
        for cand, fname, heard in got:
            mark = "" if cand == name else " (respelled)"
            body.append(
                f"<tr><td><code>{html.escape(cand)}</code>{mark}</td>"
                f'<td><audio controls preload="none" src="{html.escape(fname)}">'
                f"</audio></td><td>{html.escape(heard)}</td></tr>")
        body.append("</table>")

    (OUT / "index.html").write_text(
        "<meta charset='utf-8'><title>Surname audit</title>"
        "<style>body{font:14px system-ui;margin:2rem;max-width:60rem}"
        "table{border-collapse:collapse;margin-bottom:2rem;width:100%}"
        "td,th{border-bottom:1px solid #ddd;padding:.4rem .6rem;text-align:left}"
        "code{background:#f4f4f4;padding:.1rem .3rem}</style>"
        "<h1>Surname audit</h1><p>Play each one. Put the spelling that sounds "
        "right into <code>voices/names.yaml</code> under <code>say:</code>. If "
        "none of them do, set <code>say: ~</code> — the call will address the "
        "customer without their name rather than mispronounce it. The "
        "recogniser column is a hint, not a verdict.</p>" + "\n".join(body))
    print(f"\nWrote {OUT/'index.html'} — open it and listen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
