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
