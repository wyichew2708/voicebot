# 0001 — No Singapore home-insurance wording exists in the corpus

**Raised:** 2026-09-03
**Status:** open
**Owner:** content ops / Etiqa product owner
**Blocks:** `product/general/home`, `product/general/home/contents`,
`product/general/home/renovation`, `product/protection/personal-accident`

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
