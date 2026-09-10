# Resource-efficient, conversational voicebot enhancement plan

Review date: 2026-09-07. Baseline: `35f3cfd3943bccb40a46668755bffeb5c2609430`.
Scope: source review and proposed implementation; no inference benchmark on target hardware.

## Prioritized implementation backlog

Working branch: `enhancement/realtime-voicebot`.

First milestone implemented: see [interruption-handling.md](interruption-handling.md)
for behavior, protocol migration, tests and native-inference cancellation limits.
Second milestone implemented: see [latency-measurement.md](latency-measurement.md)
for browser playback estimates, backend timings and repeatable reports.
Third milestone implemented: see [predictable-inference.md](predictable-inference.md)
for resident voice selection, worker limits, overload behavior and deployment changes.
Priority 4 software implemented for the supported Mac Kokoro path and browser
AudioWorklet: see [incremental-audio.md](incremental-audio.md). Chatterbox remains
buffered; a streaming CUDA adapter and sustained target-device verification
remain open. Hardware latency and memory still need measurement. Priority 5
software is implemented: see [adaptive-endpointing.md](adaptive-endpointing.md)
for local CPU neural VAD, adaptive pauses and fallback behavior. Held-out audio
and target-device validation remain open. Priority 6 (compact-model routing
and prompt correction) is the next independent improvement.

Start with interruption correctness and measurement. Retain the current models for the first change so any improvement or regression can be attributed to the pipeline.

| Order | Priority | Deliverable | Completion gate |
|---|---|---|---|
| 1 | P0 | Interruptible session transport: keep receiving during inference, serialize dialogue mutations, add generation IDs, invalidate stale audio and handle hangup/disconnect. Include timing for interruption detection and playback stop. | Deterministic tests interrupt during ASR, routing and TTS; no stale audio resumes, no concurrent state mutation, and session jobs are cleaned up. Running worker cancellation limitations are explicit. |
| 2 | P0 | Audible latency instrumentation: client playback acknowledgements, meaningful-answer versus acknowledgement timing, queue wait, cache hit/miss and TTS real-time factor. | One repeatable report separates cached/uncached and cold/warm turns; timings use compatible clocks and do not present mock delays as hardware performance. |
| 3 | P0 | Predictable live inference: no cloning-model load on a call, consistent cached/live voice, GPU-worker ownership and bounded inference queues. | An unseen name/email does not load another model or switch speaker; cache reads are not blocked behind synthesis; overload is bounded. |
| 4 | P1 | Actual incremental TTS, bounded playout and AudioWorklet capture/playback. | First audio arrives before whole-response synthesis finishes on a streaming-capable backend, and sustained playback meets underrun/RTF gates. |
| 5 | P1 | Neural VAD and adaptive endpointing, preserving pre-roll and echo suppression. | Reduced end-of-turn delay without regressions on short replies, spelling, hesitation, speakerphone echo or false interruptions. |
| 6 | P1 | Compact-model routing experiment and prompt correction; compare with the existing model on held-out local-language cases. | Smaller model meets routing quality and timeout gates; total memory and end-to-end latency improve on target hardware. |
| 7 | P2 | Contextual intent/slot planning and grounded dynamic replies, with validated clauses before speech. | Multi-intent replies, corrections and follow-ups improve without unsupported policy figures, unauthorized actions or lost pending questions. |
| 8 | P2 | Final Mac/GPU profiles, admission control and sustained concurrency benchmark. | Publish measured memory and maximum concurrent calls meeting latency and quality gates. |

### First implementation slice

Primary files: `src/voicebot/server.py`, `src/voicebot/events.py`, `src/voicebot/call/engine.py`, `src/voicebot/runtime/base.py` and `ui/demo-console.html`.

1. Define session/turn/generation identifiers and interruption/playback events.
2. Separate the socket reader from the response task, with a single writer and a serialized state owner; prioritize cancellation/control events over queued output.
3. Stop client playback immediately on a confirmed interruption and discard frames belonging to obsolete generations.
4. Cancel pending response work and prevent stale results from changing dialogue state. Preserve completed authorized actions and record what speech was actually played.
5. Bound incoming audio and outgoing queues; clean up on hangup, disconnect and error. Cancellation must not assume an executor thread has stopped when its awaiter is cancelled.
6. Add race-condition integration tests and compare against the existing 719-pass baseline. Measure audible interruption latency on the actual browser/device before claiming a speed result.

Do not start with a larger model, a framework rewrite, or broad generative dialogue. The first milestone is a caller who can interrupt at any point, be heard promptly, and receive no leftover audio from the previous response.

## Recommendation

