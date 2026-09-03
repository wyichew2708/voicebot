#!/usr/bin/env python3
"""Ingest source documents into the OKF bundle's immutable `raw/` tree.

`raw/` is the evidence. Nothing in it is ever edited by hand or by a model:
it is a dated, content-addressed snapshot of a document Etiqa published, and
every claim in the compiled wiki cites a path and a page number in here.

Text is extracted with page markers intact, because a citation without a
locator cannot be checked, and a claim that cannot be checked cannot be
approved.

Usage:
    python scripts/kb_ingest.py --manifest knowledge/sources.yaml
    python scripts/kb_ingest.py --check     # re-hash, report drift, write nothing
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "knowledge"
MANIFEST = BUNDLE / "sources.yaml"

# Ligatures and layout artefacts that survive PDF extraction and then show up
# inside a quoted clause, where they are indistinguishable from a typo in the
# policy itself.
_FIXES = {
    "ﬁ": "fi", "ﬂ": "fl", "‘": "'", "’": "'",
    "“": '"', "”": '"', "–": "-", "—": "-",
    " ": " ", "﻿": "",
}


def _clean(text: str) -> str:
    for bad, good in _FIXES.items():
        text = text.replace(bad, good)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def extract(pdf: Path) -> tuple[str, int]:
    """Markdown text with an HTML page marker before each page.

    The marker is the locator. `raw/wordings/x.md#p12` is a checkable
    reference; "somewhere in the travel wording" is not.
    """
    try:
        from pypdf import PdfReader
    except ImportError:                                   # pragma: no cover
        sys.exit("pypdf is needed to ingest PDFs:  uv pip install pypdf")
    reader = PdfReader(str(pdf))
    parts = []
    for n, page in enumerate(reader.pages, 1):
        body = _clean(page.extract_text() or "")
        parts.append(f"<!-- page {n} -->\n{body}")
    return "\n\n".join(parts), len(reader.pages)


def ingest_one(entry: dict, check: bool) -> dict:
    src = Path(entry["file"]).expanduser()
    dest = BUNDLE / entry["dest"]
    result = {"dest": entry["dest"], "status": "", "sha256": "", "pages": 0}

    if not src.exists():
        result["status"] = "MISSING SOURCE"
        return result

    digest = sha256(src)
    result["sha256"] = digest
    meta_path = dest.with_suffix(".meta.json")

    if meta_path.exists():
        old = json.loads(meta_path.read_text())
        if old.get("source_sha256") == digest:
            result["status"] = "unchanged"
            result["pages"] = old.get("pages", 0)
            return result
        result["status"] = "CHANGED"
    else:
        result["status"] = "new"

    if check:
        return result

    text, pages = extract(src)
    result["pages"] = pages
    dest.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"<!-- OKF raw source. Immutable: do not edit, recompile instead. -->\n"
        f"<!-- title: {entry['title']} -->\n"
        f"<!-- publisher: {entry['publisher']} -->\n"
        f"<!-- jurisdiction: {entry['jurisdiction']} -->\n"
        f"<!-- authority: {entry['authority']} -->\n"
        f"<!-- url: {entry.get('url') or 'n/a'} -->\n"
        f"<!-- source_sha256: {digest} -->\n\n"
    )
    dest.write_text(header + text + "\n")
    meta_path.write_text(json.dumps({
        "title": entry["title"],
        "publisher": entry["publisher"],
        "jurisdiction": entry["jurisdiction"],
        "authority": entry["authority"],
        "document_version": entry.get("document_version"),
        "effective_from": str(entry.get("effective_from") or ""),
        "effective_to": str(entry.get("effective_to") or ""),
        "source_filename": src.name,
        "url": entry.get("url"),
        "source_sha256": digest,
        "pages": pages,
        "ingested_on": str(date.today()),
    }, indent=2) + "\n")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--check", action="store_true",
                    help="re-hash sources and report drift without writing")
    args = ap.parse_args()

    manifest = yaml.safe_load(Path(args.manifest).read_text())
    rows = [ingest_one(e, args.check) for e in manifest["sources"]]

    width = max(len(r["dest"]) for r in rows)
    drift = 0
    for r in rows:
        print(f"  {r['dest']:<{width}}  {r['status']:<14} "
              f"{r['pages'] or '':>3}  {r['sha256'][:12]}")
        if r["status"] in ("CHANGED", "MISSING SOURCE"):
            drift += 1
    print(f"\n  {len(rows)} sources, {drift} needing attention")
    return 1 if (args.check and drift) else 0


if __name__ == "__main__":
    raise SystemExit(main())
