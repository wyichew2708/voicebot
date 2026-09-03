# 0002 — The home wording's filename and its contents disagree about version

**Raised:** 2026-09-03
**Status:** open
**Owner:** content ops / Etiqa web team
**Severity:** low for answers, high for record-keeping

## The disagreement

The document published at

```
https://www.tiq.com.sg/wp-content/uploads/2023/10/Tiq-Home-Policy-Wording-v9-20-Oct-2023-FINAL.pdf
```

is named **v9, 20 October 2023** in its URL and filename. Every one of its
twenty-two pages carries the header **"V10 | 15 March 2025"**. The final line
of the last page then reads "Information is correct as at 20 October 2023".

So the same artefact asserts two versions and two dates.

## How it was resolved

By the authority rule: the document is the contract, the filename is metadata
about it, and a header repeated on every page outranks a footer that appears
once. The bundle records the wording as **version 10, effective 15 March
2025**, and `raw/wordings/tiq-home-2025-03-15-v10.md` is named accordingly.

## Why it is still filed

Three reasons, none of which the compile step can fix.

1. **A customer holding a policy incepted between those dates cannot be
   version-matched.** Answers are supposed to be filtered against the wording
   in force when the policy was written. That is not possible while the
   effective date is ambiguous.
2. **A 2023 URL serving a 2025 document means the 2023 document is gone.**
   Anyone with an older policy has no way to read their own terms.
3. **It is a defect against the website, not the wiki.** The knowledge base
   finding it is the intended behaviour: a continuous consistency audit of the
   published material.

## Resolution

Ask Etiqa to confirm which version is in force, from which date, and to
publish the superseded wording at a stable URL. Then correct `effective_from`
and, if a v9 document exists separately, ingest it with an `effective_to` so
older policies can be answered against their own terms.
