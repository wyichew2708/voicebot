# Tiq Home Renewal — Demo Plan

Artifact: https://claude.ai/code/artifact/81baff33-21fc-4389-a46f-da5501cdb09f
Companions: [architecture-research.md](architecture-research.md) · [two-engine-plan.md](two-engine-plan.md)

---

## 1. What the demo must prove

1. **It understands how Singaporeans actually talk** — Singlish, code-switching, Malay, Mandarin.
   The differentiator. A demo in clean English demos a commodity.
2. **It knows when to stop** — wrong party, advice requests, cancellation intent, uncleared marketing.
   Refusing correctly impresses this audience more than answering fluently.
3. **It sounds like a person** — sub-second, clean barge-in, no dead air.

**Non-goal: do not demo scale.** No dialler, no campaign management, no concurrency. Invisible in a
demo, expensive to build, and it costs you the scenarios that land.

## 2. Script decomposition — 7 agent turns

| # | Turn | Type | Slots | Risk |
|---|---|---|---|---|
| 1 | Greeting + agent ID + right-party check | template | time_of_day, salutation, surname, agent_name | **hard gate** — verify before anything |
| 2 | Servicing purpose + property | template | property_address | discloses address — post-verification only |
| 3 | Due date + renewal notice | template | due_date | policy detail |
| 4 | Premium, term, sums insured, discount, email confirm | template | premium, term_years, contents_si, reno_si, discount_pct, email | heaviest PII; figures from fact store |
| 5 | CTA — renew by due date, reply on payment | static | — | — |
| 6 | Cross-sell Tiq PA (40% off, $150/yr, COVID, dengue, $2k inpatient) | static | — | **this turn is telemarketing** |
| 7 | "Feel free to list questions" + close | static | — | opens free-form Q&A — needs advice guard |

**Six of seven turns are fixed wording.** Pre-render per language at build time → zero inference on
most of the call. Full pipeline runs only for customer questions and objections.

## 3. ⚠ The line that changes the call's regulatory status

Turns 1–5 **service an existing policy**. Turn 6 pitches **a different product** at a promo discount —
that's a marketing message, and it puts the call inside the DNC provisions.

**The trap:** "they're already our customer." For **voice calls that's wrong** — the ongoing-relationship
exemption covers **text and fax only**. A telemarketing voice call to a Singapore number needs clear and
unambiguous consent or a valid DNC check, even to a ten-year customer. Up to S$1m for an organisation.

→ Build a **cross-sell gate**: check marketing-consent + DNC state before turn 6. If not cleared,
complete the renewal servicing and close, skipping the promotion, reason written to the audit log.
**Demo this deliberately (scenario 7).** Watching the bot decline to sell is more persuasive to a
compliance stakeholder than watching it sell well — and it's the cheapest thing in the plan.

**Two more guards:**
- **Product figures never come from the model.** A hallucinated "40% discount" or "$2,000 inpatient" is
  a misrepresentation of an insurance product. Versioned **fact store** + pre-rendered promo audio.
  The LLM routes and converses; it does not invent prices.
- **Turn 7 invites questions → advice risk.** Ground coverage answers in the actual policy wording;
  route advisory questions to a licensed human. Classifier, not a prompt instruction.

## 4. Demo stack — Engine B (cascade) only

Skip the duplex engine: it speaks neither Singlish nor Malay, the two things this demo exists to show.

| Layer | Demo build | Why |
|---|---|---|
| Client | Browser WebRTC + one SIP number to a team phone | browser to iterate, ringing phone for the demo |
| Orchestration | Pipecat — real | actual product architecture |
| ASR | MERaLiON-3-3B-ASR — real | the differentiator, must be genuine |
| LLM | reserved Qwen3.6 — real | already running |
| TTS | CosyVoice3 (EN/ZH) + Malay path — real | voice quality is half the impression |
| Scripted audio | pre-rendered, 4 languages | speed, consistency, licence workaround (§9) |
| CRM | **stub** — seeded JSON personas | integration proves nothing in a demo |
| DNC / consent | **stub registry**, deliberately mixed | must be stubbed *and visible* — drives scenario 7 |
| Coverage Q&A | RAG over real policy wording | marketing pages aren't truth for coverage |
| Dialler / campaigns | **not built** | invisible, expensive |
| Payments | **never** | script sends an email link; bot takes no payment |

⚠ **Synthetic data only.** Demo environments get screen-shared, recorded, pasted into decks, left
running on laptops — the least protected system you'll build. No real policyholder names, addresses,
premiums or emails. Make the fake-ness visible in the admin view.

## 5. The seven scenarios (this *is* the demo)

