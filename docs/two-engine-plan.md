# Duplex or Cascade — Two Switchable Engines

Companion to [architecture-research.md](architecture-research.md).
Artifact: https://claude.ai/code/artifact/874125bd-22cf-45cc-b53a-604b3ec40677

---

## 1. Correction: StepAudio 2.5 Realtime is not self-hostable

No open weights found. It's a hosted WebSocket service (`wss://api.stepfun.com/v1/realtime`,
model `step-2.5-realtime`). StepFun *does* publish open weights for **Step-Audio-2-mini**
(Apache 2.0) with vLLM streaming and native tool calling — but that's the 2-series, not 2.5.

- **A1 (recommended):** Step-Audio-2-mini self-hosted. Apache 2.0, on-prem, tool calling,
  langs EN/ZH/Cantonese/JA/AR.
- **A2:** StepAudio 2.5 Realtime API. Better model, but hosted only.

⚠ **A2 is a PDPA question.** Streaming live call audio to an overseas API transfers customer
personal data out of Singapore — the Transfer Limitation Obligation requires comparable protection,
and insurance calls carry health and financial detail. Legal review, not a config flag. It also
defeats the reason local GPU was specified. **Plan below assumes A1**; the interface hides the choice.

## 2. Do you actually need to swap?

Qwen3.6 runs permanently in its own reserved GPU memory, outside this budget. That removes 21–37 GB
from the problem — and the remaining voice stack probably fits **both engines at once**.

| Component | Needed by | VRAM |
|---|---|---|
| MERaLiON-3-3B-ASR | both (B's ASR, A's shadow ASR) | ~8 GB |
| Turn detector + pVAD | both | ~0.5 GB |
| CosyVoice3 + Malay TTS | B | ~6 GB |
| Step-Audio-2-mini | A | ~16–20 GB **(unverified)** |
| **Both resident** | | **~30.5–34.5 GB** |
| Headroom for KV & concurrency | | 5.5–9.5 GB |

✅ **Most likely you don't need the swap.** Both engines fit in 40 GB, which brings back **per-call
language routing** and clean hash-split A/B, with zero swap machinery. Materially better than the
segmented-queue design, and free if the numbers hold.

Everything turns on one unmeasured value: **Step-Audio-2-mini's actual footprint.** StepFun publishes
no memory figures; 16–20 GB is an estimate from comparable audio LLMs.

| Step-Audio measures | Total | Design |
|---|---|---|
| ≤ 20 GB | ≤ 34.5 GB | **Both resident.** Per-call routing, hash-split A/B, no swap code. |
| 20–25 GB | 34.5–39.5 GB | Both resident but thin. Cap concurrency or quantize the TTS pair. |
| > 25 GB | > 40 GB | Fall back to the swap design below. |

Enable `--enable-sleep-mode` on both regardless — free at startup, and it's the difference between a
5 s contingency and a 90 s one.

### If both fit (expected): switch is a routing decision

```yaml
engine:
  default: cascade
  mode: per_call
  routing:
    - when: contact.language in [en, zh]
            and contact.singlish_score < 0.4
            and not contact.multilingual_flag
      use: duplex
    - when: always
      use: cascade
  ab_test:
    split_by: hash(contact.id)   # stable per contact, not per call
    duplex_share: 0.2
```

Hash the **contact**, not the call — someone dialled twice should get the same experience twice.

### Fallback: if only one fits, use vLLM Sleep Mode

Don't kill and restart processes (30–100+ s). Sleep Mode's documented use case is exactly two models
that each fit but not both at once. **Level 1** offloads weights to CPU RAM, discards KV, wakes a
large model in **~3–6 s** — 18–200x faster than reload. Needs 10–100 GB host RAM per model, so spec
**64 GB+**. Level 2 avoids the RAM but adds `reload_weights` + `reset_prefix_cache`; not worth it.

```bash
VLLM_SERVER_DEV_MODE=1 vllm serve step-audio-2-mini --enable-sleep-mode

1. dialler.pause()
2. await active_calls.drain()
3. POST engineB/sleep?level=1     # MUST come first
4. POST engineA/wake_up           # ~3-6 s
5. await warmup_inference()
6. dialler.resume(engine=duplex)
```

⚠ **Never wake before sleeping** — both weight sets resident busts the budget and the OOM lands on
whichever engine is mid-call. Serialise behind a lock; assert `/is_sleeping` before waking.

