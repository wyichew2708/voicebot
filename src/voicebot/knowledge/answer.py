"""Deterministic wiki-first answering.

The whole point of this module is what it does *not* do. It selects a page by
frontmatter filter and alias match -- ordinary code, no model -- and returns
that page's pre-approved `spoken` wording verbatim. It never composes a
sentence, never summarises a clause, and never falls back to "close enough".

A miss returns None, and the engine's existing behaviour takes over: offer a
colleague. On a renewal call for an insurance product, an unanswered question
costs a callback, and a wrong answer costs a misrepresentation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

from .okf import Bundle, Page, load_bundle

#: Matching a Latin alias needs a word boundary: "pa" inside "company" and
#: "excess" inside "excessive" both produced answers to questions nobody asked.
#: CJK has no such boundary, so those aliases match as substrings.
_CJK = re.compile(r"[㐀-鿿豈-﫿]")


@dataclass(frozen=True)
class Answer:
    text: str                       #: exactly what to say; already resolved
    page_id: str
    sources: tuple[str, ...]
    status: str                     #: "approved" or "draft"
    confidence: str
    matched: str                    #: the alias that selected the page

    @property
    def sourced(self) -> bool:
        return self.status == "approved" and bool(self.sources)

    @property
    def citation(self) -> str:
        """What to write in the call record. A coverage answer that cannot be
        traced afterwards is not much better than one that was guessed."""
        if self.sources:
            return f"{self.page_id} <- {self.sources[0]}"
        return f"{self.page_id} (unsourced draft)"


def _alias_hit(alias: str, text: str, low: str) -> bool:
    alias = alias.strip()
    if not alias:
        return False
    if _CJK.search(alias):
        return alias in text
    return re.search(rf"(?<![a-z0-9]){re.escape(alias.lower())}(?![a-z0-9])",
                     low) is not None


def _applies_to(page: Page, products: tuple[str, ...]) -> bool:
    """Cross-product contamination is a real failure mode: "does my car policy
    cover my dog" is in the adversarial suite for a reason. A product page
    answers only for its own product, and a concept answers only for the
    products it declares."""
    if not products:
        return True
    if page.type == "product":
        return page.id in products or any(page.id.startswith(p + "/")
                                          for p in products)
    scope = tuple(str(s) for s in page.meta.get("applies_to", []) or ())
    if not scope:
        return True                 # genuinely product-independent
    return any(s in products or s == "*" for s in scope)


def candidates(bundle: Bundle, *, when: date, jurisdiction: str,
               allow_unsourced: bool,
               products: tuple[str, ...] = ()) -> list[Page]:
    """The frontmatter pre-read filter.

    This is the cheap step, and it is the one that keeps a Malaysian home FAQ
    from ever answering a Singapore caller.
    """
    return [p for p in bundle.pages.values()
            if p.jurisdiction == jurisdiction
            and p.answerable_on(when, allow_unsourced)
            and _applies_to(p, products)]


def lookup(text: str, lang: str, *, bundle: Bundle | None = None,
           when: date | None = None, jurisdiction: str = "SG",
           allow_unsourced: bool = False,
           products: tuple[str, ...] = ()) -> Answer | None:
    """The single entry point the call engine uses."""
    bundle = bundle if bundle is not None else default_bundle()
    when = when or date.today()
    low = text.lower()

    speakable = [p for p in candidates(bundle, when=when, jurisdiction=jurisdiction,
                                       allow_unsourced=allow_unsourced,
                                       products=products)
                 if p.spoken.get(lang) or p.spoken.get("en")]

    def match(named: bool) -> "tuple[int, Page, str] | None":
        # Longest alias wins within a pass. "home contents" must beat
        # "contents", or the general page answers the specific question.
        best: tuple[int, Page, str] | None = None
        for page in speakable:
            for alias in (page.aliases if named else page.fallback_aliases):
                if _alias_hit(alias, text, low) and (best is None or len(alias) > best[0]):
                    best = (len(alias), page, alias)
        return best

    # Names first, everywhere, before any page's "what does it cover" phrasing
    # is considered. A specific question must not be shadowed by a longer
    # general one.
    best = match(named=True) or match(named=False)
    if best is None:
        return None

    _, page, alias = best
    spoken = page.spoken
    said = spoken.get(lang) or spoken.get("en")
    sources = spoken.get("source")
    sources = (sources,) if isinstance(sources, str) else tuple(sources or ())
    return Answer(text=bundle.resolve(str(said)), page_id=page.id,
                  sources=sources, status=page.status,
                  confidence=str(page.meta.get("confidence", "unknown")),
                  matched=alias)


def spoken_lines(bundle: Bundle | None = None, *, langs: tuple[str, ...] = ("en", "zh"),
                 allow_unsourced: bool = True, jurisdiction: str = "SG",
                 products: tuple[str, ...] = (),
                 when: date | None = None) -> list[tuple[str, str]]:
    """Every (text, lang) this deployment could actually say.

    The warm-up renders these like any other fixed line. A coverage answer
    that has to be synthesised live costs two to four seconds in the middle of
    a turn, which is exactly when a caller says "hello?".

    The filter is the same one `lookup` uses, and for the same reason in
    reverse: warming a page the deployment can never reach spends GPU time on
    audio that will not play. Scoping a renewal bot to home insurance and then
    rendering the travel clauses in seven voices is eighty-four wasted files.
    """
    bundle = bundle if bundle is not None else default_bundle()
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for page in candidates(bundle, when=when or date.today(),
                           jurisdiction=jurisdiction,
                           allow_unsourced=allow_unsourced, products=products):
        for lang in langs:
            said = page.spoken.get(lang)
            if not said:
                continue
            job = (bundle.resolve(str(said)), lang)
            if job not in seen:
                seen.add(job)
                out.append(job)
    return out


@lru_cache(maxsize=4)
def _cached(root: str | None) -> Bundle:
    return load_bundle(Path(root) if root else None)


def default_bundle(root: str | None = None) -> Bundle:
    """The repo's bundle, parsed once.

    Cached because the call engine asks per turn and parsing a few hundred
    pages of YAML in front of a caller is not free.
    """
    return _cached(root)


def reload() -> None:
    """Drop the cache. The console calls this when the bundle changes."""
    _cached.cache_clear()
