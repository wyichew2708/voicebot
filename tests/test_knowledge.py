"""The knowledge layer: the bundle lints, the rules bite, the golden set holds.

The negative tests matter more than the positive ones here. A linter that
passes a clean bundle proves nothing; a linter that refuses a Singapore page
citing a Malaysian source is the control that keeps this corpus honest.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from voicebot.knowledge import lint
from voicebot.knowledge.answer import Answer, lookup, spoken_lines
from voicebot.knowledge.okf import BundleError, load_bundle
from voicebot.knowledge.policy import Serving, resolve

ROOT = Path(__file__).resolve().parents[1]
BUNDLE_DIR = ROOT / "knowledge"
GOLDEN = BUNDLE_DIR / "evals" / "golden.jsonl"


@pytest.fixture(scope="module")
def bundle():
    return load_bundle(BUNDLE_DIR)


# --- the bundle itself -----------------------------------------------------

def test_bundle_lints_clean(bundle):
    errors = [f for f in lint.check(bundle) if f.level == "error"]
    assert errors == [], "\n".join(str(f) for f in errors)


def test_no_warnings_either(bundle):
    """Warnings that are allowed to accumulate stop being read. If one is
    genuinely acceptable, silence it explicitly on the page."""
    warns = [f for f in lint.check(bundle) if f.level != "error"]
    assert warns == [], "\n".join(str(f) for f in warns)


def test_every_approved_page_is_cited(bundle):
    for page in bundle.pages.values():
        if page.status == "approved":
            assert page.authority, f"{page.id} approved with no source"


def test_no_singapore_page_cites_a_malaysian_source(bundle):
    declared = lint._declared_sources(BUNDLE_DIR)
    for page in bundle.pages.values():
        for src in page.authority:
            entry = declared.get(src, {})
            assert entry.get("jurisdiction") == page.jurisdiction, (
                f"{page.id} ({page.jurisdiction}) cites {src} "
                f"({entry.get('jurisdiction')})")


def test_figures_come_from_the_table_not_from_prose(bundle):
    """The benefit table is the only place a limit is written down."""
    page = bundle.get("product/general/travel/covid-19-add-on")
    assert "{{table:" in page.body
    assert "$100,000" not in page.body
    assert bundle.figure("tiq-travel-covid19-addon",
                         "medical_expenses_overseas", "savvy") == "$200,000"
    assert "$200,000" in bundle.resolve(page.body)


def test_an_unknown_figure_raises_rather_than_rendering_a_hole(bundle):
    with pytest.raises(BundleError):
        bundle.resolve("{{table:tiq-travel-covid19-addon:no_such_benefit:savvy}}")


def test_the_product_the_bot_sells_is_now_sourced(bundle):
    """This test used to assert the opposite, and failed the day the customer
    supplied the wording. That was the point of it."""
    home = bundle.get("product/general/home")
    assert home.status == "approved"
    assert "raw/wordings/tiq-home-2025-03-15-v10.md" in home.authority
    for child in ("building", "renovation", "contents", "emergency-cash-allowance"):
        page = bundle.get(f"product/general/home/{child}")
        assert page is not None and page.status == "approved", child


def test_the_cross_sell_is_still_unsourced(bundle):
    """The remaining gap, and the figures most likely to be quoted on a call.
    Fails the day a personal accident document is ingested."""
    pa = bundle.get("product/protection/personal-accident")
    assert pa.status == "draft" and not pa.authority, (
        "personal accident now has a source: compile its benefit table, have "
        "the fact store read from it, and update this test")


def test_home_answers_survive_the_production_profile(bundle):
    """The whole point of ingesting the wording: RHEL refuses unsourced
    wording, and these answers are no longer unsourced."""
    from voicebot.data.facts import coverage_lookup
    rhel = Serving("SG", ("product/general/home",), allow_unsourced=False)
    for question in ("does it cover my renovation", "what about home contents",
                     "is fire covered", "can i cancel my policy"):
        got = coverage_lookup(question, "en", rhel)
        assert got is not None and got.sourced, question


# --- the rules bite --------------------------------------------------------

def _page(tmp_path: Path, body: str, name: str = "concept/x") -> object:
    root = tmp_path / "knowledge"
    (root / "wiki" / Path(name).parent).mkdir(parents=True, exist_ok=True)
    (root / "benefit-tables").mkdir(parents=True, exist_ok=True)
    (root / "okf.yaml").write_text("okf_version: '0.1'\nserving: {}\n")
    (root / "wiki" / f"{name}.md").write_text(body)
    return load_bundle(root)


HEAD = """---
okf_version: "0.1"
id: concept/x
title: X
type: concept
status: {status}
jurisdiction: SG
underwriter: Etiqa Insurance Pte. Ltd.
aliases: ["x"]
authority: {authority}
confidence: high
{extra}---

