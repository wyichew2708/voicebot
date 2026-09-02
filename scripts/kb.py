#!/usr/bin/env python3
"""Work with the OKF knowledge bundle.

    python scripts/kb.py lint                     # the CI gate
    python scripts/kb.py status                   # what is approved, what is not
    python scripts/kb.py ask "what is the free look period" --profile rhel
    python scripts/kb.py sources                  # ingested documents

`ask` answers exactly the way a call does, under a named profile's rules, so
"why did the bot say that?" is one command rather than a live call.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voicebot import config                                    # noqa: E402
from voicebot.knowledge import policy as knowledge_policy      # noqa: E402
from voicebot.knowledge.answer import lookup                   # noqa: E402
from voicebot.knowledge.lint import check                      # noqa: E402
from voicebot.knowledge.okf import load_bundle                 # noqa: E402

BUNDLE = ROOT / "knowledge"


def cmd_lint(args) -> int:
    bundle = load_bundle(BUNDLE)
    findings = check(bundle, when=date.today())
    errors = [f for f in findings if f.level == "error"]
    warns = [f for f in findings if f.level != "error"]
    for f in errors + warns:
        print(f)
    print(f"\n  {len(bundle.pages)} pages, {len(bundle.tables)} benefit tables, "
          f"{len(errors)} errors, {len(warns)} warnings")
    return 1 if errors else 0


def cmd_status(args) -> int:
    bundle = load_bundle(BUNDLE)
    rows = sorted(bundle.pages.values(), key=lambda p: (p.type, p.id))
    width = max(len(p.id) for p in rows)
    approved = 0
    for p in rows:
        spoken = ",".join(k for k in p.spoken if k not in ("source", "note"))
        mark = "OK " if p.status == "approved" else "   "
        approved += p.status == "approved"
        print(f"  {mark} {p.id:<{width}}  {p.status:<9} {p.jurisdiction:<3} "
              f"{'spoken:' + spoken if spoken else '':<12} "
              f"{len(p.authority)} src")
    print(f"\n  {approved}/{len(rows)} approved")
    return 0


def cmd_sources(args) -> int:
    import json
    metas = sorted((BUNDLE / "raw").rglob("*.meta.json"))
    total = 0
    for m in metas:
        meta = json.loads(m.read_text())
        rel = m.relative_to(BUNDLE).as_posix().replace(".meta.json", ".md")
        total += meta.get("pages", 0)
        print(f"  {rel:<48} {meta['jurisdiction']:<3} "
              f"{meta['authority']:<15} {meta.get('pages', 0):>3}pp  "
              f"{meta['source_sha256'][:12]}")
    print(f"\n  {len(metas)} documents, {total} pages")
    return 0


def cmd_ask(args) -> int:
    cfg = config.load(args.profile) if args.profile else None
    serving = knowledge_policy.resolve(cfg)
    print(f"  profile : {args.profile or 'bundle default'}")
    print(f"  rules   : {serving.describe}\n")
    for question in args.question:
        got = lookup(question, args.lang, jurisdiction=serving.jurisdiction,
                     allow_unsourced=serving.allow_unsourced,
                     products=serving.products)
        print(f"  Q  {question}")
        if got is None:
            print("  A  (no page answers this; the call offers a colleague)\n")
            continue
        print(f"  A  {got.text}")
        print(f"     matched {got.matched!r} -> {got.citation} "
              f"[{got.status}, confidence {got.confidence}]\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("lint").set_defaults(fn=cmd_lint)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("sources").set_defaults(fn=cmd_sources)
    ask = sub.add_parser("ask")
    ask.add_argument("question", nargs="+")
    ask.add_argument("--lang", default="en")
    ask.add_argument("--profile", default=None,
                     help="config profile whose knowledge rules to apply")
    ask.set_defaults(fn=cmd_ask)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
