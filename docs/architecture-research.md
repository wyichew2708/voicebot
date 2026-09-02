# Singlish Voicebot — Architecture Research

Self-hosted outbound insurance voicebot for Singapore: English, Singlish, Malay, Mandarin.
Constraints: local GPU, <40 GB VRAM total, ~700 ms voice-to-voice.

Compiled 2026-08-31. Companion artifact: https://claude.ai/code/artifact/d387e436-daa7-432e-aea3-849ebde296d6

---

## 1. Verdict: cascaded, for language reasons — not viability

Qwen3-Omni is out on viability: the Talker (audio-producing half) is cloud-only via DashScope, the
self-hosted vLLM path serves the Thinker only, and Transformers runs ~146 s/turn.

**NVIDIA PersonaPlex-7B-v1 is a different matter** and beats this whole architecture on conversational
dynamics. It is genuinely self-hostable (~24 GB) and commercially licensed (NVIDIA Open Model License),
so "cascaded is the only viable self-hosted architecture" is no longer true in general — only for
this requirement set.

| Dimension | PersonaPlex-7B | This stack | Verdict |
|---|---|---|---|
| Turn-taking latency | **0.070 s** (Full-Duplex-Bench) | ~0.86 s midpoint | PersonaPlex by ~12x |
| Interruptions/overlap | Native, listens while speaking; score 1.000 | VAD barge-in bolted on | PersonaPlex |
| Languages | **English only** (Fisher, LibriTTS, synthetic EN) | EN/Singlish/MS/ZH + code-switch | ❌ disqualifying |
| Tool calling | None natively — no text interface, no turn boundary | Native via vLLM | ❌ disqualifying |
| Context window | 2048 tokens ≈ 164 s | 8K, whole call | ❌ forgets start of a 6-min call |
| Factual grounding | 7B on 1,840 h *synthetic* dialogue | 35B MoE + retrieval + scripted lines | This stack |
| VRAM | ~24 GB | 14.5–31 GB | comparable |

**Worth stealing anyway:** (1) 0.070 s is the bar — treat it as the endpointing target rather than
accepting 300 ms as inevitable. (2) PersonaPlex was trained on 1,840 h of synthetic customer-service
dialogue generated with Qwen-3-32B — you have a Qwen model on the server; the same technique generates
Singlish/Malay/Mandarin insurance dialogue for fine-tuning, the cheapest path to domain data in
languages nobody publishes corpora for.

NVIDIA says the architecture supports more languages (Spanish on the roadmap). If a SEA variant ships,
reopen this decision.

## 1b. The full-duplex landscape — and the Malay wall

**The models that do full-duplex don't speak Malay. The models that speak Malay don't do full-duplex.**
Not a gap awaiting the next release — full-duplex training needs large volumes of natural two-party
conversational audio, which exists for English and Mandarin and essentially nowhere else in your set.

| Model | Duplex | Speech output languages | Licence | Malay |
|---|---|---|---|---|
| PersonaPlex-7B (NVIDIA) | Full | English only | NVIDIA Open Model | ✕ |
| Moshi (Kyutai) | Full | English-primary | permissive | ✕ |
| Hertz-dev | Full | English, research-grade | — | ✕ |
| StepAudio 2.5 Realtime (StepFun) | Full, realtime | Chinese + English | Apache 2.0 | ✕ |
| Step-Audio 2 | Half | EN, ZH, AR, JA, Cantonese, Sichuanese | Apache 2.0 | ✕ |
| GLM-4-Voice (9B, ~18GB) | Half | ZH + EN code-switch | — | ✕ |
| Kimi-Audio | Half | ZH + EN | — | ✕ |
| Qwen3-Omni | Half, Talker cloud-only | 10 langs, no Malay | Apache 2.0 | ✕ |
| Qwen3.5-Omni | Half | 36 langs — *list unpublished* | Apache 2.0 | ? |
| SeaLLMs-Audio (DAMO) | audio→**text** | SEA incl. Malay | CC-BY-NC-SA ✕ | ◐ |
| MERaLiON v3 + MERaLiON-TTS | Streaming, not duplex | EN/Singlish/ZH/MS/TA, Hokkien TTS | MERaLiON Public | ✓ |

