"""Reading an OKF bundle: pages, frontmatter, benefit tables.

Path is identity. `knowledge/wiki/product/general/travel.md` is the page
`product/general/travel`, and that ID never moves when marketing renames the
product -- the old name becomes an alias instead.

Frontmatter is the pre-read filter, not decoration. Selecting by jurisdiction,
status and effective window eliminates almost every page before a body is
read, which is what makes wiki-first retrieval cheap enough to sit in front of
a caller.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

#: Conflict-resolution order from the design doc. Higher wins the page; the
#: loser becomes an entry in `conflicts/`, raised against the *source*, not
#: against the wiki.
AUTHORITY_ORDER = (
    "policy_wording", "product_summary", "rate_table", "faq",
    "web_etiqa", "web_tiq", "promotion", "marketing",
)

STATUSES = ("draft", "in_review", "approved", "deprecated")
LIFECYCLES = ("on_sale", "closed_to_new_business", "withdrawn")

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)

#: `{{table:<table>:<row>:<column>}}` -- a figure fetched from a benefit table.
#: Figures are never written into prose, so that every number the bot speaks
#: traces to a row someone can point at.
TRANSCLUDE = re.compile(r"\{\{table:([a-z0-9\-]+):([a-z0-9_\-]+):([a-z0-9_\-]+)\}\}")


class BundleError(RuntimeError):
    pass


@dataclass(frozen=True)
class Page:
    id: str
    path: Path
    meta: dict[str, Any]
    body: str

    # -- frontmatter accessors, all total ---------------------------------
    @property
    def title(self) -> str:
        return str(self.meta.get("title", self.id))

    @property
    def type(self) -> str:
        return str(self.meta.get("type", ""))

    @property
    def status(self) -> str:
        return str(self.meta.get("status", "draft"))

    @property
    def jurisdiction(self) -> str:
        return str(self.meta.get("jurisdiction", ""))

    @property
    def lifecycle(self) -> str:
        return str(self.meta.get("lifecycle", ""))

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple(str(a) for a in self.meta.get("aliases", []) or ())

    @property
    def authority(self) -> tuple[str, ...]:
        return tuple(str(a) for a in self.meta.get("authority", []) or ())

    @property
    def spoken(self) -> dict[str, Any]:
        got = self.meta.get("spoken") or {}
        return dict(got) if isinstance(got, dict) else {}

    @property
    def review_due(self) -> date | None:
        got = self.meta.get("review_due")
        return got if isinstance(got, date) else None

    def in_force_on(self, when: date) -> bool:
        start = self.meta.get("effective_from")
        end = self.meta.get("effective_to")
        if isinstance(start, date) and when < start:
            return False
        if isinstance(end, date) and when > end:
            return False
        return True

    def is_stale_on(self, when: date) -> bool:
        """Overdue for review. Such a page is demoted out of wiki-first
        retrieval rather than trusted: a stale wiki is worse than no wiki,
        because it is believed."""
        due = self.review_due
        return due is not None and when > due

    @property
    def answers_after_expiry(self) -> bool:
        """A page that keeps answering once its window has closed.

        Only for wording that is *about* the expiry -- "that campaign ended in
        December". An expired promotion the bot cannot talk about at all is
        worse than one it can decline cleanly, because the caller asked by
        name and the model would otherwise improvise around a promo code it
        half-remembers.
        """
        return bool(self.meta.get("answer_after_expiry"))

    def answerable_on(self, when: date, allow_unsourced: bool) -> bool:
        in_window = self.in_force_on(when) or self.answers_after_expiry
        if self.status == "approved":
            return in_window and not self.is_stale_on(when)
        if self.status == "draft" and allow_unsourced:
            return in_window
        return False


@dataclass
class Bundle:
    root: Path
    manifest: dict[str, Any]
    pages: dict[str, Page] = field(default_factory=dict)
    tables: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)

    # -- lookups ----------------------------------------------------------
    def get(self, page_id: str) -> Page | None:
        return self.pages.get(page_id)

    def of_type(self, *types: str) -> list[Page]:
        return [p for p in self.pages.values() if p.type in types]

    def figure(self, table: str, row: str, column: str) -> str | None:
        return self.tables.get(table, {}).get(row, {}).get(column)

    def resolve(self, text: str) -> str:
        """Replace every `{{table:...}}` with its row value.

        An unresolvable figure raises rather than rendering a gap: a spoken
        line with a hole in it is worse than one that never plays.
        """
        def sub(m: "re.Match[str]") -> str:
            got = self.figure(m.group(1), m.group(2), m.group(3))
            if got is None:
                raise BundleError(f"no such figure: {m.group(0)}")
            return got
        return TRANSCLUDE.sub(sub, text)


def _parse(path: Path, root: Path) -> Page:
    raw = path.read_text()
    m = _FRONTMATTER.match(raw)
    if m is None:
        raise BundleError(f"{path}: no YAML frontmatter")
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:                       # pragma: no cover
        raise BundleError(f"{path}: bad frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise BundleError(f"{path}: frontmatter is not a mapping")

    from_path = path.relative_to(root / "wiki").with_suffix("").as_posix()
    declared = str(meta.get("id", from_path))
    if declared != from_path:
        raise BundleError(
            f"{path}: id {declared!r} does not match its path {from_path!r}. "
            "Path is identity in OKF; move the file or fix the id.")
    return Page(id=from_path, path=path, meta=meta, body=m.group(2))


def _load_tables(root: Path) -> dict[str, dict[str, dict[str, str]]]:
    """Benefit tables, keyed table -> row -> column.

    The `benefit` column is the row key. Every other column is a plan tier or
    a variant. Numbers live here and nowhere else.
    """
    tables: dict[str, dict[str, dict[str, str]]] = {}
    folder = root / "benefit-tables"
    for csv_path in sorted(folder.glob("*.csv")):
        rows: dict[str, dict[str, str]] = {}
        with csv_path.open(newline="") as fh:
            for row in csv.DictReader(fh):
                key = (row.get("benefit") or "").strip()
                if not key:
                    continue
                rows[key] = {k: (v or "").strip()
                             for k, v in row.items() if k and k != "benefit"}
        tables[csv_path.stem] = rows
    return tables


def load_bundle(root: Path | str | None = None) -> Bundle:
    root = Path(root) if root else Path(__file__).resolve().parents[3] / "knowledge"
    manifest_path = root / "okf.yaml"
    if not manifest_path.exists():
        raise BundleError(f"no OKF manifest at {manifest_path}")
    manifest = yaml.safe_load(manifest_path.read_text()) or {}

    bundle = Bundle(root=root, manifest=manifest, tables=_load_tables(root))
    for path in sorted((root / "wiki").rglob("*.md")):
        page = _parse(path, root)
        if page.id in bundle.pages:                     # pragma: no cover
            raise BundleError(f"duplicate page id {page.id}")
        bundle.pages[page.id] = page
    return bundle