| # | Scenario | Customer does | Proves |
|---|---|---|---|
| 1 | Happy path EN | confirms identity, email, accepts | script works end to end |
| 2 | **Singlish** | "Aiyah, I thought I already renew last month ah? Can send me the email or not?" | particles, dropped copula, "or not" tags |
| 3 | **Mid-call switch to Mandarin** | starts EN, switches after turn 2 | code-switching — where generic vendors fail |
| 4 | Malay throughout | responds in Malay from the greeting | fourth language, full coverage claim |
| 5 | **Wrong party** | "He's not home, I'm his son" | discloses *nothing*, offers callback. PDPA win |
| 6 | Coverage question | "Does it cover if my renovation got water damage?" | grounded answer; escalates if advice |
| 7 | **Not cleared for marketing** | ordinary renewal, consent flag off | completes servicing, **skips promotion** |

Scenarios 5 and 7 win the room and most demos omit them. Run them late — after the audience trusts the
bot works, a refusal reads as sophistication rather than failure.

**Add an unscripted 8th:** hand the phone to the audience. It won't go perfectly. Do it anyway — a demo
that only works on rails invites exactly that suspicion.

## 6. Show the machinery — live HUD

Voice demos hide all their work. A second screen fixes it, and doubles as the debug view:

- **Live transcript** with detected language per utterance → scenario 3's switch becomes *visible*
- **Voice-to-voice latency** per turn + running p50
- **Script position** — which of 7 turns, and whether audio was pre-rendered or generated
- **Compliance gates** as indicators: identity verified / DNC checked / marketing consent / advice guard
- **Tool calls** as they fire

## 7. Five to six weeks — demoable at week 2

1. **Week 1 — content and data, no models.** Script → templates + slots. Fact store for every product
   figure. 8–10 synthetic personas across 4 languages, both property types, consent-flag variations.
2. **Week 2 — English happy path end to end.** Pipecat + MERaLiON + Qwen3.6 + CosyVoice3, browser
   client, scenario 1. **The checkpoint that matters** — from here you always have something to show.
3. **Week 3 — the other three languages.** Pre-rendered lines, TTS routing, language detection in HUD.
   Scenarios 2–4. Expect ASR tuning on real recordings, not benchmarks.
4. **Week 4 — the guards.** Right-party gate, cross-sell consent gate, advice classifier, coverage RAG,
   audit log. Scenarios 5–7. Cheapest week, highest value.
5. **Week 5 — telephony, HUD, latency.** One SG SIP number to a team handset (budget real time — the
   +65 caller-ID routing issue surfaces here and is infrastructural). Tune endpointing and barge-in live.
6. **Week 6 — rehearsal and fallbacks.** Run the sheet 5+ times with different speakers. Record clean
   video of every scenario. Write the one-page "what's real vs stubbed" handout — someone will ask.

## 8. Demo-day risks

| Risk | Mitigation |
|---|---|
| First inference of the session is slow, and it's the one on stage | warm every model with a dummy call 5 min before; put it in the runbook |
| SIP trunk or mobile network fails in the room | browser client on the same laptop, switch without commentary |
| Everything fails | recorded video of all 7 scenarios, cued. Never demo without it |
| Audience Singlish is faster/rougher than your test set | expected — that's scenario 8; frame as live test, not showcase |
| "Is this real or a recording?" | let them pick the persona and say something unplanned |
| "What's real vs faked?" | the week-6 handout; volunteer the stubs, credibility compounds |
| Room audio re-triggers barge-in | handset or headset, never speakerphone into an open mic. Test in the room |

## 9. Open items

- ⚠ **Malay TTS licence still blocks this.** `Malaysian-F5-TTS-v3` is CC-BY-NC-4.0 and a demo to win
  business is commercial use. **Demo workaround: have a human voice actor record the pre-rendered Malay
  lines.** Six of seven turns are fixed wording, so you only need synthesis for slot values and
  free-form answers — and human carrier phrases sound better than any TTS. Doesn't scale to production,
  but unblocks the demo honestly.
- **Product figures here are illustrative.** tiq.com.sg returned 403 to automated fetching; the sums
  insured referenced (HDB renovation S$20k–180k, contents S$15k–120k, private building S$300k–2m) come
  from a third-party review. **Populate the fact store from Etiqa's own policy wording and rate card** —
  never from a review site or from this document.
- **The script's own figures need confirming** — "23.5% off", the 5-year term, the Tiq PA promo terms
  are from the supplied transcript. Confirm current before they go into a fact store read aloud.
- **The DNC reading is not legal advice.** Put the servicing-vs-marketing distinction to Etiqa compliance
  *before* the demo. If they disagree, the cross-sell gate is still right — it just changes when it opens.
- **Right-party verification depth is a policy question.** The script asks only "am I speaking with
  Mr X?". Whether that suffices before disclosing a property address is Etiqa's call. Ask early — it
  changes turn 1.
