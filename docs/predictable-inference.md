# Predictable live inference

Priority 3 removes request-time model loading from the normal voice path and
bounds native work. It retains the existing configured models and does not
claim a measured latency or 40 GB memory result.

## Mac behavior

All local ASR/TTS/LLM loading and generation now run on one owned MLX thread.
The LLM loads before calls when `guardrail.enabled` is true; when disabled or
unavailable, a live routing request does not attempt to load it again. Restart
after fixing a startup model failure. Keyword/clarification fallback remains.

If `backend.tts.prerender.model` is configured (for example `mac-polyglot`),
that model becomes the **only resident TTS model**. Kokoro is not also loaded.
Cached speech reads from disk; an unseen name, email or dynamic line uses the
resident model with the selected reference, language segmentation, pitch and
rate processing. One synthesis attempt is allowed per uncached line. Failure
does not silently switch to another speaker. The output is cached as before.

Without a prerender model (`mac`), the configured preset TTS handles all lines;
unrelated cloning-cache files are ignored. This offers a lighter TTS path but
does not provide the custom cloned speakers. Switching profiles is a restart,
not a mid-call fallback.

This prevents a second voice-model load during a call; it does not make
whole-utterance cloning instantaneous. Dynamic speech still has synthesis cost,
and pronunciation/naturalness must be checked on the actual device. The large
local LLM also counts toward Mac unified memory, even if a separate GPU server
already runs a copy. Measure the full resident set before declaring it fits.

## Bounded work

| Worker | Running jobs | Pending jobs by default |
|---|---:|---:|
| Mac local MLX | 1 | 2 |
| Mac ASR sidecar HTTP | 1 | 2 |
| CUDA client HTTP | 4 | 2 |
| Cache I/O, separate from inference | 2 | 8 |
| CUDA TTS sidecar | 1 | 2 |

`backend.inference.max_pending` changes the pending limit of the Mac inference/
ASR HTTP workers or CUDA client HTTP workers. It must be a nonnegative integer.
The TTS sidecar and cache I/O limits are fixed here. These are per-process job
limits, not an established concurrent-call capacity or global GPU scheduler.

Admission fails immediately when full; no unbounded list of waiting coroutines
is introduced. Cancelling a running coroutine does not release its native job's
slot early. Cancelled queued jobs keep their slots until dequeued, at which
point their functions are skipped. Repeated interruptions therefore cannot
grow an abandoned queue behind a long-running model call.

Cache reads have their own worker pool, so a cache hit need not wait for local
generation or an HTTP request. Worker timings continue to use the priority 2
telemetry. Native work still cannot be forcibly aborted: a hung local operation
can occupy its worker until restart. This milestone bounds admission, not the
execution time of arbitrary native model code.

WebSocket ASR/TTS overload sends a busy status and leaves the connection ready
for a later turn. An unanswered question stays unconfirmed. Metrics label the
response `overloaded`, excluding it from completed-response percentiles. The
router retains its existing fallback when LLM routing is unavailable.

## CUDA deployment

The TTS sidecar loads its multilingual model at ASGI startup on the same single
worker used for generation. Its HTTP event loop remains available during
synthesis. A full queue returns HTTP 503 with `Retry-After: 1`; the client maps
429/503 to overload without an automatic retry storm. A missing reference is
refused instead of using the model's default speaker.

Cached and live configured model names must match (repository prefixes may
differ). Health checks require ASR and TTS; an available script cache alone is
insufficient for novel replies. The sidecar advertises its model and readiness.
Legacy health endpoints without those fields remain compatible but cannot
verify the loaded model. Generated WAVs must be nonempty, mono PCM16 at the
requested rate. TTS failures now propagate instead of masquerading as successful
empty audio.

Rebuild the TTS image: `Dockerfile.tts` now copies the shared bounded-worker
implementation. Run one TTS server process per intended model replica; multiple
server workers each load their own copy and multiply the memory requirement.
External vLLM services retain their own queue limits and scheduling policies.

## Cache warming and validation

Mac console warming reuses the resident model on its owned worker and checks
for live calls before each line. A warm operation already running can finish
after a call starts; it is not preemptible. CUDA console warming directs the
operator to build and copy the cache offline. Avoid running separate cache-build
processes alongside the live model when measuring memory or response latency.

Regression coverage uses controlled workers and fake model/HTTP services to
check cancellation storms, cache reads behind busy inference, startup ownership,
resident cache misses, missing-model refusal, responsive sidecar health and
overload. These tests require neither MLX nor a GPU. Use the
[measurement guide](latency-measurement.md) for actual cold/warm, cache hit/miss
and first-answer comparisons. True incremental synthesis and AudioWorklet are
the next milestone.

Validation on this branch: the full Python run produced 764 passes, one skip
and one obsolete source-text assertion. That assertion was replaced with a
runtime check of the single-attempt budget. After that correction and the final
overload/rate checks, all 52 focused tests passed. The 12 browser tests and all
31 transcript replay expectations also passed. No target-device synthesis or
container build was executed in this environment.
