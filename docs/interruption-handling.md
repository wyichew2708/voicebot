# Interruptible voice responses

Implemented on `enhancement/realtime-voicebot` as the first resource-efficiency milestone.

## Behavior

The socket keeps accepting microphone and control messages during ASR, routing,
synthesis and playback. Confirmed barge-in stops local playback immediately and
invalidates the old response. Late audio, transcripts and end markers from that
response are ignored by the console. Typed input also replaces the current response.

One response owner serializes call-state updates. A replacement waits until the
previous response coroutine releases ownership. Confirmed facts, consent decisions
and already emitted tool events are retained. An interrupted question remains
outstanding; a bare yes/no or repeat request causes it to be asked again rather
than assuming that the caller heard it. Explicit do-not-call requests retain priority.

The new console acknowledges each completed audio utterance. The engine does not
advance past that utterance, or close the call over the final sentence, until it
receives the matching acknowledgement. This is a browser playback signal, not proof
that a human understood the words or that a physical speaker was audible.

## Ownership and limits

- `src/voicebot/realtime.py`: a dedicated writer, a serialized response owner,
  one replaceable pending request and a 32-frame output queue.
- `src/voicebot/server.py`: audio framing, response cancellation, playback waits,
  audio/transcript gates and per-connection lifecycle.
- `src/voicebot/call/engine.py`: tracks spawned routing/synthesis tasks for cleanup
  and preserves an interrupted question without reverting committed state.
- `ui/realtime-audio.js`: per-request and per-generation fences, sequence checking
  and acknowledgement identity. The console handles the actual audio devices.

Mic input is capped at 30 seconds per utterance. Overflow discards that utterance
until its boundary; the next turn can proceed. Agent output is capped at 120 seconds
per utterance. A socket write has a five-second timeout. Missing playback completion
fails the connection after audio duration plus 15 seconds; it never counts as
successful delivery. UI-only status notifications are best effort and cannot block
microphone reception behind a full output queue.

Cancelling an asyncio await **does not stop an already executing native GPU kernel,
executor thread or remote HTTP inference**. Pending coroutine work is cancelled and
stale results cannot re-enter the conversation. Existing backend workers may remain
occupied until inference returns. Backend scheduling and cooperative model-level
abort are subsequent work; no immediate GPU-memory release is claimed here.

An asynchronous backend must propagate cancellation after bounded cleanup. If it
delays cancellation, the response owner deliberately waits instead of allowing two
coroutines to mutate the same session. A backend that suppresses cancellation
indefinitely can also delay connection teardown and is not supported.

## WebSocket audio protocol 2

The console sends `audio_protocol: 2` on `start`. It increments `client_turn` on
start, barge-in, typed input, utterance end and hangup. A local request clears
playback immediately, before waiting for the server. Responses echo that request
number, so an audio-begin message already in flight cannot reopen an obsolete stream.

Server generation IDs increase on replacement or cancellation. Audio IDs identify
individual utterances within a response; sequence numbers start at zero for each.

| Message | Fields / meaning |
|---|---|
| `audio_cancel` | `generation`, `client_turn`, `reason`; clears matching local output |
| `audio_begin` | `generation`, `client_turn`, `audio_id`, `audio_protocol`, `sample_rate` |
| Binary audio | 12-byte header: little-endian uint32 generation, audio ID, sequence; then mono little-endian PCM16 |
| `audio_end` | `generation`, `client_turn`, `audio_id`; closes production, not playback |
| `playback_done` from client | Matching generation and audio ID, only after final scheduled audio finishes |
| `response_done` | Generation/client turn; the response has finished processing and playback |

The microphone's binary input remains raw PCM16 at the configured sample rate.
The protocol module rejects stale, duplicate and mismatched output frames.
Call records include generation-tagged transcripts, interruptions and playback
acknowledgements. `client_stop_ms` on barge-in measures JavaScript stop-dispatch
time only; it excludes VAD detection delay, device buffering and sound propagation.

Legacy clients omitting `audio_protocol: 2` still receive unprefixed PCM and do
not wait for playback acknowledgements. Deploy the updated console and server
together to obtain the full interruption guarantees. Existing VAD thresholds,
model selection and whole-utterance synthesis are otherwise unchanged.

## Verification

Run the Python suite and the JavaScript protocol/socket-handler suite:

```bash
python -m pytest -q
node --test tests/realtime-audio.test.cjs
python scripts/eval.py
```

New tests use barriers to interrupt ASR, routing, TTS and playback, including before
the first audio frame. They cover disconnects, hangup, oversized mic input, queued
stale output, delayed backend cancellation, real ASGI WebSocket negotiation,
unheard consent, preserved identity, immediate post-playback replies, and browser
acknowledgement races. No GPU or Mac timing is inferred from mock-backend delays.

Before a hardware release, exercise speakerphone/headset barge-in, interrupted
names and email readbacks, background-tab playback and a blocked audio device.
Measure speech onset to audible stop and subsequent meaningful-answer latency on
each target device. AudioWorklet, neural VAD, true incremental TTS and admission
control remain later milestones.

Review results (2026-09-08): full Python regression run, 733 passed / one skipped;
then 129 focused realtime/script/compliance checks passed after adding the final
startup identity-race guard (15 of these are realtime tests). Eight JavaScript
protocol/console-handler tests passed. Recorded-call replay remained 31/31
expectations across 270 caller turns, with the same keyword/handoff/clarification
breakdown as the baseline. These runs used Linux and mock inference, not physical
Mac/GPU audio hardware.