⚠ **Per-call routing dies on this path.** Interleaved dial lists would swap every call. You'd sort the
queue by engine and swap once per block (`min_batch` ~50): dial the EN/ZH block in the morning, swap
once, dial the rest after. Main reason to hope the measurement comes in low.

## 3. Chassis vs engine (~80% shared)

| Shared — written once | Per-engine |
|---|---|
| SIP transport & SG trunk | audio-in → audio-out impl |
| Dialler, DNC + consent gate (21-day TTL, fail-closed) | turn-taking & barge-in mechanics |
| Call state machine (greet→disclose→qualify→book) | tool-call adapter |
| Tool definitions (CRM, DNC, calendar) | voice/persona config |
| Pre-rendered scripted audio | language coverage & fallback |
| Recording, transcripts, audit trail | shadow ASR (Engine A only) |
| Advice-refusal classifier (MAS boundary) | — |
| Metrics & eval harness | — |

```python
class VoiceEngine(Protocol):
    async def push_audio(self, frame: bytes) -> None: ...
    def audio_out(self) -> AsyncIterator[bytes]: ...
    def events(self) -> AsyncIterator[Event]: ...
    #   Transcript(text, lang, is_final, speaker)   <- compliance record
    #   UserStartedSpeaking / UserStoppedSpeaking   <- barge-in policy
    #   ToolCall(name, args) -> ToolResult          <- CRM, DNC, calendar
    #   TurnComplete(reason)                        <- state machine advance
    async def interrupt(self) -> None: ...
    async def say_prerendered(self, clip: str) -> None: ...
    async def set_context(self, ctx: CallContext) -> None: ...
```

**No `transcribe()` or `synthesize()`** — those are cascade concepts; leaking them makes the duplex
engine pretend to be a cascade and lose the property you wanted.

## 4. The two engines

**Engine A · duplex — Step-Audio-2-mini.** audio→model→audio. EN/ZH + code-switch between them.
vLLM fork, `--max-model-len 16384`. Native tool calling. Needs shadow ASR. For EN/ZH contacts.

**Engine B · cascade — MERaLiON v3 → Qwen3.6 → TTS.** Full coverage incl. Singlish, Malay,
Hokkien/Cantonese. Reuses your Qwen3.6. Native vLLM function calling. Needs the duplex behaviour layer.

**Engine B is the default, not the fallback.** It's the only version that completes a call with a
caller who switches into Malay — normal here, not an edge case. Engine A is an *optimisation for a
known-monolingual subset*. Name it that way in code.

## 5. Four parity problems

1. **Engine A has no transcript and you legally need one.** S2S emits audio, no intermediate text →
   no compliance record, no QA, no dispute evidence, nothing for the advice-refusal classifier. Run
   **MERaLiON as shadow ASR in parallel**, off the critical path. Budget the VRAM — this is the hidden
   cost of the duplex path.
2. **Tool calling shapes differ.** Define tools once in a neutral schema, thin adapter per engine.
   Two definitions will drift, and the drifting one is the less-tested one.
3. **The engines don't sound like the same company.** Pin persona/voice per engine and tune toward
   each other — same greeting wording, similar pace and register.
4. ⚠ **Never switch engines mid-call.** Voice changes mid-sentence, state must migrate between
   incompatible representations, and the caller hears the seam exactly when they were least understood.
   Instead: Engine A detects out-of-language speech → normal escalation path (apologise, offer callback
   or human transfer, log reason) → mark contact multilingual → Engine B next time. Routing learns;
   the live call doesn't experiment.

## 6. Making Engine B feel like Engine A

Build the duplex behaviour layer **in the chassis**, so both engines share barge-in policy and tuning:

- **Preemptive generation** — LiveKit Agents ships it on by default (86% precision / 100% recall
  adaptive interruption). Generate on partial transcript, discard if the caller resumes.
- **Personalised VAD** — suppresses noise and competing speakers; biggest source of false barge-ins.
- **Clause-level TTS chunking** — emit at first speakable fragment, not first sentence.
- **Pre-rendered scripted turns** — zero inference; on a qualification call that's most turns, and it
  makes the cascade feel *faster* than duplex on the moments callers judge first.
- **Backchannels** — short acknowledgements from the VAD signal alone. Cheap, disproportionately effective.

Read **FireRedChat** (pluggable full-duplex, cascaded + semi-cascaded) and **DuplexCascade**
(VAD-free cascaded ASR-LLM-TTS with micro-turn optimisation) first.

