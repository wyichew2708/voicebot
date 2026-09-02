---
okf_version: "0.1"
id: product/general/travel/covid-19-add-on
title: Travel COVID-19 Add-on
type: product
status: approved
lifecycle: on_sale
jurisdiction: SG
underwriter: Etiqa Insurance Pte. Ltd.
line_of_business: general
regulated_advice: false
aliases: ["covid add-on", "covid-19 add-on", "covid cover", "covid coverage",
          "coronavirus cover", "新冠附加保障", "新冠保障"]
applies_to: [product/general/travel]
authority:
  - raw/faq/tiq-travel-covid19-addon.md
plan_tiers: ["entry", "savvy", "luxury"]
benefit_table: tiq-travel-covid19-addon
effective_from: null
effective_to: null
confidence: high
compiled_at: 2026-09-03
review_due: 2026-12-03
reviewed_by: []
spoken:
  source: raw/faq/tiq-travel-covid19-addon.md#p1
  en: >-
    The COVID-19 add-on covers you before, during and after the trip: travel
    postponement, trip cancellation, medical expenses overseas, a quarantine
    allowance, emergency evacuation, curtailment, and a hospitalisation benefit
    back in Singapore. The amounts depend on which plan you hold.
  zh: >-
    新冠附加保障涵盖行程前、行程中和行程后：行程延后、取消行程、海外医疗费用、
    隔离津贴、紧急医疗撤离、行程缩短，以及回到新加坡后的住院津贴。
    具体保额要看您持有哪一个配套。
---

# Travel COVID-19 Add-on

An optional add-on to [travel insurance](../travel.md), sold across three plan
tiers ([source](../../../../raw/faq/tiq-travel-covid19-addon.md#p1)).

## Eligibility

The insured must be fully vaccinated and must meet the pre-departure and
post-arrival testing requirements imposed by the destination country or the
transport operator
([source](../../../../raw/faq/tiq-travel-covid19-addon.md#p1)).

## Benefits

Every figure below is read from `benefit-tables/tiq-travel-covid19-addon.csv`
at render time. None of them is typed into this page, which is what lets a
limit change be a one-line CSV diff that a reviewer can actually see.

| Benefit | Entry | Savvy | Luxury |
|---|---|---|---|
| Travel postponement | {{table:tiq-travel-covid19-addon:travel_postponement:entry}} | {{table:tiq-travel-covid19-addon:travel_postponement:savvy}} | {{table:tiq-travel-covid19-addon:travel_postponement:luxury}} |
| Trip cancellation and loss of deposit | {{table:tiq-travel-covid19-addon:trip_cancellation_loss_of_deposit:entry}} | {{table:tiq-travel-covid19-addon:trip_cancellation_loss_of_deposit:savvy}} | {{table:tiq-travel-covid19-addon:trip_cancellation_loss_of_deposit:luxury}} |
| Medical expenses incurred overseas | {{table:tiq-travel-covid19-addon:medical_expenses_overseas:entry}} | {{table:tiq-travel-covid19-addon:medical_expenses_overseas:savvy}} | {{table:tiq-travel-covid19-addon:medical_expenses_overseas:luxury}} |
| Overseas quarantine allowance | {{table:tiq-travel-covid19-addon:overseas_quarantine_allowance:entry}} | {{table:tiq-travel-covid19-addon:overseas_quarantine_allowance:savvy}} | {{table:tiq-travel-covid19-addon:overseas_quarantine_allowance:luxury}} |
| Emergency evacuation and repatriation | {{table:tiq-travel-covid19-addon:emergency_evacuation_repatriation:entry}} | {{table:tiq-travel-covid19-addon:emergency_evacuation_repatriation:savvy}} | {{table:tiq-travel-covid19-addon:emergency_evacuation_repatriation:luxury}} |
| Travel curtailment and disruption | {{table:tiq-travel-covid19-addon:travel_curtailment_disruption:entry}} | {{table:tiq-travel-covid19-addon:travel_curtailment_disruption:savvy}} | {{table:tiq-travel-covid19-addon:travel_curtailment_disruption:luxury}} |
| Hospitalisation benefit in Singapore | {{table:tiq-travel-covid19-addon:hospitalisation_benefit_singapore:entry}} | {{table:tiq-travel-covid19-addon:hospitalisation_benefit_singapore:savvy}} | {{table:tiq-travel-covid19-addon:hospitalisation_benefit_singapore:luxury}} |

Source for the whole table:
[the add-on FAQ](../../../../raw/faq/tiq-travel-covid19-addon.md#p1).

The spoken answer names the benefits and refers the caller to their own plan
rather than reciting a tier. Reading out a limit for the wrong tier is the
single most likely way to misrepresent this product on a call.