Keep the existing deterministic call controls, policy facts, approved knowledge answers and speech cache. Improve the interruptible audio pipeline first, then introduce a small, grounded conversational model. Benchmark larger models only if the smaller model fails real conversation evaluations.

Treat a Mac's unified memory and NVIDIA VRAM as separate deployment budgets. For the GPU profile below, 40 GB means the entire GPU budget, including all models and inference allocations. Confirm exact Mac chip/RAM, GPU model, languages and target concurrent calls before final sizing. English, Singlish and Mandarin are the initial evaluation scope because those are prominent in the current code.

## Findings grounded in the implementation

| Priority | Evidence | Consequence and proposed change |
|---|---|---|
| P0 | `server.py:ws` awaits ASR and iterates all session output within the receive loop. `barge_in` clears a buffer and logs only. | New control events wait behind generation. Separate receive, serialized dialogue state and audio output tasks. Cancel obsolete work and discard stale results. |
| P0 | Audio begin/end messages have no generation identifier. | Client playback can stop, but later stale audio cannot be reliably identified. Add session/turn/generation IDs, sequence numbers and playback acknowledgements. |
| P0 | MLX `synthesize` accumulates all generated segments before yielding 20 ms frames. CUDA `_post` reads a complete response; `speak` joins chunks. | Network framing is not incremental synthesis. Add a genuine audio-chunk backend contract and streaming sidecar. Keep whole-utterance fallback for engines that cannot stream. |
| P0 | `ui/demo-console.html` uses RMS endpointing, `SILENCE_MS=700`, `BARGE_MS=260`, and ScriptProcessor capture. | Fixed waiting time and noise sensitivity limit responsiveness. Move audio capture/playout to AudioWorklet and evaluate neural VAD with adaptive endpointing. |
| P0 | `MLXBackend` uses one executor worker for ASR/TTS/generation and cache reads. LLM initialization instead uses `asyncio.to_thread`. | Long jobs and cache misses can block listening; initialization bypasses the stated thread-affinity discipline. Keep MLX initialization/inference on an owned worker, add cooperative cancellation, and move ordinary disk reads out of the GPU queue. |
| P0 | Mac `speak(prerendered=True)` renders cache misses using the cloning model. | An unseen name/address may cause seconds of delay and an additional resident model. Use a fast live voice selected at call start; prohibit unexpected model loads during a call. |
| P1 | `call/router.py` requests one of 19 labels with an eight-token budget. `Backend.complete` has no token-stream contract. | The configured 35B model is used for classification, not dynamic spoken answers. Benchmark a smaller non-thinking instruction model and add a separate grounded response path. |
| P1 | Router prompt says requests to do anything should be treated as off-topic, alongside categories such as repeat and email change. | Resolve contradictory wording: customer service requests are valid data; only instructions attempting to alter system rules must be ignored. Test imperative paraphrases. |
| P1 | `_routed` always emits a thinking phrase while classification runs. | Fast answers still receive a redundant preamble. Emit an acknowledgement only after a measured delay, and count meaningful-answer latency separately. |
| P1 | `knowledge/answer.py` selects approved spoken wording by aliases. | Preserve filtering and approved wording; add semantic retrieval for paraphrases with abstention and provenance, rather than assuming this is already generative RAG. |
| P1 | Deployment docs exclude the LLM from the GPU budget; `services.sh` assumes it is already running elsewhere. | Re-budget all services together; pin serving versions and validate each audio endpoint against the actual model. |
| P1 | CUDA completion payload lacks the explicit thinking-disable setting present in the MLX path. | Verify deployed template behavior and use a non-thinking model or supported configuration; eight reasoning tokens may produce no label. |
| P1 | Server measures time to audio production; browser schedules buffered playout. | Add client-side first-playback and interruption-stop timestamps; backend latency is not the caller's complete experience. |
| P1 | CUDA health accepts a 2xx TTS response without checking its `ready` field; the sidecar exposes readiness in that JSON. Its async `/tts` handler calls synchronous generation directly. | Validate readiness payloads and an actual warm inference; move generation to an owned worker so health/control requests remain responsive. |

Some comments and historical deployment plans describe earlier states. Implementation and target-machine measurements should be the release authority. MoE active parameter count does not remove the need to store inactive expert weights, nor imply dense-model-equivalent quality or a fixed bandwidth saving.

## Proposed conversation pipeline

