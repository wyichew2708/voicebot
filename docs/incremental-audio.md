# Incremental audio and AudioWorklet

Priority 4 adds incremental segment delivery on the supported Mac voice path
and AudioWorklet capture/playback for all profiles. It does not change the
chosen models or claim a measured target-device latency improvement.

## Supported synthesis paths

| Path | Behavior |
|---|---|
| Mac Kokoro, without a cloning-cache model | The opening, generated replies and ordinary script turns forward generated segments before later segments finish. |
| Mac cloning-cache profiles | Cache reads and whole-clip cloning retain the same reference, pitch and rate processing. Existing response-part buffering remains. |
| CUDA Chatterbox | The current service returns a complete waveform. It gains bounded browser playback, but is not presented as incremental model synthesis. |
| Slow-down/repeat paths | Retain whole-clip processing where needed for pitch-preserving time stretching and existing repeat behavior. |

The Kokoro adapter consumes the model generator on its owned worker. It uses
the existing speech chunker to insert segment boundaries without splitting
emails, abbreviations or numbers arbitrarily. Each segment becomes PCM16 frames
immediately; the adapter no longer joins every model segment before returning
the first one. This is **incremental segment synthesis**, not token-level audio
decoding. A short reply with only one segment still waits for that segment.

The interface was checked against the `mlx-audio` 0.5.3 wheel and the upstream
[Kokoro generator](https://github.com/Blaizzy/mlx-audio/blob/main/mlx_audio/tts/models/kokoro/kokoro.py).
The current [Chatterbox multilingual API](https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/mtl_tts.py)
returns a waveform; wrapping it in an HTTP streaming response would not make
the model incremental. A streaming CUDA model/adapter remains future work.

`streaming_tts` advertises the capability. `stream_speak` yields `SpeechChunk`
objects, followed by an explicit final marker. No lookahead delays the first
chunk merely to discover whether it was the last one. The engine closes nested
iterators on cancellation. Empty output and model errors cannot create a
successful final marker.

The synchronous-to-async bridge holds at most eight 20 ms PCM frames, plus one
producer frame. Backpressure pauses the producer; cancellation wakes it and
closes the model iterator on its owner. Native work already inside a model call
still cannot be forcibly interrupted. The model may itself hold a whole segment
in memory. Buffered response planning now prefetches only one response part
ahead instead of submitting every part to the bounded inference queue.

## Browser audio

The console loads `/voice-worklet.js` before starting a call. On supported
browsers the worklet captures/resamples microphone audio into exact 320-sample,
16 kHz frames (20 ms). Fractional resampling state survives render callbacks,
including 44.1 kHz input. Capture produces silent output, preserving browser
echo cancellation without local microphone monitoring.

Playback uses a bounded PCM ring, an 80 ms initial buffer, and continuous
sample-rate conversion to the device clock. It reports source samples consumed,
the first rendered sample, underrun duration and final completion. The
processor handles the actual callback array length rather than assuming a
fixed quantum, as specified by the
[AudioWorklet process API](https://developer.mozilla.org/en-US/docs/Web/API/AudioWorkletProcessor/process).

The main thread still handles WebSocket messages, VAD decisions and UI updates.
Capture permits at most eight unacknowledged frames (160 ms). If it cannot keep
up, capture stops with an explicit error instead of silently dropping speech.
Microphone upload also stops when queued socket data exceeds 128 KiB. Muting
the microphone keeps the shared output context alive so agent speech continues.

AudioWorklet needs a browser context that supports it (normally HTTPS or
localhost). If unavailable, the console keeps its existing ScriptProcessor and
scheduled-buffer/media compatibility paths. Those paths do not negotiate the
new two-second playback window. A failed negotiated worklet fails playback
explicitly; it does not silently switch to a mode unable to return credits.

## Playback credits and completion

Requests with an initialized, running worklet send `audio_flow: true`. The
server echoes `flow_control` in `audio_begin`, retaining binary audio protocol
version 2 and its generation/audio-ID/sequence header.

`audio_consumed` reports a monotonic cumulative count of source samples for the
current generation and audio ID. The server allows only two seconds of samples
ahead of consumption. Duplicate, negative, fractional, stale or beyond-sent
credits are ignored. A stalled client times out after 15 seconds waiting for
credits; mic/control reception and barge-in remain live while delivery waits.

The worklet ring has 2.1 seconds of capacity. Credits are reported approximately
every 50 ms and on final drain. `audio_end` means transmission ended; it does
not acknowledge playback. Only worklet completion after consuming the final
samples sends `playback_done`. For flow-controlled audio the server also checks
that all sent samples were consumed. Cancellation clears the ring and fences
late messages using the existing generation/audio IDs.

The operator replay feature still retains a copy of the utterance, bounded by
the server's existing 120-second output limit. The two-second window bounds
live playout; it is not a claim that no other audio copy exists.

## Measurements and validation

Streaming operations record `first_chunk_ms` and total audio duration in the
priority 2 trace. Their wall time includes downstream backpressure and is not
reported as a model service RTF. `playback_done.underrun_ms` records post-start
output starvation measured by the worklet. Browser first-playback measurements
remain estimates, not acoustic loopback tests.

Run `make test-ui` for worklet resampling, bounded buffering, cancellation and
the console's actual completion wiring. Python tests cover early incremental
delivery, bounded production, producer failure, owned-thread cleanup, credit
validation and interruption while blocked on credits. Existing chunking tests
retain checks for emails, abbreviations, Mandarin joins and slow-device fallback.

For hardware validation, run `make mac` with the Kokoro profile, then a cloning
profile and the CUDA deployment. Include long replies, short replies, pause/
resume, interruptions during synthesis, mic mute/unmute, audio permission
failure, and a busy UI. Use the [measurement guide](latency-measurement.md) and
inspect underrun events alongside answer latency. Listen for join artifacts and
resampling quality. A sustained target-browser run and a streaming CUDA adapter
are still required before claiming the full priority 4 hardware gate is met.

Local validation: 774 Python tests passed with one skip in the full run. A final
flow-controlled completion test also passed, along with 54 focused socket/UI
checks after the replay-buffer change. All 17 Node browser/worklet tests and
31 transcript replay expectations passed. The console script passed syntax
validation after the final startup-cancellation guards. These are model stubs
and simulated audio quanta; no physical microphone/speaker, MLX/GPU inference
benchmark or sustained real-browser session was run here.
