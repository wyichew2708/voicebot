---
okf_version: "0.1"
id: product/general/home
title: Home Insurance
type: product
status: approved
lifecycle: on_sale
jurisdiction: SG
underwriter: Etiqa Insurance Pte. Ltd.
line_of_business: general
regulated_advice: false
aliases: ["home insurance", "house insurance", "home policy", "home cover",
          "家居保险", "房屋保险", "住家保险"]
authority:
  - raw/wordings/tiq-home-2025-03-15-v10.md
  - raw/marketing/tiq-home-brochure-2025-03.md
version_in_force: "10"
effective_from: 2025-03-15
effective_to: null
links:
  building: product/general/home/building
  renovation: product/general/home/renovation
  contents: product/general/home/contents
  emergency_cash: product/general/home/emergency-cash-allowance
  concepts: [concept/insured-perils, concept/free-look, concept/cancellation-refund]
# The provenance note below names the Malaysian underwriter in order to rule
# it out. There is no coverage prose here for a brand to contaminate.
brand_prose_allowed: >-
  Records which company did not write this policy, and why the Malaysian
  document in the corpus must never answer a Singapore caller.
confidence: high
compiled_at: 2026-09-03
review_due: 2026-12-03
reviewed_by: []
spoken:
  source: raw/wordings/tiq-home-2025-03-15-v10.md#p8
  en: >-
    The policy is built in sections. Building, renovation and contents are the
    main three, and each is covered up to the sum insured shown on your
    schedule. There are further sections for things like an emergency cash
    allowance, personal legal liability and valuables.
  zh: >-
    这份保单是分项目的。建筑、装修和家庭财物是主要的三项，
    每一项都保到您保单明细表上列明的保额。
    另外还有紧急现金津贴、个人法律责任和贵重物品等项目。
---

# Home Insurance

The product this renewal call is about. The wording in force is version 10 of
15 March 2025
([source](../../../raw/wordings/tiq-home-2025-03-15-v10.md#p1)).

The policy, schedule, endorsements, online application, proposal form and
declaration are read together as one contract, and the duty of disclosure
under section 23(5) of the Insurance Act 1966 is stated on the same page
([source](../../../raw/wordings/tiq-home-2025-03-15-v10.md#p1)).

## Sections

Fifteen numbered sections. The three that carry the sums insured are
[building](home/building.md), [renovation](home/renovation.md) and
[contents](home/contents.md); each pays for physical loss or damage caused by
an [insured peril](../../concept/insured-perils.md), up to the sum insured
shown in the schedule, and each is operative only if the schedule shows it
([source](../../../raw/wordings/tiq-home-2025-03-15-v10.md#p8)).

The remaining sections cover an
[emergency cash allowance](home/emergency-cash-allowance.md), 24-hour
emergency home assistance, personal legal liability, valuables, removal of
debris, professional fees, conservancy charges, unauthorised transactions on a
stolen card, accidental breakage of mirrors and fixed glass, money, personal
cyber, and family accidental death protection
([source](../../../raw/wordings/tiq-home-2025-03-15-v10.md#p8)).

## Sums insured are per customer, not per product

Every one of those sections is capped by the sum insured "stated in the
Schedule", which is the customer's own document
([source](../../../raw/wordings/tiq-home-2025-03-15-v10.md#p8)). So the
figures are not knowledge and do not belong in this bundle. The bot reads them
from the policy record, where they are already correct for the person on the
line.

The product brochure prints indicative ranges by plan type. It is marketing,
the lowest authority in the bundle, its table extracts unreliably from the
PDF, and none of it is compiled here.

## Provenance

Until this wording was supplied, the only home-insurance document in the
corpus was a Malaysian FAQ from Etiqa General Insurance Berhad, a different
underwriter under a different regulator. The jurisdiction rule refuses it
mechanically, which is why nothing on this page ever came from it. See
[conflict 0001](../../../conflicts/0001-no-sg-home-wording.md).

## How to buy

<!-- okf:channel-variant -->
| Channel | Route | Contact |
|---|---|---|
| Tiq | direct online | [Customer Care](../../channel/tiq-sg.md) |
| Etiqa | online or adviser | [adviser channel](../../channel/etiqa-sg.md) |
<!-- /okf:channel-variant -->