1. AudioWorklet captures frames; echo cancellation and noise suppression remain enabled. Stateful resampling preserves timing across input buffers.
2. Lightweight CPU VAD produces speech events; streaming ASR supplies provisional and committed text. Retain a safe utterance-mode fallback where the selected runtime does not implement streaming.
3. Turn manager uses acoustic silence, transcript stability and dialogue context. Initial tuning ranges: 250–400 ms for short complete replies, 500–800 ms for ordinary speech, 900–1,400 ms for spelling or hesitation. These are experiments, not universal defaults.
4. Deterministic controls handle stop, consent, identity, explicit human requests and confirmed actions. A compact model handles contextual intent, multiple intents and optional conversational wording.
5. Existing policy/knowledge filters select allowed facts and sources. The response planner returns a structured plan: intents, pending question, proposed slots, answer source IDs and action requests.
6. Validate the plan against state and tools. Render exact dates, premiums, limits and confirmation text deterministically. Allow short generated explanations only from approved evidence.
7. Validate each complete clause before speech release. Stream checked clauses to incremental TTS with bounded look-ahead. Never speak unvalidated tokens: audio cannot be retracted.
8. Client playout reports first audible scheduling, consumed audio and underruns. Barge-in immediately clears its queue and invalidates the generation throughout the server.

Maintain one serialized state owner per call. Distinguish text planned, audio generated and speech actually played. An interrupted question must not be treated as fully heard. Track the outstanding question, confirmed/proposed slots, recent turns and a compact summary; bound model context initially to 4–8k tokens.

For cancellation, `task.cancel()` alone is insufficient for executor threads or remote inference. Support cancellation at safe generation boundaries and backend abort where available. Otherwise ignore stale results and account for the occupied worker. Bound input audio duration, outgoing audio queues, speculative jobs and per-session inference requests.

Do not cancel a genuinely authorized action after it has committed; retain its idempotency key and verified result even when speech is interrupted. Speculative ASR/intent work must be read-only, and final transcript confirmation is required before actions.

## Hardware profiles and model experiments

These are provisional allocation envelopes, not measured footprints or guaranteed capacity. Model weights, quantization overhead, activations, KV cache, runtime reservations and concurrency all contribute.

| Component | Mac efficiency profile | Single 40 GB NVIDIA profile |
|---|---|---|
| VAD | CPU Silero/ONNX candidate | CPU Silero/ONNX candidate |
| ASR | Existing Whisper baseline versus Qwen3-ASR 0.6B through a validated MLX adapter | Qwen3-ASR 0.6B/1.7B versus current MERaLiON configuration on local speech |
| Language model | Qwen3-4B-Instruct-2507, 4-bit, non-thinking, first candidate | Same 4B baseline; compare a 7–8B instruction model only if needed |
| TTS | Kokoro first for efficient EN/ZH; compare Qwen3-TTS 0.6B if quality is insufficient | Compare CosyVoice streaming and Qwen3-TTS 0.6B against existing Chatterbox |
| Initial workload | One live call | One call first, then 2/4/8 until latency or memory gate fails |

Mac initial allocation goal: 3–5 GB LLM, 1–3 GB ASR, 0.5–2 GB TTS and 2–4 GB inference/application overhead: approximately 6.5–14 GB for the voice application. Preserve another 6–10 GB for macOS and other apps; a 16 GB Mac requires particularly careful measurement. Larger TTS checkpoints need a revised budget. Test sustained memory pressure and swap, not only model loading.

40 GB GPU initial allocation goal: 8–12 GB language-model service including capped KV cache; 3–6 GB ASR; 2–5 GB TTS; 3–5 GB other runtime/transient allocations. This totals 16–28 GB, leaving 12–24 GB headroom. Avoid double-counting runtime allocations already included in service measurements. Tune explicit per-service reservations; separate processes do not isolate GPU compute or memory bandwidth.

Keep only the selected ASR/LLM/TTS resident. No cloning warm-up, alternate model benchmark or batch job during live calls. Admission control must enforce the measured capacity. Larger models are an optional quality experiment, not a prerequisite for dynamic conversation.

One voice identity per call: render cached and dynamic content with the same engine/settings where possible. If the Mac uses Kokoro live, use Kokoro for its cache too. Evaluate English, Mandarin and within-sentence code switching by ear; a language-specific speaker switch is not automatically acceptable. Preserve names, addresses, email pronunciation and number normalization tests.

## Dynamic behavior to add

- Resolve follow-ups such as “why?”, “the other one” and “same address” using the outstanding question and confirmed state.
- Handle multiple intents: “Yes, that's me, but call tomorrow.”
- Correct a slot without restarting: “No, I said fifteen, not fifty.”
- Distinguish a backchannel (“mm-hmm”) from a substantive interruption.
- Answer a side question, then resume the unanswered renewal question without repeating completed steps.
- Give brief first answers and offer detail; ask one question at a time.
- Preserve current jurisdiction/product/version filters, identity gates, handoffs and action confirmations.
- Use delayed acknowledgements sparingly; they must never imply an action succeeded before its tool result.

