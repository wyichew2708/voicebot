# Streaming synthesis

## The problem it solves

Most of a call is pre-rendered, which is why most of a call is fast. The lines
that are not pre-rendered are the ones no warm pass could have known about: a
customer name that was never in the call list, an answer assembled at call
time, a dictated address read back. Those went through the synthesiser whole.

The synthesiser returns nothing until it is finished. Measured on an M-series
Mac against `mlx-community/chatterbox-multilingual-v3`, `model.generate()`
yields exactly **one** segment, so a 6.3-second line is 5.4 seconds of silence
before the caller hears a word. That is the 7255 ms turn in the recorded call.

## What it does

`spoken.speech_chunks()` cuts an utterance into pieces at sentence boundaries,
with a deliberately short opener and larger pieces after it.
`CallSession._voice_stream()` synthesises them in order and emits each as its
own `AgentAudio`, flagged `start` on the first and `final` on the last. The
transport opens the utterance on `start` and closes it on `final`, so the
console's player appends instead of reloading — one utterance, several events.

Splitting is at sentence boundaries because that is where a listener expects a
breath. Latin sentence punctuation only splits when followed by whitespace,
which is what keeps `a.tan@example.sg` and `1,234.56` intact without masking
them, and a lookbehind stops `Mr.` ending a chunk.

## Why it is usually off

Chunking only pays while synthesis outruns playback. Write `r` for synthesis
seconds per second of audio:

- `r < 1` — each chunk is finished before the one ahead of it stops playing.
  The caller hears a continuous line that started early.
- `r > 1` — total synthesis exceeds total audio. Playback catches up and the
  line drops in the middle, and no chunk layout fixes it, because the deficit
  is in the total.

Measured on this Mac, for a name held in no cache:

| | first audio | outcome |
|---|---|---|
| whole line | 5.37 s | 6.30 s of audio, continuous |
| two chunks | 3.11 s | 3.3 s hole in the middle |

So `r` here is 1.1–1.5, and splitting made the line worse. The same model on
the same input measured 0.65 and 1.55 minutes apart, so the variance is as
large as the effect.

`CallSession` therefore measures `r` as it goes and decides per line, in
`_stream_plan()`, by spending a budget:

- the first piece is exempt — waiting for it is the head latency this is
  trying to shorten, not a hole in the middle of a sentence;
- a piece already on disk costs nothing and hands its whole duration to the
  pieces after it;
- a piece that has to be made spends `r` seconds of budget for every second it
  adds.

If any piece would run the budget out, the line is sent whole. Until a call has
synthesised something, `r` is assumed to be 1.5 — pessimistic, so an unmeasured
machine splits a line only where the pieces ahead of the synthesised one are
already warm. Cache hits never teach the rate: they return in a millisecond,
and believed, they would turn splitting on for the one line that then has to be
made from scratch.

## Why this makes a line worth rewording

The budget is why the shape of the script matters. Turn 1 currently says the
customer's name twice, once in the opening words, so every piece of it is
per-customer and nothing can be warmed:

> Good afternoon **Mr Tan**. This is Michael calling from Etiqa Insurance. Am I
> speaking with **Mr Tan**?

Say it once, at the end, and the first two pieces are identical for every
customer on the list:

> Good afternoon. This is Michael calling from Etiqa Insurance. Am I speaking
> with **Mr Tan**?

Those cached seconds pay for synthesising the name. Measured against a backend
reporting the fixed pieces warm, the reworded shape streams at `r` of 1.0, 1.4
and 2.0, where the same line built entirely from scratch is sent whole at all
three. That is the mechanism that makes an arbitrary customer name affordable
without pre-rendering the call list — on hardware far too slow to stream a
line made from nothing.

The result is that this does nothing on a machine that cannot sustain it, and
turns itself on where it can. The RHEL CUDA box is the target that should.

## Getting `r` below 1

In rough order of expected effect:

1. **Do not share the GPU with batch work.** Three copies of the model were
   resident during these measurements — the console, a `make prerender` job,
   and the benchmark. Contention was worth 2–4x, more than any other factor.
2. **Run it on the CUDA box.** This is the production target and the only
   measurement that matters is on that hardware.
3. **Quantise.** The 4-bit Kokoro live voice is far faster than the cloning
   model; nothing equivalent has been tried for Chatterbox.

Two things measured and rejected as fixes: `prepare_conditionals` re-runs on
every call and can be precomputed and passed as `conds=`, but it is only
0.14 s; and the post-processing (pitch normalisation, pacing) costs about
35 ms per second of audio, which is not where the time goes.

## What it does not fix

Pronunciation. A name synthesised live is mispronounced exactly as it was when
rendered at build time — see the surname audit, which is a separate problem
with a separate answer.
