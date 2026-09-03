# Industrial readiness

What "good enough to put in front of a customer" means for this bot, what has
been done about it, what is measured, and what is still open. Written after
81 recorded demo calls; every item below traces to something a caller
actually said.

## The bar

An outbound renewal call has three ways to fail that matter:

1. **Say something untrue** about the policy — a premium, a date, a benefit.
2. **Not listen** — answer a different question, drop a question the caller
   answered, press on past something they said.
3. **Break a rule** — pitch without consent, dodge "are you a robot", ignore
   "stop calling me", keep talking to someone who is not the policyholder.

Latency is a fourth, but it is a symptom: a caller who waits two seconds says
"hello?", which the recogniser then has to make sense of.

## Where accuracy comes from

Nothing the caller hears is generated. Every line is fixed wording — the
client's script, the fact store, the handoff procedure — with slots filled
from the policy record. Two models sit behind that and neither writes a word:

- the **router** (`call/router.py`) picks one of 19 handlers when the keyword
  layer has nothing, and is rejected if it returns anything else;
- the **dictation reader** (`call/dictation.py`) extracts a candidate email
  from a spelled-out address, which the caller then hears read back and
  confirms before it reaches the record.

So failure mode 1 is structural, not behavioural: no model can invent a
premium because no model produces speech. That is the property to protect in
every future change.

## What is measured

`scripts/eval.py` replays every recorded caller turn through the engine.

```
270 caller turns across 56 recorded calls  (keyword layer only)

  keyword     251  93.0%      settled for free, in milliseconds
  handoff      10   3.7%      given to a person
  clarify       9   3.3%      asked to repeat
  offered customer care : 6  (2.2% of turns)
  31 expectations, 31 met
```

With the models in the loop (`make eval-live`, run with the console paused
so only one copy of the router is resident):

```
  keyword     202  89.8%
  guardrail    16   7.1%      median 892 ms, p90 912 ms
  handoff       7   3.1%
  model failures: 0
```

Read the three non-keyword rows of the first table with care. "Clarify" here is the mock
backend standing in for the model — with the guardrail off, anything the
keyword layer cannot place is asked again. Live, most of those go to the
router. The number that matters is the first one: nine turns in ten never
wait for a model at all.

`tests/eval/expectations.jsonl` names caller lines from real calls and what
the reply must or must not contain; a miss is a regression against a real
customer. `scripts/eval.py --live` runs the same with the models in the loop
and reports guardrail latency.

Run it after any change to `reactions.py`, `facts.py`, `router.py` or the
engine. Add a line to the expectations file every time a transcript exposes
something.

## Fixed, with the line that exposed it

| the caller said | what went wrong | now |
|---|---|---|
| 你是机器人吗 (twice) | "I'm Dave from Etiqa" — a dodge | "Yes — I'm an automated assistant…" then the line they were on |
| 可以不要打电话给我了吗 | absorbed as declining the cross-sell | `crm.dnc_request`, consent blocked, call ends, disposition recorded |
| 哈哈哈哈哈哈哈哈 | routed off-topic → offered customer care | not a turn; the question stands |
| 阿基米德的浮力原理 (nobody spoke) | passed the rate gate, offered customer care, and two of them switched the call to Mandarin | wrong script + model says off-topic = noise: not a turn, not switch evidence. Wrong script + *no* verdict is still not a turn, but the switch evidence stands — two turns running in the other language change the language by design, and a slow model must not stall a caller who has simply carried on in Mandarin |
| why is that? (about the pitch) | "not something I can help with" | answers what it is, asks again |
| what are you trying to say just now? | repeat delivered the pitch | consent stands; only a yes unlocks turn 6 |
| my roof is leaking, does that count | "not something I can help with" | "I'd rather a colleague confirm exactly what's covered than guess" |
| 可以可以请安排 | dropped — an accepted offer, forgotten | pending questions are held, never discarded |
| 呃没有收到 | flagged, then handed to the model, which offered escalation | answers the question it was an answer to |
| w, y, i, c, h, e, w, alias hotmail dot com | "I got some of that but not all" | parser fixed; model reads what the parser cannot, read back for a yes |
| uh, okay, what happen? | "okay" read as yes; question ignored | every word must be part of an answer |
| Yes, I received it. · Sure, thank you. · uh yes, that is my home. | a yes with words after it went to the model — two seconds, or a timeout, for a yes | a yes by the looser reading advances; a *no* only by the strict one, because 不乱来啊 has a 不 in it and is not an answer |
| 呃什么保险来的 · 哦将我要做什么咧 | to the model, which timed out under load | purpose and procedure phrasings the recording produced |
| 可以不要打电话给我了吗 (while the cross-sell offer was pending) | consumed as a decline of the offer — "好的，没问题" — nothing recorded | a do-not-call request is checked before any pending question |
| 呃没有收到 (one turn late) | only counted as a notice-denial when the turn counter read exactly 3; otherwise to the model, which offered customer care | "didn't receive it" is about the notice whenever it is said, from the moment the purpose has been stated |
| 2026 | "twenty fifty-six" | spelled forms for years, codes, emails, unit numbers, phone numbers |

