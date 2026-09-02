# Two Deployments — MacBook and RHEL GPU Server

Artifacts: [plan](https://claude.ai/code/artifact/afff499d-f9f6-4b82-9d2b-0f7e3098bd15) ·
[demo console prototype](https://claude.ai/code/artifact/be4fecd6-c36f-4787-a2e6-6ba274274a96)
UI source: [ui/demo-console.html](../ui/demo-console.html)

---

## 1. Holding Malay — what changes

Parking Malay **speech output** drops `Malaysian-F5-TTS-v3` and with it the CC-BY-NC-4.0 blocker —
the programme's highest-priority open item since the first research pass. Frees ~3 GB and removes the
cross-lingual voice-consistency problem entirely (one TTS engine covers everything you speak).

| Capability | Before | Now |
|---|---|---|
| Speech output | EN, Singlish, MS, ZH | EN, Singlish, ZH |
| Speech **understanding** | EN, Singlish, MS, ZH, TA | **unchanged** — MERaLiON still hears Malay |
| TTS engines | CosyVoice3 + Malaysian-F5 | CosyVoice3 alone (Kokoro on Mac) |
| Unresolved licences | CC-BY-NC blocker | **none** |
| TTS VRAM | ~6 GB | ~3 GB |

✅ **Keep Malay understanding on.** It costs nothing extra. When a caller answers in Malay the bot
should *recognise* it and respond in English offering transfer to a Malay-speaking colleague — better
demo moment than pretending Malay doesn't exist, and the day TTS is unblocked you're adding a voice to
a path that already works.

## 2. One codebase, two runtime profiles

```python
# runtime/__init__.py
def load_backend(profile: str) -> Backend:
    if profile == "mlx":  return MLXBackend()    # Apple Silicon
    if profile == "cuda": return CUDABackend()   # RHEL + NVIDIA

class Backend(Protocol):
    async def transcribe(self, audio: bytes) -> Transcript: ...
    async def complete(self, msgs, tools) -> Completion: ...
    def       synthesize(self, text, voice) -> AsyncIterator[bytes]: ...
    def       health(self) -> BackendHealth: ...   # surfaced in the UI
```

Both expose OpenAI-compatible HTTP (`mlx_audio.server` on Mac, vLLM on RHEL), so even transport is
shared and the backend class is mostly config.

## 3. Target A — MacBook (MLX)

| Component | Choice | Memory | Notes |
|---|---|---|---|
| LLM | Qwen3.6-35B-A3B MLX 4-bit | ~20 GB | 32 tok/s M4 Pro · 44 M4 Max · 52 M5 Max |
| ASR | Polyglot-Lion-1.7B via mlx-audio | ~2–4 GB | Qwen3-ASR family, which mlx-audio supports |
| TTS | Kokoro (mlx-audio) | ~0.5 GB | EN + Mandarin, streaming, faster than realtime |
| VAD + turn | Silero + turn detector | ~0.5 GB | CPU |
| **Total** | | **~23–25 GB** | unified memory, shared with the OS |

**Minimum M4 Pro 36 GB** (tight — closing apps). **Recommended M4 Max 48–64 GB.**

MoE is a particularly good Mac fit: Apple Silicon inference is memory-bandwidth bound and Qwen3.6
activates only 3B of 35B per token — 35B quality at 3B bandwidth cost.

⚠ **Mac risk 1 — ASR.** MERaLiON has no official MLX build. A community 4-bit MLX conversion of
MERaLiON-**2**-3B exists but is unverified (401 on fetch) and a generation behind. So the Mac demo may
not run the same ASR as the server → **Singlish accuracy could differ between your two demos.**
Options in order: (a) convert Polyglot-Lion-1.7B to MLX (Qwen3-ASR fine-tune, mlx-audio supports that
family — most likely clean path); (b) test the community MERaLiON-2 MLX quant; (c) MERaLiON-3 on
PyTorch MPS and accept slower inference. **Settle in week 1.**

⚠ **Mac risk 2 — Mandarin TTS.** Kokoro includes Mandarin but reviewers rate its English notably
higher. Test early. Fallback: pre-render Mandarin scripted turns on the RHEL box with CosyVoice3 and
ship the audio with the Mac build — six of seven turns are fixed wording, so that covers most of the call.

## 4. Target B — RHEL GPU server (CUDA)

| Component | Choice | VRAM |
|---|---|---|
| LLM | Qwen3.6-35B-A3B | reserved (outside budget) |
| ASR | MERaLiON-3-3B-ASR (vLLM) | ~8 GB |
| TTS | Fun-CosyVoice3-0.5B | ~3 GB |
| VAD + turn | Silero + turn detector | ~0.5 GB |
| **Total** | | **~11.5 GB** |

At 11.5 GB the engine-swapping question is moot for this config — room to spare.

```bash
# 1 · NVIDIA driver + container toolkit
sudo dnf install nvidia-container-toolkit

# 2 · CDI — the RHEL/podman GPU mechanism
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml

# 3 · verify BEFORE touching any model
podman run --rm --device nvidia.com/gpu=all nvidia/cuda:12.4.0-base nvidia-smi

# 4 · SELinux: label mounted volumes with :Z
podman run --device nvidia.com/gpu=all -v ./models:/models:Z ...

# 5 · firewalld — open only the console port, bind vLLM to localhost
sudo firewall-cmd --add-port=8080/tcp --permanent && sudo firewall-cmd --reload
```

⚠ **The three that actually bite:**
- **SELinux** — default policy blocks container device access. Symptoms are opaque errors deep in CUDA
  init, not a clear message. Do step 3 before debugging model loads.
- **Podman, not Docker** — most vLLM docs assume Docker. Use podman + CDI. Don't paste Docker commands.
- **Subscription/repo access** — `subscription-manager` without the right repos fails like a network
  problem. Confirm entitlements before day one.

Budget **two days**, not two hours.

## 5. Side by side

| Dimension | MacBook | RHEL server |
|---|---|---|
| Primary job | portable demo, daily dev | the real system; the accurate demo |
| ASR fidelity | substituted — may differ on Singlish | MERaLiON-3, the genuine article |
| Network needed | **none** — fully offline | yes, plus the box being up |
| Telephony | browser only, realistically | browser and real SIP |
| Concurrency | one call | many |
| Setup effort | low — pip and go | two days (driver, CDI, SELinux) |
| Demo risk | lowest | higher, but it's the truthful one |

**Demo day:** run **RHEL as primary** (real models = the claim you're making), keep the **MacBook as
live fallback**. Same console on both, so the audience won't notice the switch.

## 6. The demo console

Working prototype: https://claude.ai/code/artifact/be4fecd6-c36f-4787-a2e6-6ba274274a96
Source in [ui/demo-console.html](../ui/demo-console.html) — runs five simulated scenarios.

Must show:
- **Scenario + persona picker** with visible synthetic-data marking
- **Live transcript with a language chip per utterance** — how the audience *sees* the Mandarin switch
- **Latency, live** — voice-to-voice ms per turn + running p50
- **Script rail** — which of 7 turns, and pre-rendered vs generated
- **Compliance gates** — identity / DNC / marketing consent / advice guard
- **Backend badge** — MLX or CUDA + model health; answers "is this really local?" without a word

**Single-screen layout.** The page itself never scrolls — `height: 100dvh; overflow: hidden` on the
shell, three columns that each manage their own overflow, and `min-height: 0` on every flex child so
panels can shrink below content size. Only the transcript scrolls internally (it's a live feed, and it
auto-scrolls to the latest turn). Verified with no page overflow at 1440x900 and 1366x768 in both
themes. Below 1180px wide or 620px tall the three-column layout can't fit a screen honestly, so those
breakpoints hand scrolling back to the page rather than clipping content.

Build notes: Vite + React + TS, WebRTC audio, one WebSocket for events. Design for a projector (large
type, high contrast, no colour-only status encoding — every gate state pairs a mark with a word). Motion
tracks real events only — a spinner animating while nothing happens reads as fake. Ship a **replay
mode** that plays a recorded session through the same UI: that's your demo-day fallback and it looks
identical because it is the same code.

## 7. Build order

1. **Week 1** — settle the Mac ASR question (convert Polyglot-Lion to MLX, test the MERaLiON-2 quant,
   measure both against MERaLiON-3 on your Singlish recordings).
2. **Week 1 (parallel)** — RHEL bring-up: driver, toolkit, CDI, nvidia-smi-in-podman, SELinux. Start
   day one so it's never on the critical path.
3. **Week 2** — console shell against a mock backend. UI first: makes every later integration visible,
   is the earliest thing stakeholders can react to, and becomes replay mode for free.
4. **Week 3** — Mac backend, English end to end, offline on the laptop.
5. **Week 4** — CUDA backend on RHEL. Same code, `profile: cuda`. Compare both on identical audio.
6. **Week 5–6** — scenarios, gates, Malay-detection escalation, rehearsal on both machines including
   the mid-demo fallback switch.

## 8. Unknowns

- **MERaLiON on MLX unverified** — community MERaLiON-2-3B 4-bit conversion couldn't be inspected (401).
  Existence ≠ works, and it's a generation behind.
- **Polyglot-Lion has no published MLX build either** — the inference is that mlx-audio's Qwen3-ASR
  support extends to a Qwen3-ASR fine-tune. Reasonable, untested.
- **Kokoro Mandarin quality is a real question**, not a formality. Judge by ear early.
- **Mac tok/s figures are third-party** and measured on text generation, not inside a voice loop with
  ASR and TTS competing for the same unified memory bandwidth. Expect worse.
- **RHEL version assumed 9.** If it's RHEL 8 the toolkit path differs. Confirm before week 1.


---

## Built (2026-09-01)

The CUDA path is no longer a plan — `src/voicebot/runtime/cuda_backend.py` implements it.

**Shape:** no models in-process. ASR and LLM are OpenAI-compatible vLLM endpoints; TTS is a small
sidecar (`scripts/tts_sidecar.py`) used only for improvised lines. The process stays small and
restartable while GPU work sits behind stable endpoints.

**The cache is the interesting part.** Pre-render keys cover model, language, speaker, reference
clip, generation parameters and text — nothing platform-specific. So `make prerender` on a MacBook
produces wavs the server reuses byte-for-byte. Ship `voices/cache` and `voices/refs` with the code
and six of seven turns cost a disk read on both targets, and the two deployments are guaranteed to
sound identical.

**Deliberate difference from the Mac path:** the server does *not* render on a cache miss. On a
laptop a miss costs one slow line; on a call server it would stall the turn behind a model load with
a customer on the line. It falls through to the live voice and logs the miss instead.

**Readiness checks the model, not the port.** `/v1/models` is queried and the configured model must
be present. This was a real bug found in development: an unrelated process on port 8801 returned
404s, and a liveness-only probe reported ready — the console would have started calls against
nothing.

**Host prep** is `deploy/rhel/setup.sh`, ordered so the container-GPU check runs *before* any model
work. SELinux blocking container device access otherwise surfaces as an opaque CUDA init error, and
the script prints `setsebool -P container_use_devices 1` when it catches it.

**Still unverified:** no NVIDIA hardware was available. The backend is tested against a stub of all
three services — request shapes, cache short-circuit, readiness including the wrong-model case — so
everything but the GPU is covered. Budget the two days §04 predicted for the first real bring-up.