**Worth checking:** Qwen3.5-Omni claims 36 speech-output languages but publishes only 10. If Malay is
among the unlisted 26 it deserves a hard look — you're already on Qwen. Five-minute test.

**The one to watch:** **MERaLiON2-Omni** (alpha, A*STAR). MERaLiON-TTS already ships (incl.
Mandarin→Hokkien), and AudioLLM-3B-ASR is described as a lightweight real-time streaming model built
for deployment. They're the only lab building speech generation for exactly your language set.

### Build full-duplex as a *behaviour*, not an architecture

The experience you want isn't exclusive to end-to-end models:

- **FireRedChat** — pluggable full-duplex voice interaction, cascaded and semi-cascaded. Wraps any
  STT-LLM-TTS stack with VAD + turn detection + barge-in. Language-independent by design.
- **FlexDuo** — pluggable full-duplex module for existing dialogue systems, benchmarked vs Moshi and
  GLM-4-Voice.
- **LiveKit Agents v1.5.6** — shipped software: adaptive interruption at 86% precision / 100% recall,
  dynamic endpointing, **preemptive generation on by default**.
- **Personalized VAD (pVAD)** — suppresses background noise and competing speakers, cutting the false
  barge-ins that make cascades feel twitchy on a phone line.

Measure against **Full-Duplex-Bench v1.5** (overlap handling) and **v3** (tool use under disfluency) —
the same benchmarks PersonaPlex is scored on.

**On 0.070s vs 0.86s:** the gap is narrower than it reads. PersonaPlex's figure is *turn-taking
latency* on a benchmark, not a full response. A cascade with preemptive generation, clause-chunked
streaming TTS and pre-rendered scripted lines answers many turns in well under 200 ms — because it
started before you finished, or the answer was already on disk.

## 2. Recommended stack (~28.5 GB of 40 GB)

| Stage | Choice | VRAM | Why |
|---|---|---|---|
| Transport | LiveKit SIP or Pipecat + FreeSWITCH | — | must terminate on a Singapore-originated trunk |
| VAD | Silero VAD v5 | CPU | ~2 MB, drives barge-in |
| Turn-taking | LiveKit turn-detector (multilingual) | ~0.5 GB | semantic endpointing saves 200–400 ms |
| ASR | **MERaLiON-3-3B-ASR** (vs Polyglot-Lion-1.7B) | ~8 GB / ~3.5 GB | Singlish 12.5% WER, code-switch trained; 1.7B rival is 20x faster — see §4b |
| LLM | **Qwen3.6-35B-A3B** — reuse existing deployment | 21–37 GB | 3B active of 35B: decode ~4x a dense 32B. Footprint depends on quantization |
| TTS EN/ZH | Fun-CosyVoice3-0.5B | ~3 GB | 150 ms first packet, true streaming |
| TTS Malay | Malaysian-F5-TTS-v3 | ~3 GB | ⚠ **CC-BY-NC-4.0 — non-commercial**, see §5 |

### Reusing Qwen3.6-35B-A3B

Good fit for voice: only **3B of 35B params fire per token** (~4x a dense 32B on decode), and of 40
layers only 10 are full attention — the other 30 are Gated DeltaNet linear attention with
constant-size state, so KV cache grows slowly. Apache 2.0, 262K native context, has a vision encoder
you don't need.

Footprints: FP16 70 GB · FP8/Q8 ~37 GB · Q4_K_M ~21 GB.

**Open question — does the 40 GB cover the whole server, or just what the voicebot adds?**

- *Scenario A (shared 40 GB ceiling):* FP8 alone eats the budget. Must run Int4 (~21 GB) + swap to
  Polyglot-Lion-1.7B ASR (3.5 GB) + TTS 6 GB + turn 0.5 GB = **31 GB**, 9 GB headroom.
  With MERaLiON-3-3B instead: 35.5 GB, only 4.5 GB for KV. ⚠ One benchmark rejected AWQ-Int4 on this
  model for quality regression, and the 21 GB figure is GGUF `Q4_K_M` (llama.cpp — poor fit for
  concurrent low-latency serving). Validate Int4 quality on vLLM before committing.
