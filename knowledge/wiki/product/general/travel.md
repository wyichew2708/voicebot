---
okf_version: "0.1"
id: product/general/travel
title: Travel Insurance
type: product
status: approved
lifecycle: on_sale
jurisdiction: SG
underwriter: Etiqa Insurance Pte. Ltd.
line_of_business: general
regulated_advice: false
# The product's own names, which resolve an entity.
aliases: ["travel insurance",
          "travel policy",
          "travel plan",
          "travel cover",
          "旅游保险",
          "旅行保险"]
# The ways a caller asks what the thing covers, used only when no page
# matched on a name of its own. They are a fallback and not ordinary aliases
# because "what does the policy cover for renovation" must reach the
# renovation page, and a longest-match rule would hand it to this one.
#
# The gap they close: on a recorded call "may I know more details of this
# policy?" and "why is the coverage of this policy?" both ended in a callback
# while this page sat here holding the answer.
fallback_aliases: ["what does it cover",
                   "what does this policy cover",
                   "what does the policy cover",
                   "what does my policy cover",
                   "what is covered",
                   "what's covered",
                   "what am i covered for",
                   "coverage of this policy",
                   "coverage of the policy",
                   "policy coverage",
                   "the coverage",
                   "what coverage",
                   "more details of this policy",
                   "more details of the policy",
                   "details of this policy",
                   "details of the policy",
                   "more details",
                   "more detail",
                   "tell me more about the policy",
                   "tell me about the policy",
                   "what's in the policy",
                   "what is in the policy",
                   "what are the benefits",
                   "what do i get",
                   "保什么",
                   "保障范围",
                   "有什么保障",
                   "包括什么",
                   "详情",
                   "多一些资料",
                   "讲多一点",
                   "多讲一点",
                   "这个保单保什么"]
authority:
  - raw/wordings/tiq-travel-2024-02-05-v8.md
  - raw/wordings/tiq-travel-2023-07-06-v7.md
  - raw/faq/tiq-travel-covid19-addon.md
version_in_force: "8"
effective_from: 2024-02-05
effective_to: null
links:
  covid_add_on: product/general/travel/covid-19-add-on
  concepts: [concept/free-look, concept/pre-existing-condition, concept/claims-notice]
confidence: high
compiled_at: 2026-09-03
review_due: 2026-12-03
reviewed_by: []
---

# Travel Insurance

A single-trip and annual travel product underwritten by the company named in
[the underwriter page](../../entity/etiqa-sg-legal.md). The wording in force is
version 8, effective 5 February 2024
([source](../../../raw/wordings/tiq-travel-2024-02-05-v8.md#p1)); version 7 of
6 July 2023 remains the governing wording for policies incepted before that
date ([source](../../../raw/wordings/tiq-travel-2023-07-06-v7.md#p1)).

Two wordings being live at once is the ordinary state of an insurance book,
and it is why answers are version-filtered against the customer's own policy
rather than against whatever is on sale today.

## What the policy is built from

The policy, schedule, endorsements, application, proposal form and declaration
are read together as one contract
([source](../../../raw/wordings/tiq-travel-2024-02-05-v8.md#p1)). The duty of
disclosure under the Insurance Act 1966 is stated on the same page.

## Benefit sections

Benefits are organised into numbered sections, and the limits attached to each
depend on the plan bought. Figures are never written here; they are held in
`benefit-tables/` and read at answer time. See
[the COVID-19 add-on](travel/covid-19-add-on.md) for the one benefit table
compiled so far.

## How to buy

Coverage, limits and exclusions do not vary by brand -- this is one product
from one underwriter.

<!-- okf:channel-variant -->
| Channel | Route | Contact |
|---|---|---|
| Tiq | direct online | [Customer Care](../../channel/tiq-sg.md) |
| Etiqa | online or adviser | [adviser channel](../../channel/etiqa-sg.md) |
<!-- /okf:channel-variant -->

Current promotions are not listed on this page. They are effective-dated and
live in [promotions](../../promotion/freetravel-2023.md).
