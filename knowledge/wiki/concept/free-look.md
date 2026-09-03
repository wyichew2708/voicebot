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
applies_to: [product/general/travel, product/general/home]
authority:
  - raw/wordings/tiq-home-2025-03-15-v10.md
  - raw/wordings/tiq-travel-2024-02-05-v8.md
version_in_force: "10 (home), 8 (travel)"
effective_from: 2024-02-05
confidence: high
compiled_at: 2026-09-03
review_due: 2026-12-03
reviewed_by: []
spoken:
  source:
    - raw/wordings/tiq-home-2025-03-15-v10.md#p18
    - raw/wordings/tiq-travel-2024-02-05-v8.md#p39
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

Home and travel, and only because both wordings were checked. The home policy
carries the clause at
[section 6 of its general conditions](../../raw/wordings/tiq-home-2025-03-15-v10.md#p18)
in materially identical terms: fourteen days, written request, premium
refunded if no claim has been made, and the same two carve-outs for short
policies and renewals.

Until the home wording was ingested, this page was scoped to travel alone and
said so, because nothing established that home insurance carried the same
terms. It turned out to. The point is that the bundle waited for the document
instead of assuming, and that the scope widened by a citation rather than by
someone's recollection.
