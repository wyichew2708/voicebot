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

---

## Shipping a release (2026-09-03)

A release is **two artefacts, and only one of them is in git.** Getting that wrong is the failure
this section exists to prevent: the code deploys cleanly, every scripted line misses the cache, and
the call is held in a voice nobody chose.

| Artefact | Where it lives | Size | How it travels |
|---|---|---|---|
| Code, config, **reference clips** | git — `voices/refs/*.wav` is tracked | 7 MB | `git pull` |
| **Pre-rendered cache** — `voices/cache/` | gitignored | ~1 GB, 4,680 wavs | `rsync`, out of band |

The cache is gitignored because it is generated, large, and changes whenever the script, a voice or
a persona does. The reference clips are tracked because they are **deployment assets** — the ten
files that define what the agent sounds like, without which nothing can be rendered at all.

### The runbook

```bash
# 1 · On the machine that renders. Bring the cache up to date FIRST: the box
#     must never be the thing that discovers a missing line.
git pull && make prerender            # prints "N distinct lines, M to render"

# 2 · Ship the cache. Content-addressed, so this is incremental and safe to
#     repeat; --delete prunes lines the script no longer reaches.
rsync -av --delete voices/cache/ rhel-box:/opt/voicebot/voices/cache/

# 3 · On the GPU box.
cd /opt/voicebot && git pull          # brings code, config and voices/refs
./deploy/rhel/services.sh             # ASR + TTS containers
make rhel                             # or: docker compose up -d console

# 4 · Verify before anyone dials.
curl -s localhost:8788/api/health | jq '.ready, .asr, .llm, .tts, .knowledge'
```

Step 1 before step 3, always. A cache miss on this box does **not** render — it falls through to the
live voice, which is a different speaker mid-call, and logs `pre-render miss on the server`. That is
deliberate (a miss on a laptop costs one slow line; here it would stall the turn behind a model
load), and it means the cache is a precondition rather than an optimisation.

### What the cache holds

`make prerender` covers **1,701 distinct lines**: three personas × two languages × two registers ×
seven voices, plus both forms of every turn that asks a question — with it, and without it for a
caller who has already answered. Roughly 2–3 hours from cold on an M-series Mac, and it is
incremental, so a script change re-renders only what changed.

Keys cover model, language, speaker, reference clip, generation parameters, pacing, target pitch and
text — nothing platform-specific. So a wav rendered on a MacBook is reused byte-for-byte here, and
`tests/test_profile_parity.py` fails if the two profiles ever disagree about a value that is in the
key. A mismatch would not error; it would quietly re-render every line into a slightly different
voice.

### Mandarin needs its own clips

Each voice clones **two** reference clips — an English speaker and a Mandarin one — and the language
of the line picks which. Cloned from the English clip, a Mandarin line came back from the recogniser
with every character right and none of the punctuation: the tones were flat, because the speaker
being imitated had never spoken Mandarin.

```yaml
male:
  ref_audio: {en: voices/refs/male.wav, zh: voices/refs/zm_yunjian.wav}
  target_f0: {en: 162, zh: 135}
```

Every male voice clones `zm_yunjian`, every female voice `zf_xiaobei`. Both are tracked in git, so
`git pull` brings them; `tests/test_mandarin_voice.py` fails if a profile names a clip the
repository does not carry — which is precisely the failure that is invisible at runtime, because a
missing clip renders the model's default speaker and nothing downstream can tell.

### The TTS sidecar clones what it is told

Improvised lines only — scripted turns never reach it. The **reference clip travels with the
request** (`ref_audio` in the `/tts` body), resolved against `/app`, and a named clip that does not
exist is a `400` rather than a silent fall back to the default speaker.

That means the sidecar container **must have `voices/` mounted**. `docker-compose.yml` does it;
`deploy/rhel/services.sh` did not until this release, and without it the image's anonymous volume is
empty and every improvised line is a stranger's voice. The script now mounts it and warns if
`voices/refs` is missing on the host.

### If something sounds wrong

| Symptom | Cause | Check |
|---|---|---|
| Voice changes mid-call | a cache miss fell through to live TTS | `journalctl`/container logs for `pre-render miss on the server` |
| Every improvised line is the wrong speaker | `voices/` not mounted into the TTS container | `podman inspect voicebot-tts \| grep -A3 Mounts` |
| Mandarin sounds flat, English fine | Mandarin clips missing, so it fell back to the English one | `make test` — `test_mandarin_voice.py` covers it |
| Console starts but calls fail | a service is up on the port but serving the wrong model | `/api/health` reports `ready` from `/v1/models`, not liveness |
| Coverage questions all become callbacks | `unsourced_answers: refuse`, as this profile ships | `/api/health` → `knowledge`; **this is correct behaviour** |

