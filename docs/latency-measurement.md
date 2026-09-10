# Voice response measurements

Priority 2 of the enhancement plan is implemented. This instruments the existing
pipeline; it does not establish a speed result on Mac or CUDA hardware.

## What is measured

The console sends one `playback_started` message per utterance, fenced by the
current generation and audio ID. The server assigns its role: `opening`,
`acknowledgement`, `clarification` or `answer`. Standalone thinking phrases and
language bridges are acknowledgements. A clip combining a bridge and an answer
is answer-bearing audio; the measurement does not locate its first substantive word.

For microphone turns, first-audio latency starts at the last voiced capture
callback using `performance.now()`. It includes the endpoint wait, request,
inference and scheduled playback. The last callback is an endpoint estimate,
not an acoustic annotation of when the speaker stopped. Typed requests and
opening greetings start at request time and are reported separately.

Web Audio prefers the device-time estimate from
[`AudioContext.getOutputTimestamp()`](https://developer.mozilla.org/en-US/docs/Web/API/AudioContext/getOutputTimestamp).
It maps the scheduled source start onto the browser's performance clock. When
unavailable, it estimates from `currentTime` and the scheduled start. Media
fallback uses the browser's `playing` event. The method is stored with each
sample. Timers discard playback cancelled before its scheduled start. These
are browser estimates, not microphone loopback measurements of the speakers.
Missing, invalid, duplicate and stale samples cannot become zero latency.

The console's answer statistic uses the first answer-bearing playback sample
for each microphone response. Acknowledgements appear separately. Transcript
generation times remain labelled as server times.

The server records `response_metrics` (schema version 1) in the existing call
log when a response finishes or is interrupted. It includes:

- Profile, configured model IDs, selected voice, language, input type and
  operator-declared model state (`cold`, `warm` or `unknown`).
- Response-owner queue time, total server response time, and audio-ready offsets.
- ASR, LLM and TTS wall times; executor queue/run time where instrumented.
- Actual TTS source, cache use and generated audio duration. Service real-time
  factor is TTS wall seconds divided by output audio seconds; cached audio is
  excluded. Queueing and HTTP/service wait are included, so this is not GPU
  kernel speed.
- Each utterance's role, browser playback duration and measurement method.

Client durations use only the client clock. Server durations use only the server
clock. Do not subtract them to infer one-way network latency. Model operations
can overlap; do not add their durations or percentiles to reconstruct total
latency. Native inference can outlive cancellation; unfinished workers are
explicit, and late completion cannot rewrite a recorded snapshot.

## Repeatable local run

1. Record hardware, OS/browser, profile, model revisions, voice and cache
   contents alongside the run. Start with one call. Keep the same phrases,
   languages and voice for comparisons.
2. Start the real profile, for example `make mac` or `make rhel`. Warm the
   selected services before a warm run. Set `VOICEBOT_BENCHMARK_STATE=warm`
   only when verified; the variable is a label and changes no model behavior.
   A cold run requires restarting/resetting all relevant services and checking
   startup warmup has not already exercised them. Otherwise leave `unknown`.
3. Use the console microphone with browser audio enabled. Include repeated
   cached phrases and fresh dynamic phrases. Collect enough repetitions to
   interpret p95 (prefer at least 100 completed responses per condition).
   Also interrupt several responses and verify they are counted as interrupted.
4. End the call so its events are flushed to `logs/calls.jsonl`. For an isolated
   run, copy the newly appended call records to a separate JSONL file; do not
   remove existing call logs. Keep hardware/run notes with that file.
5. Generate the report:

   ```sh
   make latency-report
   .venv/bin/python scripts/latency_report.py path/to/run.jsonl --format json
   .venv/bin/python scripts/latency_report.py path/to/run.jsonl --format markdown
   ```

Reports group by profile, configured model IDs, declared model state, input,
cache use, language and voice. Cache state is `hit` when all completed TTS
sources are cached, `mixed` when cached and other sources occur, `miss` when
all known sources are uncached, and `none` when no TTS source is available.
Check individual operations when interpreting a mixed response.

Percentiles use nearest rank and completed responses/operations only. Counts
for errors, interruptions and missing answer playback remain visible. A
clarification-only response legitimately has no answer sample. JSON includes
stage statistics, clarification timing and model IDs; Markdown is a compact
playback comparison. Raw events retain per-utterance methods and worker details.

Historical transcript-only logs produce no inferred playback measurements.
Mock and typed runs test the pipeline but cannot establish target-hardware
voice performance. This milestone does not yet measure first model token,
true streaming TTS chunks, underruns, memory peaks or acoustic interruption
stop time. Use a real-device recording for perceptual and interruption checks.

## Regression checks

`make test-ui` checks browser packet fencing and playback measurements without
audio hardware. `tests/test_telemetry.py` checks clock separation, actual executor
queueing, cancellation snapshots and report exclusions. `tests/test_realtime.py`
checks socket correlation, duplicate samples and flushing on interrupted calls.
The ordinary Python suite and transcript replay remain the dialogue regression
gates; neither is a live model benchmark.
