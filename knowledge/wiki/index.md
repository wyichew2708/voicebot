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
| [Home Insurance](product/general/home.md) | draft | **the product this bot sells, and the one with no source document** |
| [Home contents](product/general/home/contents.md) | draft | placeholder wording carried over from the fact store |
| [Renovation](product/general/home/renovation.md) | draft | placeholder wording |
| [Personal Accident](product/protection/personal-accident.md) | draft | the cross-sell; its figures are placeholders |
| [Travel Insurance](product/general/travel.md) | approved | two wordings live: v8 current, v7 for older policies |
| [Travel COVID-19 add-on](product/general/travel/covid-19-add-on.md) | approved | the one compiled benefit table |

## Concepts

| Page | Status | Scope |
|---|---|---|
| [Free look period](concept/free-look.md) | approved | travel |
| [Pre-existing condition](concept/pre-existing-condition.md) | approved | travel |
| [Time limit to notify a claim](concept/claims-notice.md) | approved | travel |

Each is cited from the travel wording and scoped to travel. Nothing
establishes that home insurance carries the same terms.

## Journeys, channels, entities

- [Renewing a policy](journey/renew.md) — draft; the steps live in the call script
- [Tiq, direct online](channel/tiq-sg.md) — approved
- [Etiqa, online or adviser](channel/etiqa-sg.md) — draft
- [Etiqa Insurance Pte. Ltd.](entity/etiqa-sg-legal.md) — draft

## Promotions

- [FREE TRAVEL Campaign 2023](promotion/freetravel-2023.md) — expired 31 December 2023, and still answers to say so

## What is missing

See [conflict 0001](../conflicts/0001-no-sg-home-wording.md). The bot's own
product has no wording, no product summary and no rate table in the corpus.
