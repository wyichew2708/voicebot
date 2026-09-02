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

Nine documents, 121 pages, ingested from Etiqa material on this machine.

| | |
|---|---|
| Singapore sources | 5 |
| Malaysian sources, quarantined | 4 |
| compiled pages | 15 |
| approved and cited | 8 |
| draft and unsourced | 7 |

The Singapore set is the Tiq Travel wording at versions 7 and 8, the COVID-19
add-on FAQ, and the 2023 campaign terms. From those, three concepts are
compiled with clause-level citations: the free look period, the definition of
a pre-existing condition, and the thirty-day claims deadline.

## The gap that matters

**The product this bot sells has no source document.** Nothing describes
Singapore home insurance. The home pages are therefore `draft`, they cite
nothing, and they carry the two placeholder answers that used to live in the
fact store.

The only home-insurance document in the corpus is a Malaysian FAQ, published
by a different underwriter under a different regulator, describing four
Malaysian products. It is superficially the right subject and entirely the
wrong answer. It is the most plausible route by which this bundle could tell a
Singapore customer something false.

So the jurisdiction rule is mechanical, not a matter of care: a Singapore page
that cites a Malaysian source fails the lint and cannot be merged. Judgement is
not a control. A failing test is.

See `knowledge/conflicts/0001-no-sg-home-wording.md`.

## The one setting

`unsourced_answers` decides whether draft wording may be spoken.

| Profile | Setting | Effect |
|---|---|---|
| `mac-polyglot` | `allow` | the demo speaks the placeholder home wording, exactly as before |
| `rhel` | `refuse` | a home coverage question becomes a callback from a colleague |

The bundle default is `refuse`. A deployment opts into `allow` in its own
config file, where a reader can see it, and the console logs which it is at
startup and reports it on `/api/health`.

On RHEL this will look like a regression and is not one. Until Etiqa's home
wording is ingested, "I'd rather a colleague confirm exactly what's covered
than guess" is the only honest answer the bot has.

## Closing the gap

No code changes are needed.

1. Put the home policy wording, product summary and rate table somewhere
   readable and declare them in `knowledge/sources.yaml`.
2. `make kb-ingest` — extracts, hashes and dates them into `raw/`.
3. Compile the coverage sections and the benefit table, citing page numbers.
4. `make kb-lint` — the gate.
5. Move the pages to `status: approved` under dual sign-off.

Every deployment then answers home coverage questions with a citation,
including the ones refusing them today. `tests/test_knowledge.py` has a test
that fails the moment home insurance gains a source, so that nobody forgets
step five.

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