## The wait

A guardrail turn costs 1–2 s of silence on the line. Two things now cover it:

- **A filler from the cache** — "One moment." — played *while* the model
  runs, not before it. Starts the model, speaks, then awaits. Neutral on
  purpose: "Let me just check that" followed by "sorry, I didn't catch that"
  when the model timed out was a promise and its contradiction.
- **Warm voices.** Every shipped voice is pre-rendered for every line the
  script can reach (1,701 lines across seven voices and two languages). A cold
  voice is 2–4 s a turn; the console
  says so in amber before a call starts, and the warm-up yields to any live
  call rather than competing with it for the GPU.

The largest remaining lever is measured and unapplied by choice: **prompt
caching** on the router takes a guardrail turn from ~1030 ms to ~310 ms at
identical accuracy (35/40). See the model survey artifact. Smaller models
were slower-per-correct-answer, not faster: the 35B-A3B activates ~3B
parameters and is already the small model.

## Compliance state, as code

- `compliance/gates.py` — identity, DNC register, marketing consent, advice.
- `CallState` — whether the call has *earned* a pitch, separate from whether
  it is permitted one. A handoff, an unresolved request or an impatient caller
  suppresses it.
- Consent to the cross-sell is affirmative-only. "Not a no" is not a yes; two
  non-answers are a decline; a request to repeat never delivers the pitch.
- Disclosure on request; do-not-call on request; both are closed-set
  dispositions in the call record.

## Still open

In rough order of what would bite first on a real line.

1. **RHEL end-to-end has never run.** The CUDA path is written to mirror the
   Mac path by calling the same code, and the sidecar refuses to start on the
   English-only model — but none of it has met a GPU. Ship `voices/cache`
   with the deploy; first run is a smoke test, not a demo. The runbook is in
   [deployments.md](deployments.md#shipping-a-release-2026-09-03), and two
   defects that would only ever have shown up on that first run are now
   fixed: the TTS container was never given `voices/`, and the sidecar looked
   its reference clip up in a two-entry table of its own rather than using
   the one the console asked for — so five of the seven voices, and every
   Mandarin line, would have been rendered by the model's default speaker
   with nothing logged.
2. **Telephony.** The console talks to a browser microphone. A SIP/PSTN leg
   changes the audio (8 kHz, codec loss), the endpointing and the barge-in
   behaviour. The client VAD constants will need re-measuring against a real
   line.
3. **The recogniser on Singlish.** Polyglot-Lion is the Mac substitute;
   MERaLiON on the GPU box is the model actually trained on it. Accuracy on
   real Singaporean callers is unmeasured until the box runs.
4. **Facts.** Coverage answers come from the OKF bundle in `knowledge/`,
   which cites a source document and a page number for every approved answer
   and refuses to let a Singapore page cite a Malaysian one. Home insurance is
   compiled from its own policy wording: the product, its four section pages,
   the insured perils and the cancellation terms, all cited. Both profiles
   answer them identically now, because they are no longer unsourced.

   Two gaps remain. **Tiq Personal Accident**, the product the call
   cross-sells, has no source document, so its discount, starting premium,
   monthly equivalent and inpatient limit are still placeholders in
   `data/facts.py` — the figures most likely to be quoted on a call and the
   ones with the least behind them. And the home wording **contradicts its
   own filename** about version and effective date, so a customer's policy
   cannot be matched to the wording that governs it
   (`knowledge/conflicts/0002`). See `docs/knowledge-layer.md`.
5. **Prompt caching** on the router — see above. One change, 3.3× on the
   slowest turn type.
6. **The live eval needs the GPU box.** On this Mac it only runs with the
   console stopped and the synthesiser left unloaded (`_RoutingOnly` in
   `scripts/eval.py`); with both resident, Metal runs out of memory and every
   model call fails silently as a timeout. That is a measurement constraint,
   not a product one — but it means the router's accuracy under production
   load has only been measured on a machine that cannot run production.
7. **The engine's shape.** `on_caller` is a long ordered chain; the "pending
   question dropped" bug appeared four times this build because a question
   we asked was state living in a variable rather than a state the machine
   was in. An explicit pending-question state would remove the class.
8. **Retention and access** the moment `personas.py` becomes real customer
   data: `logs/calls.jsonl` is a compliance record and a PDPA liability at
   once.
9. **Load.** One call at a time on the Mac. The GPU path is designed for
   concurrency and has not been tested under it.