- *Scenario B (Qwen already resident, budget is incremental):* voice stack = **14.5 GB**, 25.5 GB
  headroom. Run FP8 throughout, keep the 3B ASR. Preferred if infrastructure allows.

### Serve it with voice flags — reusing the model ≠ reusing the instance

- `--max-model-len 8192`, **not** the 262144 in Qwen's serving guidance. KV allocation scales with
  this; it's the single largest memory lever.
- `--language-model-only` — drops the unused vision encoder, frees memory for KV cache.
- **Prefix caching on** — persona/script/compliance prompt is byte-identical every turn.

⚠ **Shared-instance contention.** If other workloads hit the same vLLM server, voice p95 TTFT becomes
dead air on a live customer call. Give voice a dedicated replica or use priority scheduling. Monitor
p95, not the mean.

**Throughput is a non-issue:** ~28–30 tok/s single-user, ~156 tok/s aggregate at concurrency 32.
Speech consumes ~3.5 tok/s, so even the low figure is ~8x ahead of playback. Tune TTFT, not tok/s.

**If Malay dialogue disappoints:** Qwen-SEA-LION-v4 (Qwen3-32B base) ranks #1 among open models
<200B on SEA-HELM with specific Malay gains — but it's dense, so you trade A3B latency for quality.

## 3. Latency budget

| Stage | Budget |
|---|---|
| Network / telephony | 40–80 ms |
| **Semantic endpointing** | **200–350 ms** ← largest line, not GPU-bound |
| ASR | 150–300 ms |
| LLM TTFT (Qwen3.6-35B-A3B) | 80–150 ms |
| TTS TTFB | 120–150 ms |
| Jitter / playout | 40–60 ms |

Midpoint ≈ 860 ms — above target. Getting to 700 ms p50 requires:

- vLLM **prefix caching** of the system prompt (identical every call/turn)
- **Speculative LLM start** on the partial transcript, discard if the caller resumes
- **Chunk TTS at the first clause**, not the first sentence
- **Pre-render every scripted line** (greeting, disclosures, pitch, standard objections) to audio —
  those turns then cost zero inference, and most turns on a qualification call are scripted
- Keep all models on one box; each cross-service hop is 20–60 ms of pure waste

### ⚠ Biggest latency risk

MERaLiON's hosted ASR API reports ~600 ms first-token latency. If that carries to local serving, the
target is unreachable. **Benchmark this first.** Fallback if local 3B ASR >350 ms: two-tier — a fast
streaming recogniser (Voxtral-Mini-4B-Realtime or Whisper-large-v3-turbo) drives the interaction,
MERaLiON re-transcribes asynchronously to correct the record.

## 4. Why MERaLiON for ASR

Built by A*STAR I²R with IMDA support, trained substantially on IMDA's National Speech Corpus
(~10,600 h of Singaporean English incl. Singlish and mother-tongue languages).

