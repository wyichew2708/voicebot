---
okf_version: "0.1"
id: promotion/freetravel-2023
title: FREE TRAVEL Campaign 2023
type: promotion
status: approved
jurisdiction: SG
underwriter: Etiqa Insurance Pte. Ltd.
line_of_business: general
regulated_advice: false
aliases: ["free travel", "freetravel", "voucher code", "promo code"]
applies_to: [product/general/travel]
authority:
  - raw/promotions/tiq-travel-freetravel-tc-2023.md
  - raw/promotions/tiq-travel-freetravel-faq-2023.md
effective_from: 2023-12-01
effective_to: 2023-12-31
# The window has closed. The page still answers, because the wording below
# is about the expiry itself.
answer_after_expiry: true
confidence: high
compiled_at: 2026-09-03
review_due: 2026-12-03
reviewed_by: []
spoken:
  source: raw/promotions/tiq-travel-freetravel-tc-2023.md#p1
  en: >-
    That campaign ran in December 2023 and has ended, so the code no longer
    applies. I can have a colleague tell you what is running now.
  zh: >-
    那个活动是二〇二三年十二月的，已经结束了，优惠码不能再用。
    我可以请同事告诉您现在有什么优惠。
---

# FREE TRAVEL Campaign 2023

A voucher-code campaign offsetting the premium on a travel policy, open to
Singapore citizens, permanent residents and foreigners, and valid from
1 December to 31 December 2023
([source](../../raw/promotions/tiq-travel-freetravel-tc-2023.md#p1)).

## Why this page is still here

It has expired, and that is the point. `effective_to` is in the past, so the
frontmatter filter drops it from retrieval and the bot cannot offer it. The
page is kept so that a caller who asks about the code by name gets a straight
answer about it having ended, rather than a model improvising around a promo
code it half-remembers from training data.

Promotions are never compiled into product pages, never cached beyond their
window, and always carry a hard `effective_to`. The linter fails a promotion
that does not.