---

## Trying another voice (2026-09-07)

Eight TTS models are now selectable at runtime, on both targets, without editing config or
restarting anything. The switch is in the console — *TTS model — experiment* — and the point of it
is that the incumbent is a choice rather than a default nobody has tested against.

**One registry, two platforms.** `config/tts-models.yaml` is the whole vocabulary. Each entry says
what the model is called, which languages it will actually speak, whether it clones a reference clip
or holds preset speakers, and how to reach it on each target:

```yaml
kokoro:
  languages: [en, zh]
  clone: false
  speaker:
    male:   {en: am_michael, zh: zm_yunjian}
    female: {en: af_heart,   zh: zf_xiaobei}
  mlx: {repo: mlx-community/Kokoro-82M-4bit}   # Mac: in-process
  gpu: {engine: kokoro}                        # RHEL: its own sidecar
```

`src/voicebot/tts_models.py` turns that into a *lab* per platform: `MLXLab` loads the model in
process and keeps two resident, `SidecarLab` routes each model to the sidecar serving its engine.
Both backends' `speak()` calls the same override hook first, so the switch reaches every improvised
line and nothing else in the pipeline learns which model is talking.

| | MacBook (`mlx`) | RHEL (`cuda`) |
|---|---|---|
| How a model runs | in-process, mlx-audio | one sidecar per engine |
| Where it comes from | `mlx:` repo id, downloaded on first use | `gpu:` engine name, built into an image |
| Switching cost | a model load (seconds, then cached) | none — the sidecars are already up |
| Bringing one up | `make tts-deps` / nothing | `docker compose --profile trial up -d --build tts-kokoro` |

**The trial sidecars are opt-in.** Six `tts-<engine>` services sit behind compose's `trial` profile
on ports 8803–8808, so `docker compose up -d` still starts exactly what it started before. The
console is told where they are through `VOICEBOT_TTS_SIDECARS`, which compose pre-wires.

**A selected model takes the whole call, cache and all.** Pick one and every line of the next call
is spoken by it — the scripted turns bypass the pre-rendered cache, because the reason to try a
model is to hear it on the lines a customer actually gets, not only on the improvised ones. So the
switch is a bench control, not a deployment setting: the default position renders nothing new and
serves the cache exactly as before, and the console refuses to change model mid-call (409) rather
than swapping voices on a caller. Leave it on the default for anything a customer will hear.

**Licences are recorded, not enforced.** Several candidates are non-commercial or research-licensed
(F5-TTS's weights are CC-BY-NC-4.0; Fish S2 is research-only) and three are English-only. The
registry carries the language list and the sidecar refuses a language a model cannot speak, but
nothing stops a non-commercial model being selected — this is an experiments bench. Read
[tts-models.md](tts-models.md) before pointing a real call at anything but the default.

### Hearing them

`make tts-bench` runs a 78-sentence Singapore insurance set — premiums, NRIC fragments, addresses,
mixed English/Mandarin lines — through any set of models or sidecar addresses and reports latency,
real-time factor, failures, speaker drift in semitones and character error rate. Whatever it renders
is kept, and the console's *Voice samples → LISTEN* panel plays the lot, filtered female/male, so a
choice can be made by ear as well as by table.

### What was actually run

The Mac path was run in full on CPU (mlx-audio has one): Kokoro through `MLXLab` for female EN,
female ZH and male EN. The GPU path was run with the real deps script, the real sidecar process and
the real `CUDABackend` — Kokoro and the default Chatterbox, gender routing verified by measured
pitch (222 Hz female against 125 Hz male), Malay correctly refused. **CUDA itself and the container
images are still unverified**: `docker compose config` validates with and without `--profile trial`,
but no image has been built here.

Three defects came out of that run and are fixed:

| What broke | Why it was invisible | Fix |
|---|---|---|
| The dependency script installed nothing | the documented `PIP=<venv>/bin/pip` cannot work — `uv venv` installs no `pip` | `VENV=` resolves an installer, and the script exits 2 if it cannot find one |
| Every English line 500'd on Kokoro | its front end needs spaCy's `en_core_web_sm`, which is not a pip dependency of anything — so the sidecar booted and reported healthy | the deps arm installs the wheel by URL; the engine catches the error and names the fix |
| A slow engine produced a silent turn | TTS shared the 30 s ASR/LLM budget and an exceeded budget returned empty audio | TTS has its own `timeout_s` (120 s default) and logs `this turn will be SILENT` with the engine and the budget |