{body}
"""


def _make(tmp_path, *, status="approved", authority="[]", extra="", body="Body."):
    return _page(tmp_path, HEAD.format(status=status, authority=authority,
                                       extra=extra, body=body))


def _rules(bundle):
    return {f.rule for f in lint.check(bundle)}


def test_approved_without_a_citation_fails(tmp_path):
    assert "refs" in _rules(_make(tmp_path))


def test_money_in_prose_fails(tmp_path):
    b = _make(tmp_path, body="The limit is $5,000 per trip.")
    assert "figures" in _rules(b)


def test_a_promotion_without_an_expiry_fails(tmp_path):
    body = HEAD.format(status="approved", authority="[]", extra="", body="B.")
    body = body.replace("type: concept", "type: promotion")
    assert "ttl" in _rules(_page(tmp_path, body))


def test_an_id_that_does_not_match_its_path_is_refused(tmp_path):
    body = HEAD.format(status="draft", authority="[]", extra="", body="B.")
    body = body.replace("id: concept/x", "id: concept/somewhere-else")
    with pytest.raises(BundleError, match="does not match its path"):
        _page(tmp_path, body)


def test_a_broken_wiki_link_fails(tmp_path):
    b = _make(tmp_path, status="draft", body="See [gone](../nope.md).")
    assert "links" in _rules(b)


def test_a_stale_page_is_warned_and_drops_out_of_retrieval(tmp_path):
    b = _make(tmp_path, status="draft", extra="review_due: 2020-01-01\n")
    assert "staleness" in _rules(b)
    page = b.get("concept/x")
    assert page.is_stale_on(date(2026, 9, 3))


# --- serving policy --------------------------------------------------------

def test_unsourced_answers_is_a_word_not_a_flag():
    assert resolve({"knowledge": {"unsourced_answers": "allow"}}).allow_unsourced
    assert not resolve({"knowledge": {"unsourced_answers": "refuse"}}).allow_unsourced
    with pytest.raises(ValueError):
        resolve({"knowledge": {"unsourced_answers": "maybe"}})


def test_the_bundle_default_refuses_unsourced_wording(bundle):
    assert (bundle.manifest["serving"]["unsourced_answers"]) == "refuse"


def test_shipped_profiles_disagree_on_purpose():
    from voicebot import config
    assert resolve(config.load("mac-polyglot")).allow_unsourced, "the demo speaks placeholders"
    assert not resolve(config.load("rhel")).allow_unsourced, "production does not"


# --- the golden set --------------------------------------------------------

def _cases():
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["id"])
def test_golden(case, bundle):
    serving = Serving(jurisdiction="SG",
                      products=tuple(case.get("products", [])),
                      allow_unsourced=case["unsourced"] == "allow")
    got = lookup(case["q"], case["lang"], bundle=bundle,
                 jurisdiction=serving.jurisdiction,
                 allow_unsourced=serving.allow_unsourced,
                 products=serving.products)

    if case["expect"] == "defer":
        assert got is None, f"{case['note']}: answered from {got and got.page_id}"
        return

    assert got is not None, f"{case['note']}: nothing answered"
    assert got.page_id == case["page"]
    # Case-insensitive: a case should pin what is said, not where a sentence
    # happens to start.
    said = got.text.lower()
    for needle in case.get("must_contain", []):
        assert needle.lower() in said, f"{needle!r} missing from {got.text!r}"
    for needle in case.get("must_not_contain", []):
        assert needle.lower() not in said, f"{needle!r} should not be spoken"


# --- warming ---------------------------------------------------------------

def test_spoken_lines_follow_the_serving_policy(tmp_path):
    """Draft wording is warmed only where it could be spoken.

    Written against a synthetic bundle rather than the real one: every page in
    the shipped bundle that carries spoken wording is now approved, so the
    real bundle can no longer exercise the difference. It still has to work,
    because the next unsourced page will arrive before its source does."""
    body = HEAD.format(status="draft", authority="[]", body="B.", extra=(
        "spoken:\n  en: A draft sentence.\n  zh: 一句草稿。\n"))
    b = _page(tmp_path, body)
    assert spoken_lines(b, allow_unsourced=False) == []
    assert len(spoken_lines(b, allow_unsourced=True)) == 2


def test_warming_is_scoped_to_what_this_deployment_can_say(bundle):
    """Scoping the bot to home insurance and then rendering the travel
    clauses in seven voices is eighty-four files of audio that cannot play."""
    everything = spoken_lines(bundle, allow_unsourced=True)
    home_only = spoken_lines(bundle, allow_unsourced=True,
                             products=("product/general/home",))
    assert len(home_only) < len(everything)
    # The travel claims deadline is scoped to travel. The free look is not a
    # valid probe any more: both wordings carry it, so it is warmed for home
    # on purpose.
    assert not any("thirty days" in t for t, _ in home_only), (
        "a travel-scoped clause was warmed for a home-only deployment")


def test_coverage_answers_are_warmed_at_all():
    """They never were while they lived in the fact store, so the first
    coverage question of a demo paid live synthesis mid-turn."""
    from voicebot.runtime import warm
    jobs = warm.plan(["en", "zh"], ["standard"], [None],
                     Serving("SG", (), allow_unsourced=True))
    texts = {t for t, _, _ in jobs}
    assert any("built-in wardrobes and kitchen cabinets" in t for t in texts)


# --- the engine's contract -------------------------------------------------

def test_the_engine_records_a_citation(bundle):
    from voicebot.data.facts import coverage_lookup
    got = coverage_lookup("what is the free look period", "en",
                          Serving("SG", ("product/general/travel",), False))
    assert isinstance(got, Answer)
    assert got.sourced
    # A page, a source document and a page number. Which document leads is the
    # page's business; that there is one, and that it names a page, is not.
    assert " <- raw/wordings/" in got.citation
    assert re.search(r"#p\d+$", got.citation)


def test_a_broken_bundle_does_not_drop_the_call(monkeypatch):
    """Losing coverage answers costs a callback. Raising here drops a call."""
    from voicebot.data import facts
    import voicebot.knowledge as k
    monkeypatch.setattr(k, "lookup",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    assert facts.coverage_lookup("renovation", "en") is None
