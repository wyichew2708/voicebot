# Handing a call to a colleague

## Why this is a document and not a sentence

The first build "escalated" by saying a line and carrying on. In one recorded
call it told the customer it could not update their email address, that a
colleague would call — and then, without pausing, pitched personal accident
cover at them.

Nothing in that call was a bug in the ordinary sense. Every component did what
it was told. The failure was that a handoff had no consequences: it did not
stop the script, did not gate the marketing, was not recorded in a form anyone
could act on, and never checked that the promised callback could actually
reach the customer.

A handoff is a state the call enters. This is what entering it does.

## When

| Trigger | Reason code | Detected by |
|---|---|---|
| Caller asks for a person | `requested` | `reactions.asks_for_human` |
| Caller says twice that we are not understanding them | `complaint` | `reactions.sounds_frustrated` |
| Advice under the Financial Advisers Act | `advice` | `compliance.gates.check_advice` |
| Any change to the details we hold | `data_change` | `facts.wants_record_change` -> `engine._change_request` |
| Any change to the cover itself | `policy_change` | `facts.wants_record_change` -> `engine._change_request` |
| Caller asks for a lower premium | `pricing` | `facts.is_price_request` |
| Caller raises something off this call | `off_topic` | the guardrail, then `_pending = "officer"` |
| Malay — understood, not spoken | `language` | `engine._looks_malay` |
| Tamil — understood, not spoken (two turns running) | `language` | `engine._looks_tamil` |
| Three replies running we could not make out | `not_understood` | `engine._clarify`, `_repeat` |

**The bot no longer attempts a change before handing it over.** It used to: it
asked for the new address, read back what it thought it had heard, and on a
recorded call turned "w y i a" into `yi@hotmail.com` — then made it worse on
the retry, because a caller spelling something out more carefully is a caller
the recogniser has already failed once. A voice line is not a form. Nothing is
written to the record either way; what goes across is that a change was asked
for and the caller's own words, so a colleague who can verify them finishes
the job. Where the model can read a dictated address it is attached to the
handoff as an **unverified suggestion**, marked as one, to save the colleague
a minute — never spoken, never written.

`requested` and `complaint` outrank everything else: a caller who has asked for
a person does not need a fourth attempt at the script first. `handoff.PRIORITY`
holds the order.

## The procedure

1. **Name the reason in the customer's terms.** "I don't want to risk getting
   your email address wrong — if it's off by one character the renewal notice
   won't reach you at all." Not "escalating", and not silence.
2. **Say what happens and by when.** "A colleague will call you back — they'll
   have everything we've covered, so you won't have to repeat yourself."
3. **Check we can reach them.** The callback number is read back four digits at
   a time and confirmed. A callback promised to a number nobody answers is
   worse than no callback. If the caller says it is the wrong number we log
   that and let the colleague confirm it — capturing a new number over the
   same line that has just failed us is how the callback goes nowhere twice.
4. **Record it.** A `HandoffRequested` event carries the reason, the code, a
   summary in the operator's words, and what is still outstanding — including,
   for a failed address, the caller's own words as we heard them.
5. **Stop.** No further script turns. No cross-sell. `_advance` returns
   immediately once `session.handoff` is set, and `may_cross_sell` refuses
   independently, so neither path can reintroduce it alone.

## Warm transfer

This build promises a **callback**, because there is no telephony leg to
bridge. The seam for a real warm transfer already exists:

- `Handoff.warm` — set true when a leg can be bridged.
- `HandoffRequested` — the event a telephony layer subscribes to.
- `handoff.ACTION_WARM` — the customer-facing wording for a live transfer
  ("let me put you through now"), already written in both languages.

What a telephony integration must add: bridge the leg on the event, keep the
audio path open while it rings, and fall back to the callback wording if no
agent answers within the queue timeout. Nothing above this line changes.

## Not simply cross-selling

Consent and DNC say whether we are *permitted* to pitch. They say nothing
about whether we should. `compliance.gates.CallState` carries the second
question, and every field in it exists because of a moment in a real call:

| Field | Blocks the pitch when |
|---|---|
| `handing_off` | we just told them we could not help |
| `awaiting_adviser` | they are already waiting on a callback |
| `unresolved` | a servicing request is unfinished |
| `impatient` | they told us to hurry, or to go |
| `declined` | they already said no on this call |
| `comprehension_failures` | we have not been hearing them |

When it does run, turn 6 is **asked for, not delivered**:

> "Before I let you go — may I take twenty seconds to mention one thing that
> could save you money? Happy to skip it if you'd rather."

A no ends it in one word and is logged. A yes gets the client's approved
wording, unchanged. This is strictly more conservative than the signed-off
script, which delivered the pitch unprompted — but the permission sentence is
new wording and needs Etiqa sign-off before any real call.

## What is still missing

- **No live agent exists.** Every handoff is a logged callback request; the
  console shows it, `logs/calls.jsonl` records it, and nothing dials anyone.
- **The callback window is asserted, not enforced.** "Within one working day"
  is a promise no part of this system can keep on its own.
- **Reason codes are not yet reconciled with Etiqa's own dispositions.** The
  six here are what the call can actually distinguish; a real deployment maps
  them onto the CRM's list.
