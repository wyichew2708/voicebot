# TTS candidates — what they are, what they may be used for, how to try them here

A shortlist of seven self-hosted TTS models was proposed for this platform: CosyVoice 3,
Fish Speech S2, Chatterbox Turbo, F5-TTS, IndexTTS 2, Kokoro and VibeVoice Realtime. This is
the check of that list against **this product** — an outbound insurance call that must speak
English and Mandarin in one voice, on-premise, commercially — followed by what was built so
each can be tried without the console, the cache or the call engine learning anything new.

Licences and language lists below were read from the projects' own repositories and model
cards on 2026-09-05. They change; re-check before anything ships.

## The verdict in one table

| Model | Licence (weights) | zh | ms | ta | Clones a clip | Fits this product |
|---|---|---|---|---|---|---|
| **Chatterbox multilingual v3** (in use) | MIT | ✅ | ✅ | ❌ | ✅ | the incumbent, and the bar |
| **CosyVoice 3** 0.5B | Apache-2.0 | ✅ | ❌ | ❌ | ✅ (transcript helps) | **the one serious challenger** |
| Chatterbox Turbo 350M | MIT | ❌ **English only** | ❌ | ❌ | ✅ (clip required) | English live voice only |
| IndexTTS-2 1.7B | bilibili Model Use License | ✅ | ❌ | ❌ | ✅ | listen; legal review; heavy |
| Kokoro 82M | Apache-2.0 | ✅ | ❌ | ❌ | ❌ preset voices | already the fallback voice |
| F5-TTS | code MIT, **weights CC-BY-NC-4.0** | ✅ | ❌ | ❌ | ✅ (transcript helps) | trial only — non-commercial |
| Fish Speech S2-Pro ~5B | **Fish Audio Research License** | ✅ | ✅ | ❌ | ✅ (transcript required) | trial only — non-commercial |
| VibeVoice Realtime 0.5B | MIT | ❌ **English only** | ❌ | ❌ | ❌ not documented | not added |

Three corrections to the shortlist as it was presented:

- **F5-TTS is not commercially usable as shipped.** Its README: the pre-trained models "are
  licensed under the CC-BY-NC license due to the training data Emilia". The code is MIT; the
  weights are not. This repo already dropped `Malaysian-F5-TTS-v3` for exactly this reason
  ([architecture-research.md](architecture-research.md) §5).
- **Chatterbox Turbo is English-only**, as is Chatterbox Nano. That is not a three-star
  multilingual rating; it is zero Mandarin. On a call that opens in 华语 it cannot speak at all,
  and on an English call that quotes an address to a Mandarin speaker it would need a second
  model — which is the two-speakers-in-one-call problem most of this repo's voice work exists
  to prevent.
- **Fish Speech S2's licence is resolved, not open.** The S2-Pro card: research and
  non-commercial use at no cost, commercial use only under a separate paid licence from Fish
  Audio. It is also ~5B parameters — larger than the whole TTS VRAM budget in the plan.

And one thing none of the seven does: **Tamil**. Only Chatterbox multilingual and Fish list
**Malay**. The Singapore-language question this list was meant to answer is not answered by
any model on it; it stays where [singlish-voice.md](singlish-voice.md) left it — fine-tune, or
record.

## Each model, against this product

### CosyVoice 3 — benchmark it properly