## 7. The voice budget without Qwen

| Configuration | VRAM | Headroom | Verdict |
|---|---|---|---|
| Engine B alone (cascade — the version that must ship) | ~14.5 GB | 25.5 GB | very comfortable |
| Engine A alone (duplex + shadow ASR) | ~26.5 GB | 13.5 GB | comfortable |
| **Both resident** | **~30.5–34.5 GB** | 5.5–9.5 GB | fits at estimated sizes — the target |

Engine B at ~14.5 GB is the headline: the version that must ship uses barely a third of the budget.
Engine A can be added later as a pure addition rather than a trade.

The duplex engine is **not cheaper** — it folds ASR/LLM/TTS into one model then needs shadow ASR back
for the compliance transcript, so you pay for transcription twice. Its advantage is latency, not memory.

The 5.5–9.5 GB left over isn't slack — it's your KV cache and concurrency budget. Pre-rendered scripted
turns (§6) reduce how much you need, since those calls hold no model state.

## 8. Decision metrics — agree before either engine is finished

| Metric | Why it decides something | Likely winner |
|---|---|---|
| Voice-to-voice p50/p95 | felt speed | A |
| Barge-in precision/recall | stops when interrupted, ignores noise | A |
| WER on code-switched turns | the Singapore case — score separately or it hides | B |
| Task completion rate | appointments booked — the business metric | ? |
| Early-hangup rate | callers leaving in first 20 s | ? |
| Tool-call accuracy | right record, right slot, no hallucinated policy | B |
| Compliance violations | advice given, disclosure skipped — must be zero | B |
| Cost per completed call | GPU-seconds vs outcomes | ? |

Fixed recorded scenarios first (incl. deliberately code-switched and noisy) — offline scoring needs no
swap: run the set through whichever engine is awake, swap once, run the other.
**Full-Duplex-Bench v1.5 / v3** give comparable published numbers for the conversational half.

**One trap survives whichever configuration you land on.** With both engines resident you get clean
per-call randomisation by contact hash. But one confound is architectural, not a swapping artifact:

- **Population confound.** Engine A only ever handles monolingual EN/ZH contacts — the only ones it can
  serve. Engine B handles everyone. Comparing raw completion rates measures *which contacts are easier*,
  not which engine is better, and it will flatter Engine A enough to make you ship it. Restrict the
  comparison to **Engine-A-eligible contacts only**, randomise within that pool, report B's numbers on
  the ineligible remainder separately. Never pool them.
- **If you land on the swap fallback**, add a second control: the comparison becomes time-blocked, so
  alternate block order across days or morning/afternoon differences load onto one engine.

## 9. Build order

1. **Chassis with a fake engine** — SIP leg, DNC/consent gate, state machine, tools, recording, audit,
   driven by a stub `VoiceEngine` playing clips. Proves compliance path + SG trunk before any model.
   The +65 caller-ID problem surfaces here.
2. **Engine B to working** — the version that must ship, so it goes first. Resolve the Malay TTS
   licence in parallel; it gates this engine.
3. **Full-duplex behaviour layer** — in the chassis, benefits both. Measure against Full-Duplex-Bench.
   Most of the perceived gap closes here, before Engine A exists.
4. **Engine A behind the same interface** — Step-Audio-2-mini + shadow ASR. If the interface was drawn
   right this is additive and touches no chassis code; if not, this is the cheapest place to find out.
5. **Routing, A/B, decision** — per-call routing by contact language, hash-split A/B on the
   eligible-contact design, §8 metrics to significance. Build swap machinery here *only* if Step-Audio
   measured too large in Step 4 — otherwise this step is a config file.

## 10. Unknowns that could move this plan

- **Step-Audio-2-mini param count / VRAM unverified — now the single load-bearing unknown.** Repo
  documents vLLM + Apache 2.0 but no memory figures. ≤20 GB → both resident, §2 is a config file.
  >25 GB → swap subsystem and you lose per-call routing. Measure it first.
- **Its Singlish behaviour is untested** — handles EN and ZH incl. code-switch between them, but nothing
  published covers Singapore English. Test before routing any real contact to Engine A.
- **StepAudio 2.5's release model may change** — if open weights appear, Engine A swaps inside one adapter.
- **Step-Audio tool-calling semantics not specified in detail** in reachable docs. Budget adapter time.
- **Malay TTS licence still unresolved** and gates Engine B, the engine that must ship. Highest-priority
  open item in the programme.
