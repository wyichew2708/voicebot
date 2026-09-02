---
okf_version: "0.1"
id: concept/free-look
title: Free look period
type: concept
status: approved
jurisdiction: SG
underwriter: Etiqa Insurance Pte. Ltd.
line_of_business: general
regulated_advice: false
aliases: ["free look", "free look period", "cooling off", "cooling-off",
          "change my mind", "cancel after buying", "犹豫期", "冷静期"]
applies_to: [product/general/travel]
authority:
  - raw/wordings/tiq-travel-2024-02-05-v8.md
version_in_force: "8"
effective_from: 2024-02-05
confidence: high
compiled_at: 2026-09-03
review_due: 2026-12-03
reviewed_by: []
spoken:
  source: raw/wordings/tiq-travel-2024-02-05-v8.md#p39
  en: >-
    You have fourteen days from receiving the policy to look through it and
    cancel in writing for a refund of the premium, as long as no claim has
    been made. It does not apply to renewals or to policies of less than a
    year.
  zh: >-
    您收到保单后有十四天可以细看条款。这段期间内以书面通知取消，
    在没有索赔的情况下可以退还保费。续保和一年以下的保单不适用。
---

# Free look period

Fourteen days from the date the policyholder receives the policy, during which
they may cancel in writing and have the premium refunded, provided no claim
has been made
([source](../../raw/wordings/tiq-travel-2024-02-05-v8.md#p39)).

Two carve-outs sit in the same clause and are easy to drop when summarising,
which is why the spoken wording keeps both: the period does not apply to
policies with a period of insurance shorter than a year, and it does not apply
to renewals
([source](../../raw/wordings/tiq-travel-2024-02-05-v8.md#p39)).

The renewals carve-out is the one that matters to this bot. It runs renewal
calls, so a caller who asks "can I change my mind?" on a renewal is asking
about the case the clause excludes.

## Scope

`applies_to` names travel only. The clause is cited from the travel wording,
and nothing here establishes that home insurance carries the same terms.
Assuming it does is the kind of quiet generalisation this bundle exists to
prevent.
