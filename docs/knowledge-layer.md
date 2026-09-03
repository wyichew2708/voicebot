# The knowledge layer

An OKF bundle in `knowledge/`, a deterministic reader in
`src/voicebot/knowledge/`, and one setting that decides whether the bot may
speak wording that nothing backs up.

Built to the design in *Implementation Design — Knowledge & Harness Layer*,
scoped to what one voicebot needs today rather than to the full programme.

## The property this must not break

Nothing the caller hears is generated. Every line is fixed wording with slots
filled from the policy record, and the two models in the system write no
speech: the router picks one of nineteen labels, the dictation reader extracts
a candidate email for read-back.

The knowledge layer keeps that intact. A page carries pre-approved `spoken`
wording; retrieval is a frontmatter filter and an alias match, both ordinary
code; the answer is that wording verbatim or nothing. There is no generation
step and no summarisation step. A miss returns `None` and the engine offers a
colleague, exactly as it did before.

What the layer adds is provenance. Every answer names the page and the source
document behind it, and that citation goes into the call record.

## Layout

```
knowledge/
  okf.yaml            bundle manifest: authority order, rules, serving policy
  sources.yaml        every document the wiki may cite, declared before use
  raw/                immutable extracted sources, hashed, with page markers
    wordings/         policy wordings          <- highest authority
    faq/              FAQs
    promotions/       campaign terms
    non-sg/           Malaysia. Quarantined. No SG page may cite these.
  wiki/               compiled pages. Path is identity.
  benefit-tables/     every figure, as CSV. Numbers live nowhere else.
  conflicts/          source disagreements awaiting a human
  evals/golden.jsonl  golden and adversarial cases
  log.md              append-only: what was compiled, when, and why
```

## What is in it today

Eleven documents, 149 pages.

| | |
|---|---|
| Singapore sources | 7 |
| Malaysian sources, quarantined | 4 |
| compiled pages | 19 |
| approved and cited | 13 |
| draft and unsourced | 6 |

The home set is the Tiq Home policy wording, from which the product page, its
four section pages and two concepts are compiled with clause-level citations.
The travel set is the Tiq Travel wording at versions 7 and 8, the COVID-19
add-on FAQ and the 2023 campaign terms.

The home brochure is ingested as `marketing`, the lowest authority. Nothing is
compiled from it: its benefit table extracts from the PDF with the columns
scrambled, and the sums insured it prints are per customer anyway. The wording
caps every section at "the Sum Insured stated in the Schedule", so those
figures live in the policy record where they are already right for the person
on the line.

## What the wording changed

For a day, this bundle had no source for the product the bot sells, and the
only home-insurance document in the corpus was a Malaysian FAQ from a
different underwriter under a different regulator. It was superficially the
right subject and entirely the wrong answer, and it never contributed a word,
because a Singapore page citing a Malaysian source fails the lint. Judgement
is not a control. A failing test is.

The real wording is now in, and it corrected both answers the bot had been
giving.

| Was | Is |
|---|---|
| renovation covers fittings "you have installed", including wiring | wiring is not in the definition, and a former owner's improvements count |
| contents means "furniture, appliances and personal effects" | any moveable household item, minus ten lines of exclusions |

Neither wrong version was ever spoken on the RHEL profile, which refuses
unsourced wording. Both were spoken on the demo. That is the setting earning
its keep.

Two things are still open. The wording contradicts its own filename about
which version it is and when it took effect, so policies cannot be
version-matched (`conflicts/0002`). And Tiq Personal Accident, the product the
call cross-sells, still has no source document, which leaves its discount and
premium figures as placeholders in the fact store.

## The one setting

`unsourced_answers` decides whether draft wording may be spoken.

| Profile | Setting | Effect |
|---|---|---|
| `mac-polyglot` | `allow` | draft wording may be spoken |
| `rhel` | `refuse` | only wording with a source behind it may be spoken |

The bundle default is `refuse`. A deployment opts into `allow` in its own
config file, where a reader can see it, and the console logs which it is at
startup and reports it on `/api/health`.

Since the home wording arrived, the two profiles answer home coverage
questions identically, because those answers are no longer unsourced. The
setting still matters for the next page that arrives before its document
does.

## Adding a document

No code changes are needed. This is the path the home wording took.

1. Put the document somewhere readable and declare it in
   `knowledge/sources.yaml` with its publisher, jurisdiction and authority.
2. `make kb-ingest` — extracts, hashes and dates it into `raw/` with page
   markers, so a citation can name a page.
3. Compile the pages, citing page numbers.
4. `make kb-lint` — the gate.
5. Move the pages to `status: approved` under dual sign-off.

`tests/test_knowledge.py` fails the day Tiq Personal Accident gains a source,
so nobody forgets step five for the one product still missing.

## The rules the linter enforces

Each exists because breaking it produces one specific wrong answer.

| Rule | What it refuses |
|---|---|
| citations | an approved page with no inline reference to a source |
| locators | a citation to a page number the document does not have |
| jurisdiction | a Singapore page citing a Malaysian source |
| figures | a money amount typed into prose or into spoken wording |
| links | a graph edge that goes nowhere |
| ttl | a promotion with no expiry, which outlives its campaign |
| staleness | an approved page with no review date; an overdue one is demoted |
| identity | a page whose declared id does not match its path |

Warnings are treated as errors by the test suite. A warning nobody clears is a
warning nobody reads.

## Scoping, and why answers disappear

A page answers only for the products a call is about. `concept/free-look` is
cited from the travel wording and declares `applies_to: [product/general/travel]`,
so it says nothing on a home call. That looks unhelpful and is deliberate:
nothing establishes that home insurance carries the same fourteen-day terms,
and quietly generalising is how a knowledge base starts lying.

The adversarial cases in the golden set pin both directions.

## Commands

```bash
make kb-ingest    # re-extract sources into raw/
make kb-check     # have the source documents changed underneath us?
make kb-lint      # the gate
make kb-status    # which pages are approved
make kb-sources   # ingested documents, with hashes
make kb-ask Q="what is the free look period" PROFILE=rhel
```

`kb-ask` answers the way a live call would, under a named profile's rules, so
"why did the bot say that?" is one command instead of a call.

## Not built

Deliberately, and in rough order of when it will be wanted.

- **Retrieval over raw wordings.** Clause-level questions ("is dengue covered
  under section 4?") need the wording itself, not a compiled summary. The
  bundle is structured for it; nothing implements it.
- **A compile loop.** Pages here were compiled by hand from cited text. The
  design's extract-then-compose pipeline, with structured fact extraction and
  semantic diffs, is what makes that repeatable at hundreds of pages.
- **Dual sign-off.** `reviewed_by` is empty on every page. It is a field, not
  yet a workflow.
- **A crawler.** No web sources are ingested, so the merge-consistency problem
  between the two websites does not arise yet.
- **Vector search.** At fifteen pages, a frontmatter filter and an alias match
  are faster and debuggable. Add it when measured recall drops.
