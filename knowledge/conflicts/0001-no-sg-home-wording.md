# 0001 — No Singapore home-insurance wording exists in the corpus

**Raised:** 2026-09-03
**Status:** resolved 2026-09-03
**Owner:** content ops / Etiqa product owner
**Blocks:** `product/protection/personal-accident` (still open for that one)

## The disagreement

The renewal bot sells Singapore home insurance. No document under `raw/`
describes it. The only home-insurance document available anywhere in the
corpus is `raw/non-sg/etiqa-my-home-faq.md`, which is:

- published by Etiqa General Insurance Berhad, not Etiqa Insurance Pte. Ltd.;
- written for Malaysia, under a different regulator;
- describing four Malaysian products (Houseowner and Householder, Fire,
  MyRumah, Home Secure) that are not the Singapore product.

Superficially it is the right subject. It is entirely the wrong answer.

## Why this is filed as a conflict rather than fixed

There is nothing to fix in the wiki. The defect is upstream: the source set is
incomplete. Filing it here keeps the gap visible and attached to the pages it
blocks, instead of being remembered by whoever happens to be looking.

## Resolution

Obtain from Etiqa Singapore:

1. the home insurance policy wording currently in force, with its version and
   effective date;
2. the product summary or product highlights sheet;
3. the benefit and sum-insured table, or the rate card, as data rather than a
   brochure;
4. the equivalent three for Tiq Personal Accident, which the call cross-sells.

Declare them in `sources.yaml`, run `make kb-ingest`, compile, and move the
pages to `approved` under dual sign-off.

## Consequence while open

Every deployment that sets `unsourced_answers: refuse` answers a home coverage
question with a callback from a colleague. That is the correct behaviour and
it is measured: see the golden set case `home-coverage-unsourced`.


---

## Resolution, 2026-09-03

The customer supplied the Tiq Home policy wording and the product brochure
from tiq.com.sg. Both are now declared in `sources.yaml` and ingested, and the
home pages are compiled and approved from the wording.

What this closed:

- `product/general/home` and its four children are `approved` and cited.
- `concept/insured-perils` and `concept/cancellation-refund` are new pages,
  compiled from the same wording.
- `concept/free-look` widened from travel-only to home as well, on a citation
  rather than an assumption. The two wordings carry materially identical
  clauses.
- The Malaysian FAQ never contributed a word, which is what the jurisdiction
  rule was for.

What the wording changed about answers the bot was already giving:

- Renovation was described as fixtures and fittings "you have installed,
  such as flooring, built-in cabinetry and wiring". Wiring is not in the
  definition, and a former owner's improvements are covered too.
- Contents was described as "furniture, appliances and personal effects". The
  definition is any moveable household item, and the substance of the clause
  is its exclusion list, not its inclusion.

**Still open:** Tiq Personal Accident, the product the call cross-sells. Its
discount, starting premium, monthly equivalent and inpatient limit are still
placeholders in `data/facts.py`, and no source document has been supplied for
them. Those are the figures most likely to be quoted on a call.
