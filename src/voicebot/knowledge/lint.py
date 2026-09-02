"""The linter. This is what makes the bundle trustworthy rather than tidy.

A wiki nobody checks becomes a confident liar within a quarter, and it is more
dangerous than no wiki because it is believed. Every rule below exists to make
one specific failure impossible to merge:

  refs        a claim with no citation cannot reach `approved`
  locators    a citation that points at a page the document does not have
  jurisdiction a Singapore page citing a Malaysian source -- the likeliest way
              this particular corpus produces a wrong answer to a real customer
  figures     a dollar amount typed into prose instead of read from a table
  links       a graph edge that goes nowhere
  ttl         a promotion with no expiry, which outlives the campaign
  spoken      wording the bot may say whose source does not resolve
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from .okf import AUTHORITY_ORDER, LIFECYCLES, STATUSES, TRANSCLUDE, Bundle

REQUIRED = ("okf_version", "id", "title", "type", "status", "jurisdiction",
            "underwriter", "aliases", "authority", "confidence")

#: A citation: a path under raw/ with a page locator. Without the locator a
#: reviewer cannot check the claim, and an unverifiable claim is not evidence.
REF = re.compile(r"\(((?:\.\./)*raw/[^)\s#]+\.md)#p(\d+)\)")

#: Money in prose. Every figure the bot may speak has to come from a benefit
#: table, so that changing a limit is a CSV edit with a diff, not a hunt
#: through prose for the four places it was written out.
MONEY = re.compile(r"(?<![\w])(?:S?\$|SGD\s*)\s?\d[\d,]*(?:\.\d+)?", re.I)

WIKI_LINK = re.compile(r"\[[^\]]*\]\(((?!https?:|mailto:)[^)]+\.md)\)")
CHANNEL_BLOCK = re.compile(r"<!--\s*okf:channel-variant\s*-->.*?"
                           r"<!--\s*/okf:channel-variant\s*-->", re.S)
BRANDS = re.compile(r"\b(Tiq|Etiqa)\b")


@dataclass(frozen=True)
class Finding:
    page: str
    rule: str
    detail: str
    level: str = "error"

    def __str__(self) -> str:
        return f"{self.level.upper():<7} {self.page:<40} {self.rule}: {self.detail}"


def _raw_meta(root: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for meta_path in (root / "raw").rglob("*.meta.json"):
        rel = meta_path.relative_to(root).as_posix().replace(".meta.json", ".md")
        out[rel] = json.loads(meta_path.read_text())
    return out


def _declared_sources(root: Path) -> dict[str, dict]:
    path = root / "sources.yaml"
    if not path.exists():
        return {}
    manifest = yaml.safe_load(path.read_text()) or {}
    return {s["dest"]: s for s in manifest.get("sources", [])}


def check(bundle: Bundle, when: date | None = None) -> list[Finding]:
    when = when or date.today()
    root = bundle.root
    raw = _raw_meta(root)
    declared = _declared_sources(root)
    found: list[Finding] = []

    def bad(page, rule, detail, level="error"):
        found.append(Finding(page, rule, detail, level))

    index = bundle.get("index")
    indexed = set(WIKI_LINK.findall(index.body)) if index else set()

    for page in sorted(bundle.pages.values(), key=lambda p: p.id):
        pid, meta, body = page.id, page.meta, page.body

        # -- schema ------------------------------------------------------
        for key in REQUIRED:
            if key not in meta:
                bad(pid, "schema", f"missing frontmatter key {key!r}")
        if page.status not in STATUSES:
            bad(pid, "schema", f"status {page.status!r} not one of {STATUSES}")
        if page.lifecycle and page.lifecycle not in LIFECYCLES:
            bad(pid, "schema", f"lifecycle {page.lifecycle!r} unknown")
        if page.jurisdiction not in ("SG", "MY"):
            bad(pid, "schema", f"jurisdiction {page.jurisdiction!r} unknown")

        # -- authority ---------------------------------------------------
        for src in page.authority:
            if src not in raw:
                bad(pid, "authority", f"{src} is not an ingested source")
                continue
            entry = declared.get(src, {})
            if entry.get("authority") not in AUTHORITY_ORDER:
                bad(pid, "authority", f"{src} has no declared authority rank")
            # The rule this whole corpus needs.
            if entry.get("jurisdiction") and entry["jurisdiction"] != page.jurisdiction:
                bad(pid, "jurisdiction",
                    f"a {page.jurisdiction} page cites {src}, which is "
                    f"{entry['jurisdiction']}. Different underwriter, "
                    "different wording, different regulator.")

        if page.status == "approved" and not page.authority:
            bad(pid, "refs", "approved with no authority source")
        if page.status == "approved" and not REF.search(body):
            bad(pid, "refs", "approved with no inline citation in the body")
        if page.status == "approved" and page.review_due is None:
            bad(pid, "staleness", "approved with no review_due")
        if page.is_stale_on(when):
            bad(pid, "staleness",
                f"review_due {page.review_due} has passed; demoted from "
                "wiki-first retrieval until reviewed", "warn")

        # -- citations resolve -------------------------------------------
        for rel, locator in REF.findall(body):
            target = rel.lstrip("./")
            if target not in raw:
                bad(pid, "refs", f"citation to unknown source {target}")
            elif int(locator) > raw[target].get("pages", 0):
                bad(pid, "refs",
                    f"{target} has {raw[target]['pages']} pages, cited p{locator}")

        # -- figures ------------------------------------------------------
        prose = TRANSCLUDE.sub("", body)
        for hit in MONEY.findall(prose):
            bad(pid, "figures",
                f"{hit!r} written into prose; put it in benefit-tables/ and "
                "transclude it")

        # -- links --------------------------------------------------------
        for link in WIKI_LINK.findall(body):
            target = (page.path.parent / link).resolve()
            if not target.exists():
                bad(pid, "links", f"broken link to {link}")

        # -- spoken wording ------------------------------------------------
        spoken = page.spoken
        if spoken:
            langs = [k for k in spoken if k not in ("source", "note")]
            if not langs:
                bad(pid, "spoken", "spoken block with no language")
            srcs = spoken.get("source")
            srcs = [srcs] if isinstance(srcs, str) else list(srcs or [])
            if page.status == "approved" and not srcs:
                bad(pid, "spoken", "approved spoken wording with no source")
            for src in srcs:
                path, _, loc = src.partition("#p")
                if path not in raw:
                    bad(pid, "spoken", f"spoken source {path} not ingested")
                elif loc and int(loc) > raw[path].get("pages", 0):
                    bad(pid, "spoken", f"spoken source {src} past end of document")
            for lang in langs:
                said = str(spoken[lang])
                try:
                    bundle.resolve(said)
                except Exception as exc:
                    bad(pid, "figures", f"{lang}: {exc}")
                if MONEY.search(TRANSCLUDE.sub("", said)):
                    bad(pid, "figures",
                        f"{lang}: a figure is written into spoken wording")

        # -- promotions ----------------------------------------------------
        if page.type == "promotion" and not meta.get("effective_to"):
            bad(pid, "ttl", "a promotion with no effective_to outlives its campaign")

        # -- brand containment ---------------------------------------------
        if page.type == "product":
            stripped = CHANNEL_BLOCK.sub("", body)
            excuse = meta.get("brand_prose_allowed")
            if BRANDS.search(stripped) and not excuse:
                bad(pid, "channel",
                    "a brand name appears outside the channel-variant block; "
                    "coverage is one product, brand is a channel attribute",
                    "warn")
            elif excuse and not BRANDS.search(stripped):
                # An excuse nobody needs is an excuse nobody removed. Left in
                # place it silently disarms the rule for the next editor.
                bad(pid, "channel",
                    "brand_prose_allowed is set but no brand name appears; "
                    "drop it", "warn")

        # -- index ---------------------------------------------------------
        if pid != "index":
            rel = pid + ".md"
            if not any(link.endswith(rel) for link in indexed):
                bad(pid, "index", "not linked from wiki/index.md", "warn")

        if "Also, separately" in body:
            bad(pid, "shape", "one page, one concept -- split it", "warn")

    return found