`MERaLiON-3-3B-ASR` published WER: Singlish **12.52%**, Cantonese 11.27%, code-switching 22.65%,
Tamil 25.83%, Hokkien 46.50% (don't rely on it). Malay/Mandarin supported but not broken out.
The gen-2 ASR variant is reported 5–30% better than `whisper-large-v3`.

### 4b. The contender: Polyglot-Lion

`Qwen3-ASR-0.6B/1.7B` fine-tuned for Singapore's four official languages. The 1.7B reaches an average
error rate of **14.85** vs MERaLiON-2-10B-ASR's **14.32** (parity) while being **~20x faster**
(0.10 s/sample vs 2.02 s) and a fraction of the size. Whisper-large-v3-turbo: 33.04.

Per-language (1.7B): English 2.10–5.28 · Mandarin 1.45–8.00 CER · Malay 9.98–21.51 · Tamil 19.75–39.19.

That speed gap lands squarely on the biggest latency risk above, and frees ~4.5 GB.

⚠ **The catch:** the paper does **not evaluate code-switching** — the authors flag it as future work.
That's exactly what your callers do, and exactly what MERaLiON trains for. Its comparison is also
against MERaLiON-*2*-10B, not the 3B gen-3 recommended here. **Benchmark both on your own recordings,
scored specifically on code-switched utterances.**

## 5. ⚠ Unresolved: Malay TTS

A licensing/data problem, not a compute one.

- `mesolitica/Malaysian-F5-TTS-v3` is the only model doing MS + local EN + ZH code-switching in one
  voice (15,631 h) — but **CC-BY-NC-4.0, non-commercial**. Blocked for commercial use as-is.
- CosyVoice 3 / Kokoro / Orpheus have **no Malay**.
- Routing by language across two TTS models breaks voice identity *mid-sentence* — exactly when
  code-switching callers trigger it.

Options: (a) commercial licence from Mesolitica; (b) fine-tune CosyVoice 3 or F5-TTS on owned/licensed
Malay data for one cross-lingual voice — correct answer, multi-week; (c) cloud API for Malay only,
breaking the on-prem requirement. **Resolve before building — it can change the TTS engine for all
languages.**

## 6. Singapore constraints that change the architecture

| Constraint | Consequence |
|---|---|
| DNC "No Voice Call" register, check valid **21 days** | DNC state is a precondition gate with a TTL, not a nightly batch |
| **Relationship exemption does NOT cover voice** (text/fax only) | consent provenance stored per contact and auditable — most-missed rule |
| Penalties up to **S$1m** org / S$200k individual | fail closed: unknown DNC/consent ⇒ do not dial |
| Caller must identify self, org, purpose | first scripted turn mandatory, non-skippable |
| **MAS**: advice triggers FAA; Medisave products can't close by phone | scope to qualification + appointment-setting; enforce with an advice-refusal classifier, not a prompt instruction |
| Telcos block international calls showing +65 3/6/8/9 (since Dec 2022) | SIP trunk **must originate in Singapore** or calls never ring |
| No legal AI-disclosure mandate; IMDA GenAI Transparency Guidelines (20 Jul 2026) recommend an "AI Information Card" | disclose anyway |
| Recording + PDPA | consent line in opening script; retention policy on audio and transcripts |

## 7. Build order (risk retired per week)

1. **Week 1** — ASR bake-off: MERaLiON-3-3B-ASR vs Polyglot-Lion-1.7B on vLLM. First-token latency on
   3–6 s utterances, WER on *your own* recordings, scored separately on code-switched utterances.
   Decides both the latency budget and 4.5 GB of the memory budget.
1b. **Week 1** — Re-serve Qwen3.6 with voice flags (`--max-model-len 8192`, `--language-model-only`,
   prefix caching). Measure p95 TTFT under real co-tenancy; decide dedicated replica vs priority scheduling.
2. **Week 1 (parallel)** — Resolve the Malay TTS licence with Mesolitica.
3. **Week 2** — Prove the loop end-to-end over a Singapore SIP trunk with a deliberately trivial
   script. Confirm calls actually connect (the +65 caller-ID issue surfaces here).
4. **Week 3** — Tune turn-taking on real bilingual callers. Instrument barge-in, false-endpoint rate,
   dead air. Expect more time here than on the models.
5. **Week 4+** — Compliance gates (DNC state machine, consent provenance, advice-refusal classifier,
   recording/retention), then dialogue quality.

## 8. Not verified — check before relying on

- **MERaLiON Public Licence commercial terms** — bespoke licence, text not retrieved. The whole ASR
  recommendation depends on commercial use being permitted. Read it.
- **VRAM figures are estimates**, from parameter counts and typical quantized overheads, not measured.
- **Latency numbers are vendor claims**, not independently measured.
- **Malay/Mandarin WER for MERaLiON-3 not published separately.**
- **Qwen3.6 Int4 quality on vLLM unverified** — 21 GB figure is GGUF `Q4_K_M`; AWQ-Int4 was rejected
  for quality regression in one published benchmark. Scenario A rests on an unvalidated Int4 path.
- **Polyglot-Lion code-switching unmeasured**, and compared against MERaLiON-2-10B rather than the
  gen-3 3B model. The two are not measured on the same footing.
- **Qwen3.6 throughput figures are from a DGX Spark GB10** (memory-bandwidth bound); datacenter GPUs
  should do better but I have no measurement.
- **Concurrency (10–25 calls/card) is inferred**, not load-tested.
- **Regulatory reading is not legal advice** — confirm the DNC voice distinction and the MAS advice
  boundary with Singapore counsel before dialling a real number.
