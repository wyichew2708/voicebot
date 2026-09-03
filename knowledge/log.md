# Operation log

Append-only. What was compiled, when, from what, and why.

## 2026-09-03 — bundle created, first ingest

Ingested nine source documents, 121 pages.

**Singapore, Etiqa Insurance Pte. Ltd.**

| Source | Authority | Pages |
|---|---|---|
| Tiq Travel policy wording v8, 5 Feb 2024 | policy_wording | 41 |
| Tiq Travel policy wording v7, 6 Jul 2023 | policy_wording | 42 |
| Tiq Travel COVID-19 Add-on FAQ | faq | 4 |
| FREE TRAVEL Campaign 2023 FAQ | promotion | 2 |
| FREE TRAVEL Campaign 2023 T&C | promotion | 2 |

**Malaysia, Etiqa General Insurance Berhad — quarantined**

Home, Personal Accident, Travel and General FAQs, 30 pages. Ingested so the
corpus is honest about what is on the shelf and so the jurisdiction gate has
something real to refuse. No Singapore page may cite them.

### Compiled

Fourteen pages. Six approved and cited; eight draft.

Approved: `product/general/travel`, `product/general/travel/covid-19-add-on`,
`concept/free-look`, `concept/pre-existing-condition`,
`concept/claims-notice`, `promotion/freetravel-2023`, `channel/tiq-sg`.

Draft, unsourced: `product/general/home` and its two children,
`product/protection/personal-accident`, `channel/etiqa-sg`,
`entity/etiqa-sg-legal`, `journey/renew`.

### Decisions

- **Two wordings kept live.** v7 governs policies incepted before 5 Feb 2024
  and is not deleted. Answers are version-filtered against the customer's
  policy, not against what is on sale.
- **A duplicate was found and dropped.** `Tiq-Travel-COVID-19-Add-on-FAQ (1).pdf`
  hashes identically to the file without the suffix. One copy ingested.
- **Concepts scoped to travel.** Free look, pre-existing condition and the
  claims deadline are all cited from the travel wording. Nothing establishes
  that home insurance carries the same terms, so `applies_to` names travel
  only. The bot's home calls therefore do not answer from them.
- **The expired campaign still answers.** `answer_after_expiry` lets the page
  say the campaign ended. Silence would leave a named promo code to the model.
- **Placeholder home wording carried over as draft.** The two coverage answers
  that were in the fact store are preserved verbatim so the demo does not
  change behaviour, but they are unsourced and any deployment setting
  `unsourced_answers: refuse` will decline them.

### Open

`conflicts/0001-no-sg-home-wording.md` — the product the bot sells has no
source document.

## 2026-09-03 — the home wording arrived

The customer supplied two documents from tiq.com.sg: the Tiq Home policy
wording and the product brochure. Ingested, and the gap that
`conflicts/0001` was filed for is closed.

| Source | Authority | Pages |
|---|---|---|
| Tiq Home policy wording v10, 15 Mar 2025 | policy_wording | 22 |
| Tiq Home brochure | marketing | 6 |

### Compiled

Nineteen pages now, thirteen approved. New and cited from the home wording:

- `product/general/home` moved from draft to approved
- `product/general/home/building` (section 1)
- `product/general/home/renovation` (section 2), rewritten from the definition
- `product/general/home/contents` (section 3), rewritten from the definition
- `product/general/home/emergency-cash-allowance` (section 4), with the only
  home benefit table the wording fixes
- `concept/insured-perils`, the gate every home coverage question passes
- `concept/cancellation-refund`, eighty per cent pro-rata
- `concept/free-look` widened from travel-only to home as well

### Decisions

- **The document outranks its own filename.** The PDF is published at a v9,
  20 October 2023 URL and every page reads "V10 | 15 March 2025". Recorded as
  version 10, effective 15 March 2025, and filed as `conflicts/0002`.
- **No figures compiled from the brochure.** It is `marketing`, the lowest
  authority, and its benefit table extracts from the PDF with the columns
  scrambled. Sums insured are per customer anyway: the wording caps every
  section at "the Sum Insured stated in the Schedule", so they belong in the
  policy record, not in the bundle. The emergency cash allowance is the
  exception, because the wording states those amounts outright.
- **Contents answers defer on specifics.** The definition is one line and the
  exclusion list is ten. A caller asking about contents has one object in
  mind, so the spoken wording gives the definition and hands the item to a
  person.
- **Free look widened on a citation.** Yesterday it was scoped to travel and
  said that nothing established home carried the same terms. The home wording
  turned out to carry a materially identical clause. The scope changed because
  a document said so, not because it seemed likely.

### What the wording corrected

Both placeholder answers were subtly wrong.

- Renovation was "fixtures and fittings you have installed, such as flooring,
  built-in cabinetry and wiring". Wiring is not in the definition, and a
  former owner's improvements are covered too.
- Contents was "furniture, appliances and personal effects". The definition is
  any moveable household item, and the substance of the clause is its
  exclusions: motor vehicles, pedal cycles, cash, documents, and anything
  already counted under renovation or the building.

Neither was ever spoken on the RHEL profile, which refuses unsourced wording.
Both were spoken on the demo.

### Open

- `conflicts/0002` — the wording contradicts its filename about version and
  effective date, so policies cannot be version-matched.
- Tiq Personal Accident, the cross-sell, still has no source document.
