# Priority 6: compact-model routing and prompt correction

Status: first implementation slice complete: routing prompt/parser audit,
context, evaluation harness and baseline/candidate comparison checks. Live
compact-model and target-hardware
comparisons remain pending. See [routing evaluation](routing-evaluation.md).
Working branch: `enhancement/realtime-voicebot`.

## Goal

Improve understanding of varied caller replies while keeping response latency
and memory suitable for a MacBook or a local GPU with about 40 GB of memory.
Retain the current model as the baseline until a compact candidate passes the
quality and hardware gates. Do not infer live performance from mock tests.

## Implementation order

1. Audit routing prompts and context. Identify conflicting instructions, include
   the pending question and relevant confirmed state, and constrain outputs to
   valid intents. Preserve deterministic handling for straightforward replies.
2. Build a reproducible evaluation harness and a held-out set covering English,
   Mandarin and Singlish; ambiguous replies, corrections, stop requests, human
   handoff requests and multi-intent utterances. Keep evaluation wording separate
   from prompt examples. Report results by language and intent, including errors.
3. Compare a compact model with the current model using identical inputs and
   limits. Pin model revisions, quantization, runtime and prompt versions. Record
   routing accuracy, invalid-output and timeout rates, latency and peak memory.
   Separate cold/warm runs and routing-only measurements from full voice latency.
4. Validate outputs and fallback behavior. Unknown, malformed or timed-out output
   must lead to clarification or handoff without bypassing identity, consent,
   do-not-call handling or action confirmation. Do not treat model-reported
   confidence as a calibrated probability.
5. Promote the smaller model only after the gates below pass on target hardware.
   Keep the prior configuration available for rollback and document the measured
   trade-offs. Load only the selected model during live calls.

The first implementation slice is the routing audit and evaluation harness.
Model selection follows baseline measurement; changing the default is not part
of the audit alone.

## Completion evidence

- Reproducible baseline and candidate reports with per-language/per-intent
  accuracy, critical errors, invalid outputs and timeout counts.
- No regression in deterministic identity, consent, stop, handoff and action
  confirmation cases; zero unauthorized actions in the release suite.
- Agreed routing quality and timeout thresholds defined before candidate
  promotion, with failed cases available for review.
- Measured routing and end-to-end voice latency, total memory and sustained
  resource use on the actual Mac and GPU deployment. A model-only improvement
  does not establish a seamless voice experience.
- Documented configuration, limitations and rollback procedure.

## Outstanding audio verification

Priority 5 software is complete, but neural VAD and adaptive pauses still need
real microphone evaluation for short replies, hesitation, spelling, background
noise, speakerphone echo and interruptions. Complete that target-device gate
alongside routing evaluation. The CUDA streaming TTS adapter and sustained
playback validation from priority 4 also remain open.

Priority 7 will address contextual intent/slot planning and grounded dynamic
replies; see the [full roadmap](resource-efficient-voicebot-plan.md).
