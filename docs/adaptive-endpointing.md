# Local speech detection and adaptive pauses

Priority 5 software is implemented on `enhancement/realtime-voicebot`. It adds
Silero v5 speech classification in a dedicated browser worker, with ONNX Runtime
Web on one CPU thread. It requests no GPU execution provider. Model accuracy,
CPU load and memory on the target Mac/GPU host remain unmeasured.

## Enable locally

Run once from the repository:

```sh
make setup-vad
make check-vad
make run  # or your configured real-model profile
```

`setup-vad` downloads pinned npm archives, verifies their SHA-512 integrity,
extracts only the named model/runtime assets, and writes source/version and
SHA-256 records to `models/vad/manifest.json`. The model is about 2.3 MB and the
WASM binary about 14 MB; these are file sizes, not resident-memory estimates.
Generated assets are ignored by git. Install them separately on each server.
Do setup while calls are stopped. Once installed, inference uses local URLs and
needs no runtime CDN, external inference service, Python ML stack or GPU model.
The detector initializes when the microphone opens; its startup is separate
from subsequent turn latency. The microphone button shows preparation until
capture is ready; tapping again stops the stream immediately and closes any
worker that finishes starting afterward (startup timeout: ten seconds).
`check-vad` requires Node.js and exercises the real model and recurrent state on CPU; it is not an audio-accuracy benchmark.

The console shows the active detector. Missing assets or unsupported WASM use
the energy detector with the same adaptive pause policy. Neural inference is
limited to one active 32 ms frame plus four queued frames and one partial frame.
Overflow, worker errors or a one-second inference timeout terminate the worker,
discard the incomplete utterance and ask the caller to repeat using energy
fallback. Muting terminates the worker. AudioWorklet capture remains preferred.

## Pause behavior

The default **Balanced** setting uses these silence targets:

| Speech pattern | Target |
|---|---:|
| Clear speech of at least 300 ms | 450 ms |
| Short reply below 300 ms | 700 ms |
| Low signal or sustained uncertain classification | 850 ms |
| Speech resumes after at least 180 ms of silence | 1,100 ms |
| More than eight seconds of voiced speech, otherwise clear | 700 ms |

**Long pauses** uses 1,000 ms, extending to 1,400 ms after hesitation. Changes
apply to the next utterance. Timing accumulates captured audio duration instead
of wall-clock gaps, and rounds up to the next processed frame. Capture ends at
25 seconds plus pre-roll, below the server's 30-second buffer ceiling.

Neural onset requires probability ≥0.6 for at least 40 ms; continued voice uses
≥0.35. While the agent speaks, onset needs ≥0.7, the existing seven-times-room
energy threshold and 260 ms of sustained evidence. Energy fallback keeps room
calibration and sensitivity controls. The 320 ms pre-roll and browser echo
cancellation remain enabled. Onset frames are counted once, preserving short
replies without letting double-counted blips pass the 80 ms client gate.

These are heuristic silence targets, not semantic end-of-turn prediction. A
pause that exceeds the active target still ends a turn. VAD cannot distinguish
the caller from all background speech or residual speaker echo. Use Long pauses
for spelling or deliberate delivery; confirm behavior on representative audio.

## Measurement and release gate

Response traces record `speech_detector`, `endpoint_policy`,
`endpoint_target_ms`, `endpoint_reason` and the existing measured `endpoint_ms`.
Latency reports separate detector/policy groups; old logs retain `unknown`.
The target is not substituted for actual endpoint or audible-response latency.

Automated checks cover policy boundaries, short replies, hesitation, false
transients, pre-roll and interruption dwell, frame ordering/timestamps, bounded
inference queues, late results, local asset availability and telemetry. Run
`make test-ui`, `make test`, and `make eval` for regression checks.

Validation on 2026-09-10: 778 Python tests passed, one skipped (46 existing
dependency/API deprecation warnings); all 28 JavaScript checks passed. The real
CPU/WASM smoke test processed 32 recurrent frames successfully. Transcript
replay from this milestone met 31/31 expectations across 270 caller turns; it
used the keyword layer, not live speech models.

Chromium could not be installed in this review environment because its download
timed out. Browser WASM initialization and microphone behavior have therefore
not been verified end to end here.

The remaining completion gate is held-out audio and a sustained session on the
Mac and GPU deployment: English/Mandarin/Singlish, quiet speakers, names/digits,
spelling, background noise, speakerphone echo and interruptions. Compare false
cuts, missed short replies, endpoint p50/p95 and meaningful audible response
latency alongside CPU/RAM and playback gaps. Synthetic frames and transcript
replay do not establish these results. No measured speedup is claimed yet.

Next independent milestone: priority 6, compact-model routing and prompt
correction with held-out quality comparisons.

## Pinned implementation references

- [Silero VAD](https://github.com/snakers4/silero-vad): speech detector.
- [vad-web v0.0.30](https://www.npmjs.com/package/@ricky0123/vad-web/v/0.0.30): bundled v5 model and its 512-sample recurrent-state interface.
- [ONNX Runtime Web](https://onnxruntime.ai/docs/tutorials/web/env-flags-and-session-options.html): single-thread WASM and matching runtime assets; installed version 1.29.0.
