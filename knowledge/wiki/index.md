---
okf_version: "0.1"
id: index
title: Etiqa Singapore knowledge bundle
type: index
status: approved
jurisdiction: SG
underwriter: Etiqa Insurance Pte. Ltd.
line_of_business: general
regulated_advice: false
aliases: []
authority:
  - raw/wordings/tiq-travel-2024-02-05-v8.md
confidence: high
compiled_at: 2026-09-03
review_due: 2026-12-03
reviewed_by: []
---

# Etiqa Singapore knowledge bundle

The agent's entry point. Every page in the bundle is listed here; the linter
warns about any that is not.

Status is load-bearing. **Approved** pages are cited to an ingested source and
may be spoken. **Draft** pages are not, and are spoken only where a deployment
explicitly allows unsourced answers. The wording in force is version 8 of the
travel policy
([source](../raw/wordings/tiq-travel-2024-02-05-v8.md#p1)).

## Products

| Page | Status | Note |
|---|---|---|
| [Home Insurance](product/general/home.md) | approved | **the product this bot sells**; wording v10, 15 March 2025 |
| [Building](product/general/home/building.md) | approved | section 1; the structure, not the fittings |
| [Renovation](product/general/home/renovation.md) | approved | section 2; fixtures and fittings that are not part of the building |
| [Home contents](product/general/home/contents.md) | approved | section 3; moveable household items, minus a long exclusion list |
| [Emergency cash allowance](product/general/home/emergency-cash-allowance.md) | approved | section 4; the only home figures fixed by the wording |
| [Personal Accident](product/protection/personal-accident.md) | draft | the cross-sell; its figures are still placeholders |
| [Travel Insurance](product/general/travel.md) | approved | two wordings live: v8 current, v7 for older policies |
| [Travel COVID-19 add-on](product/general/travel/covid-19-add-on.md) | approved | a compiled benefit table |

## Concepts

| Page | Status | Scope |
|---|---|---|
| [Insured perils](concept/insured-perils.md) | approved | home |
| [Cancelling the policy](concept/cancellation-refund.md) | approved | home |
| [Free look period](concept/free-look.md) | approved | home and travel |
| [Pre-existing condition](concept/pre-existing-condition.md) | approved | travel |
| [Time limit to notify a claim](concept/claims-notice.md) | approved | travel |

Scope is set by which wording the clause was read from, never by assuming that
one product's terms carry to another. The free look period covers both
products because both wordings were checked and found to agree.

## Journeys, channels, entities

- [Renewing a policy](journey/renew.md) — draft; the steps live in the call script
- [Tiq, direct online](channel/tiq-sg.md) — approved
- [Etiqa, online or adviser](channel/etiqa-sg.md) — draft
- [Etiqa Insurance Pte. Ltd.](entity/etiqa-sg-legal.md) — draft

## Promotions

- [FREE TRAVEL Campaign 2023](promotion/freetravel-2023.md) — expired 31 December 2023, and still answers to say so

## What is missing

The home wording is in ([conflict 0001](../conflicts/0001-no-sg-home-wording.md)
is resolved), but two things are still open.

- **Tiq Personal Accident**, the product this call cross-sells, has no source
  document. Its discount and premium figures remain placeholders in the fact
  store, and they are the ones most likely to be quoted on a call.
- **The home wording contradicts its own filename** about which version it is
  and when it took effect, so policies cannot be version-matched. See
  [conflict 0002](../conflicts/0002-home-wording-version-mismatch.md).