`FunAudioLLM/Fun-CosyVoice3-0.5B-2512`. Apache-2.0 on the card. Chinese, English, Japanese,
Korean, German, Spanish, French, Italian, Russian, Cantonese and a list of Chinese dialects.
Zero-shot cloning from a clip; better with the clip's transcript; streaming; vLLM and
TensorRT-LLM serving paths in the repo. It is also the other model the Singlish fine-tuning
paper used alongside Chatterbox, with a slightly *better* accent-similarity result
(0.5771 → 0.6798 against Chatterbox's 0.5114 → 0.6376), which matters because the production
plan in [singlish-voice.md](singlish-voice.md) is a fine-tune.

Why it is the only serious challenger: it is the only model on the list that is commercially
clean, speaks Mandarin, clones, and streams. Everything the current Chatterbox path relies on,
it also has.

What it costs to switch: the pre-render cache is keyed on the model, so every one of the
1,701 lines re-renders, and every voice's `target_f0` was measured from what *Chatterbox*
lands on and would need re-measuring. Neither is a reason not to try it; both are reasons
the trial is a benchmark and not a config flip.

Things to know before trusting a first listen:

- It is not on PyPI in a form that installs. The repo is cloned recursively and put on
  `sys.path`, exactly as its own README does. `scripts/tts_engine_deps.sh cosyvoice3` does this.
- CosyVoice 3 carries a system prompt (`You are a helpful assistant.<|endofprompt|>`) in front
  of its text in every one of the repo's examples. The engine adds it; without it the model is
  being prompted differently from how it was trained.
- Give it the reference transcript. Put what the clip says in a `.txt` beside the wav
  (`voices/refs/male.txt`) or in the voice's profile entry as `ref_text`, and the engine takes
  the zero-shot path rather than the transcript-free one.
- No Malay. If Malay speech output is ever unblocked, this model does not carry it.

### Chatterbox Turbo — the English fast path, if anything

`ResembleAI/chatterbox-turbo`, MIT, 350M, single-step decoder, built for low-latency agents.
Takes `[laugh]`, `[chuckle]` and `[cough]` inline. **English only.** A reference clip is
required. There is a Nano at 110M for CPU.

Where it could fit: as the *live* English voice for improvised lines, where 200 ms matters
and the cached lines are already Chatterbox multilingual — same family, closer timbre than
any other pairing. Two cautions. The paralinguistic tags are appealing in a demo and a
sign-off question on a compliance-approved insurance script: a `[chuckle]` is a change to the
wording Etiqa approved. And a Mandarin call gets nothing from it, so it can only ever be one
half of a pair; the English/Mandarin seam then runs between two different models rather than
two clips of one, and drift across that seam is the thing to measure first.

### IndexTTS-2 — worth an hour of listening, then a lawyer

`IndexTeam/IndexTTS-2`, 1.7B, Chinese and English, strong speaker similarity and emotion
control in the published numbers, cloned from a clip. The weights are under the **bilibili
Model Use License Agreement**: commercial use is permitted unless the licensee exceeds
100 million monthly active users or RMB 1 billion annual revenue, with attribution and
downstream-terms obligations, a prohibition on "high-risk" uses that names automated
decision-making, governing law of the PRC and arbitration in Shanghai. For an insurer's
customer-facing system that is a legal review, not a footnote. It is also three times the
size of CosyVoice 3 for a high-concurrency call platform, and it installs from the repository
with its own checkpoint download.

### Kokoro 82M — already here, in exactly the role proposed

`hexgrad/Kokoro-82M`, Apache-2.0, ~0.2 s a line, no cloning. It is the live voice in every Mac
profile and the shipped Mandarin clips (`zm_yunjian`, `zf_xiaobei`) are Kokoro's own speakers.
The shortlist's placement — cheap fallback, never the premium voice — is the placement it
already has. The `kokoro` engine puts the same presets behind the GPU sidecar so the fallback
is available on that box too.

### F5-TTS — for listening, not for shipping

`SWivid/F5-TTS`. Good quality per parameter, `pip install f5-tts`, Triton/TensorRT-LLM
deployment with a published 253 ms figure on an L20. English and Chinese. But the base weights
are CC-BY-NC-4.0, so as shipped it cannot voice a commercial call. The engine exists so the
quality/cost claim can be heard; `F5_MODEL`/`F5_CKPT` point at a differently licensed
checkpoint if one is ever trained or bought.

### Fish Speech S2 — same, and heavier

`fishaudio/s2-pro`. 80+ languages, Malay among them, very strong published quality, streaming
from its own SGLang-based server. Research licence. The `fish` engine forwards to a running
`fish-speech` `api_server` rather than loading the model itself, because its serving stack is
its own; it is there to be listened to.

### VibeVoice Realtime — not added

`microsoft/VibeVoice-Realtime-0.5B`, MIT, ~300 ms to first audio. The card is explicit:
"intended for English speech only; other languages may produce unpredictable results", and
voices are preset styles rather than clones of a supplied clip. English-only *and* no cloning
rules it out twice over for this product, so it has no engine. MLX builds exist under
`mlx-community/VibeVoice-Realtime-0.5B-*` if anyone wants to hear it on a Mac.

## What was built so they can be tried

Nothing above the TTS seam changed. The console, the call engine, the cache and the CUDA
backend still speak one contract — `POST /tts` with text, language, reference clip — and every
candidate is an **engine** behind that contract in the one sidecar.

```
scripts/tts_sidecar.py --list-engines

chatterbox        ResembleAI/chatterbox (multilingual v3)       clones=True  langs: ar da de el en es fi fr he hi it ja ko ms nl …
chatterbox-nano   ResembleAI/chatterbox-nano                    clones=True  langs: en
chatterbox-turbo  ResembleAI/chatterbox-turbo                   clones=True  langs: en
cosyvoice3        FunAudioLLM/Fun-CosyVoice3-0.5B-2512          clones=True  langs: de en es fr it ja ko ru yue zh
f5                F5TTS_v1_Base                                 clones=True  langs: en zh
fish              fishaudio/s2-pro via api_server               clones=True  langs: any
indextts2         IndexTeam/IndexTTS-2                          clones=True  langs: en zh
kokoro            hexgrad/Kokoro-82M                            clones=False langs: en zh
```

The rules every engine is held to are the rules the Chatterbox sidecar already had, made
general:

- **A language the engine does not speak is a 400**, naming the engine and what it does
  speak. Chatterbox Turbo has no language argument at all; handed 就是续保的事 it would read it
  through the English front-end and return audio of a plausible length. The refusal is the
  only place that can be caught.
- **A cloning engine without a clip is a 400**, not the model's default speaker.
- **An engine that cannot clone says so once** in the log and uses a preset chosen by the
  line's language — for Mandarin, the same Kokoro speaker the cloning voices copy.
- **The reference transcript travels when there is one**: `ref_text` in the request, from the
  voice's profile entry or a `.txt` beside the clip. Chatterbox ignores it; CosyVoice 3 and F5
  clone better for it; Fish refuses without it.
- `/health` reports `engine`, `model`, `languages` and `clones`, and the console's *Live
  backend* panel shows the engine the sidecar actually loaded rather than the profile's label.

### Running one

Each engine has its own dependency set — two of them are git repositories, not packages — so
there is one image, or one venv, per engine. The default image is unchanged and still tagged
for Chatterbox.

```bash
# GPU box, containers: build the trial image and run it BESIDE the default, on its own port
make tts-build TTS_ENGINE=cosyvoice3
podman run -d --name voicebot-tts-cosy --device nvidia.com/gpu=all \
  -v ./voices:/app/voices:Z -v ~/.cache/huggingface:/root/.cache/huggingface:Z \
  -p 127.0.0.1:8803:8802 localhost/voicebot-tts:cosyvoice3

# or in compose, replacing the default for the whole stack (a trial, not a deploy)
TTS_ENGINE=cosyvoice3 docker compose up -d --build tts

# GPU box, bare host: deps into a venv, then the sidecar
PIP=.venv-tts/bin/pip TTS_ENGINE_PREFIX=$PWD/models ./scripts/tts_engine_deps.sh cosyvoice3
COSYVOICE_HOME=$PWD/models/CosyVoice .venv-tts/bin/python scripts/tts_sidecar.py --engine cosyvoice3 --port 8803
```

Beside, not instead: the cache was rendered by Chatterbox, and a trial engine on port 8802
means every improvised line on every call is a different speaker from the cached lines
around it. Point the console at a trial engine (`VOICEBOT_TTS_URL`) only on a box nobody is
dialling from.

Environment the engines read: `TTS_ENGINE`, `COSYVOICE_HOME`, `COSYVOICE_MODEL`,
`INDEXTTS_HOME`, `INDEXTTS_MODEL`, `F5_MODEL`, `F5_CKPT`, `FISH_URL`, `KOKORO_VOICE_EN`,
`KOKORO_VOICE_ZH`. Defaults are in the engine classes and in `Dockerfile.tts`.

### On a Mac

The MLX path is already model-agnostic: `tts.model` is the live voice and
`tts.prerender.model` the cached one, both mlx-audio repo ids. Two environment variables now
override them without editing a profile, and because the model is in the cache key a trial
renders beside the shipped lines rather than over them:

```bash
VOICEBOT_PRERENDER_MODEL=mlx-community/Fun-CosyVoice3-0.5B-2512-8bit make prerender
```

MLX builds exist for CosyVoice 3 (`mlx-community/Fun-CosyVoice3-0.5B-2512-{fp16,8bit,4bit}`),
Chatterbox Turbo (`mlx-community/chatterbox-turbo-{fp16,8bit,4bit}`), IndexTTS-2
(`mlx-community/IndexTTS-2-fp16`) and VibeVoice Realtime; none for F5 or Fish. ⚠ The
CosyVoice 3 card says it loads through `mlx-audio-plus`, a fork — whether the `mlx-audio`
this repo pins loads it is **unverified**. Expect to try both.

## The benchmark

`make tts-bench` renders a fixed sentence set through one or more sidecars and produces a
page to listen to and a table of the numbers an ear cannot judge. It is the "Singapore
insurance benchmark" the shortlist asked for, built from the script rather than written
fresh, so it measures the product. 78 distinct lines today (50 English, 28 Mandarin) — the
shortlist said 100–200, and the way to get there is more personas and more coverage answers,
not more prose:

| group | what is in it |
|---|---|
| `script` | the seven scripted turns for every persona, standard register, English and Mandarin |
| `singlish` | the same turns in the Singlish rewording |
| `money` `deductible` `sums` | S$1,284.60 · 23.5% · S$3,500 · 三万五千新元 |
| `policy` `claim` `email` `address` `phone` `date` | TH-4471-0093 · a.tan@example.sg · #08-212 · 6887 8777 · 10 February 2026 |
| `scheme` `cpf` | MediShield Life · Integrated Shield Plan · CPF MediSave · 终身健保 · 公积金 |
| `names` `brand` `question` | Mr Tan, Madam Yeo, Mr Ng, Mr Chew · Etiqa · Tiq Home · Tan先生 |

Two modes, because they answer different questions. **Product** (default) sends each line
through `CUDABackend.synthesize` — the code a live call runs, so identifiers are spelled,
a mixed line is split by script, and each piece goes in its own language. **`--raw`** sends
the written line whole, which shows what the model's *own* normalisation does with
"S$1,284.60" — the thing the deterministic layer in `spoken.py` exists to stop mattering.

```bash
python scripts/tts_sidecar.py --engine chatterbox --port 8802 &
python scripts/tts_sidecar.py --engine cosyvoice3 --port 8803 &
make tts-bench TARGETS="chatterbox=http://127.0.0.1:8802 cosyvoice3=http://127.0.0.1:8803"
open voices/bench/latest/index.html

# with MERaLiON in the loop, for character error rate against what was said
make tts-bench TARGETS="..." BENCH_ARGS="--asr-url http://127.0.0.1:8801"
```

What it measures, against the list the shortlist proposed:

| proposed | here |
|---|---|
| First-audio latency | wall time per line (the sidecar is not streaming; TTFB is a follow-up) |
| RTF | ✅ per line and mean |
| Speaker similarity | ✱ by ear, on the page — same reference clip for every model |
| MOS | ✱ by ear |
| WER/CER | ✅ with `--asr-url`, against what the voice was handed |
| Number pronunciation | ✅ the `money`/`policy`/`address`/`phone`/`date` groups, product and `--raw` |
| Speaker steadiness | ✅ **drift** — spread of per-line median pitch, in semitones (this repo's own metric) |
| GPU VRAM, concurrent calls/GPU | not here — `nvidia-smi` while it runs, then a load test |
| Barge-in recovery, streaming stability | not a TTS property — the console's, see [streaming-synthesis.md](streaming-synthesis.md) |
| Malay, Tamil | no model on the list speaks Tamil; Malay is Chatterbox multilingual and Fish only |

The same reference clips go to every model (`voices/refs/male.wav`, `zm_yunjian.wav` for
Mandarin, or `--ref-en`/`--ref-zh`). Models run one after another, never interleaved on one
GPU. The first line of each includes the model's first-use cost, reported rather than hidden.

## Recommendation

Run the benchmark with **Chatterbox multilingual (the incumbent), CosyVoice 3, and Chatterbox
Turbo on the English half**, with the ASR round-trip on. Listen to the `script` and `names`
groups first; read the `drift` and `CER` columns second. IndexTTS-2 is worth the same run if
its licence survives review. F5 and Fish are worth a listen for calibration and cannot ship.

If CosyVoice 3 wins by ear and by number, the switch is a profile change plus `make
prerender` plus re-measuring `target_f0` per voice — and it puts the production voice on the
same model the Singlish fine-tuning recipe was published for.
