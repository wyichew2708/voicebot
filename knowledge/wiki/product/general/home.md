---
okf_version: "0.1"
id: product/general/home
title: Home Insurance
type: product
status: draft
lifecycle: on_sale
jurisdiction: SG
underwriter: Etiqa Insurance Pte. Ltd.
line_of_business: general
regulated_advice: false
aliases: ["home insurance", "house insurance", "home policy", "home cover",
          "家居保险", "房屋保险", "住家保险"]
authority: []
version_in_force: null
effective_from: null
effective_to: null
links:
  contents: product/general/home/contents
  renovation: product/general/home/renovation
# The provenance section below names both underwriters on purpose: the whole
# point of the page is which company did NOT write a Singapore home policy.
brand_prose_allowed: >-
  Names the Malaysian underwriter in order to rule it out. This page makes no
  coverage claim, so there is no coverage prose for a brand to contaminate.
confidence: low
compiled_at: 2026-09-03
reviewed_by: []
---

# Home Insurance

**This is the product the renewal bot sells, and it is the one product in this
bundle with no source document.**

Nothing under `raw/` describes Singapore home insurance. There is no policy
wording, no product summary and no rate table. Every claim about what this
product covers would therefore be unreferenced, so this page and its children
are `draft` and cannot be approved.

## The trap this page exists to close

The only home-insurance document in the corpus is
`raw/non-sg/etiqa-my-home-faq.md`, a Malaysian FAQ describing four Malaysian
products under a different underwriter and a different regulator. It is
superficially the right subject and entirely the wrong answer, and it is the
most plausible way this bundle could produce a confident falsehood for a
Singapore caller.

The jurisdiction rule in the linter refuses it mechanically: a Singapore page
that cites a Malaysian source fails the build. That is deliberate. Judgement
is not a control; a failing test is.

See [the conflict entry](../../../conflicts/0001-no-sg-home-wording.md).

## To finish this page

Drop Etiqa's Singapore home wording and product summary into `raw/wordings/`,
declare them in `sources.yaml`, run `make kb-ingest`, then compile the benefit
table and the coverage sections from them. No code changes: the moment these
pages reach `approved`, the bot starts answering coverage questions from them
with citations, in every deployment, including the ones that refuse unsourced
answers today.

## How to buy

<!-- okf:channel-variant -->
| Channel | Route | Contact |
|---|---|---|
| Tiq | direct online | [Customer Care](../../channel/tiq-sg.md) |
| Etiqa | online or adviser | [adviser channel](../../channel/etiqa-sg.md) |
<!-- /okf:channel-variant -->