## Delivery sequence and gates

| Stage | Work | Required evidence |
|---|---|---|
| 0: baseline | Record representative audio and current end-to-end behavior; pin dependencies/model revisions; instrument playback and memory. | Reproducible cold/warm, cached/uncached, EN/ZH/Singlish results on each machine. |
| 1: interruption | Separate socket receive from inference, introduce generation IDs, bounded queues and playback acknowledgements. | Barge-in during ASR, generation and TTS never resurrects stale audio; disconnects release jobs. |
| 2: efficient audio | AudioWorklet, VAD/endpoint experiments, true streaming backend, consistent cache/live voice, no live model loads. | Lower meaningful response latency without more false cuts, dropped short replies or audio gaps. |
| 3: dynamic dialogue | Small-model routing, structured state/slots, grounded clause generation, evidence filters and contextual follow-ups. | Improved task completion/paraphrase coverage; no regression in deterministic safety/action cases. |
| 4: capacity | Shared-device scheduling, warmup readiness, admission control and sustained target-hardware runs. | Measured maximum concurrency satisfying latency, memory and quality gates. |

Planning allowance: roughly 4–6 engineering weeks, conditional on runtime compatibility and access to both target machines; this is not a delivery commitment.

## Evaluation contract

Use 300–500 consented or synthetic audio cases spanning short replies, accents, code switching, names/digits, speakerphone echo, silence/noise, interruptions, tool failures and multi-turn corrections. Split by speaker and wording so test data is not copied from handler templates. Keep transcript-only regression checks, but do not count them as ASR/TTS or audible-latency evaluation.

Starting acceptance targets, to validate and revise on actual devices:

| Metric | Target |
|---|---|
| Last user speech to first meaningful audio, cached/fixed | p50 ≤700 ms; p95 ≤1.2 s |
| Same, dynamic response on GPU | p50 ≤1.2 s; p95 ≤2 s |
| Same, dynamic response on Mac | p50 ≤1.5 s; p95 ≤2.5 s |
| Confirmed interruption event to playback stopped | p95 ≤150 ms; also report speech-onset detection delay separately |
| Live TTS real-time factor | p95 <0.8 at admitted workload |
| Stale audio after interruption | Zero in deterministic race/replay suite |
| Unconfirmed actions or unsupported numeric policy claims | Zero in release suite; not a claim of universal safety |

Report meaningful answer and acknowledgement latency separately; stage timings include endpoint wait, ASR finalization, queueing, retrieval/routing, model first token, validated clause, TTS first chunk and client playout. Use client monotonic clocks for client-to-client intervals and correlation IDs for server spans; do not subtract unrelated clocks. Track task completion, clarification rate, entity accuracy, false interruptions, WER/CER, underrun duration, peak memory and voice naturalness. Run a sustained 30-minute session before increasing concurrency.

## Primary references

- [Qwen3 ASR](https://github.com/QwenLM/Qwen3-ASR): 0.6B/1.7B candidates and streaming implementations. Runtime support must be validated separately on MLX.
- [Qwen3 TTS](https://github.com/QwenLM/Qwen3-TTS): small multilingual TTS candidates; model-family streaming claims do not prove the selected adapter streams.
- [CosyVoice](https://github.com/QwenAudio/CosyVoice): streaming multilingual synthesis candidate for CUDA.
- [MLX Audio](https://github.com/Blaizzy/mlx-audio): Apple Silicon audio adapters; pin and benchmark exact versions.
- [Qwen3-4B-Instruct-2507](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507): non-thinking baseline candidate.
- [Silero VAD](https://github.com/snakers4/silero-vad): lightweight CPU speech detection candidate.

Published benchmark speed and first-packet claims are not end-to-end latency guarantees for this application.

## Baseline verification in this review

`python -m pytest -q`: 719 passed, one skipped, four dependency/API deprecation warnings in 176.18 seconds. Tests ran in this Linux review environment, not on Apple Silicon or an NVIDIA inference server.

`python scripts/eval.py` completed successfully using the default `mac-polyglot` knowledge profile and mock backend: 270 caller turns across 56 nonempty recorded calls, 31 expectations met and zero failures. Its path breakdown was 251 keyword turns, 10 handoffs and nine clarifications. Eighteen turns were replayed after the engine ended a call earlier than the recording; this limits its interpretation as full-conversation success. The default profile permits unsourced answers, so these results are not a strict production-knowledge evaluation. No live ASR, LLM or TTS quality/speed result is inferred from this replay.
