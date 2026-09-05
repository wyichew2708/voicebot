# TTS candidates — what they are, how to try each one here, and what fits

A shortlist of seven self-hosted TTS models was proposed for this platform: CosyVoice 3,
Fish Speech S2, Chatterbox Turbo, F5-TTS, IndexTTS 2, Kokoro and VibeVoice Realtime. **All
seven are now runnable behind the same sidecar and the same benchmark**, on the GPU box and —
where an MLX port exists — on the Mac. This page is the assessment of each against **this
product** (an outbound insurance call that must speak English and Mandarin in one voice), the
exact way to run each, and the benchmark to compare them on.

**This is the experiments stage.** Nothing here ships to a customer, so licence is recorded
as a fact about each model rather than a gate on trying it. It becomes a gate the day one of
them is chosen, which is why it is written down now rather than rediscovered then. Licences
and language lists were read from the projects' own repositories and model cards on
2026-09-05; they change.

## Switching models: the short version

Everything below is reachable from one file, `config/tts-models.yaml`, and three switches that
read it. None of them needs a profile edit or a restart.

**In the console.** *Call setup → TTS model — experiment.* Pick a model and every agent line of
the next call — scripted turns included, cache bypassed on purpose — is spoken by it; the
transcript shows the latency each line cost. The box under the picker says one typed line in
the selected model without starting a call (Enter or **SAY**), and the player bar shows which
model spoke and how long it took. Models the machine cannot run are greyed out with the reason
in their tooltip. Blank returns to the shipped path.

**From the terminal.**

```bash
make tts-models                                        # what runs here, and why not
make tts-say MODEL=kokoro TEXT="Good afternoon Mr Tan, this is Michael from Etiqa."
make tts-say MODEL=cosyvoice3 TEXT="您的保单在二月十日到期。" VOICE=female
make tts-bench MODELS="chatterbox cosyvoice3 kokoro"   # the whole sentence set, side by side
```

`tts-say` writes `voices/bench/say/<model>.wav` and plays it. The `PROFILE` variable (default
`mac-polyglot`) picks whose lab runs it: the Mac profile loads mlx-audio models in-process; the
RHEL profile talks to the sidecars named in `backend.tts.sidecars` or `VOICEBOT_TTS_SIDECARS`.

**Adding or changing a model** is a stanza in `config/tts-models.yaml`: label, languages, whether
a clip changes the speaker (else `speaker` presets per language and `lang_codes`), the MLX repo,
the GPU engine. A different quantisation is one line changed. The switch, the CLI and the
benchmark all pick it up on the next request.

Two things the switch does not do. It does not change the *voice* — the reference clips and
presets a call uses are still the voice picker's — so the comparison is model against model on
the same speaker. And it is fixed for the duration of a call, like the voice and the register.

## The candidates in one table

| Model | Engine | Weights licence | zh | ms | Clones a clip | Mac (MLX) | Fit for this product |
|---|---|---|---|---|---|---|---|
| **Chatterbox multilingual v3** (in use) | `chatterbox` | MIT | ✅ | ✅ | ✅ | mlx-audio | the incumbent, and the bar |
| **CosyVoice 3** 0.5B | `cosyvoice3` | Apache-2.0 | ✅ | ❌ | ✅ (transcript helps) | mlx-audio-plus fork | **the one serious challenger** |
| Chatterbox Turbo 350M | `chatterbox-turbo` | MIT | ❌ English only | ❌ | ✅ (clip required) | mlx-audio | English live voice only |
| Chatterbox Nano 110M | `chatterbox-nano` | MIT | ❌ English only | ❌ | ✅ | — | CPU curiosity |
| IndexTTS-2 1.7B | `indextts2` | bilibili Model Use License | ✅ | ❌ | ✅ | mlx-audio | listen; legal review; heavy |
| Kokoro 82M | `kokoro` | Apache-2.0 | ✅ | ❌ | ❌ presets | mlx-audio (in use) | already the fallback voice |
| F5-TTS | `f5` | code MIT, **weights CC-BY-NC-4.0** | ✅ | ❌ | ✅ (transcript helps) | f5-tts-mlx | experiments only |
| Fish Speech S2-Pro ~5B | `fish` / `fish-server` | **Fish Audio Research License** | ✅ | ✅ | ✅ (transcript required) | torch on MPS | experiments only |
| VibeVoice Realtime 0.5B | `vibevoice` | MIT | ❌ English only | ❌ | ❌ presets | mlx-audio | English preset voice |

None of the seven speak **Tamil**. Only Chatterbox multilingual and Fish list **Malay**. The
Singapore-language question stays where [singlish-voice.md](singlish-voice.md) left it —
fine-tune, or record.

Three things in the shortlist as presented were wrong and are worth knowing before listening:

- **F5-TTS is not commercially usable as shipped.** Its README: the pre-trained models "are
  licensed under the CC-BY-NC license due to the training data Emilia". Code MIT, weights not.
  This repo dropped `Malaysian-F5-TTS-v3` for exactly this ([architecture-research.md](architecture-research.md) §5).
- **Chatterbox Turbo is English-only**, as are Nano and VibeVoice Realtime. Not a weak
  multilingual rating — zero Mandarin. On a call that opens in 华语 they cannot speak, and on an
  English call that quotes an address to a Mandarin speaker they need a second model, which is
  the two-speakers-in-one-call problem most of this repo's voice work exists to prevent.
- **Fish Speech S2's licence is resolved, not open.** Research and non-commercial at no cost;
  commercial use only under a paid licence from Fish Audio. It is also ~5B parameters.

## Each model, against this product

### CosyVoice 3 — benchmark it properly

`FunAudioLLM/Fun-CosyVoice3-0.5B-2512`. Apache-2.0. Chinese, English, Japanese, Korean,
German, Spanish, French, Italian, Russian, Cantonese, a list of Chinese dialects. Zero-shot
cloning from a clip, better with the clip's transcript; streaming; vLLM and TensorRT-LLM
serving paths in the repo. It is the other model the Singlish fine-tuning paper used alongside
Chatterbox, with a slightly *better* accent-similarity gain (0.5771 → 0.6798 against
0.5114 → 0.6376), which matters because the production plan is a fine-tune.

It is the only model on the list that is commercially clean, speaks Mandarin, clones and
streams — everything the current Chatterbox path relies on. Switching would re-render the
1,701-line cache and re-measure every voice's `target_f0`, so the trial is a benchmark, not a
config flip.

Details that decide a first listen: it installs from a recursive clone, not PyPI (the deps
script does this); CosyVoice 3 carries `You are a helpful assistant.<|endofprompt|>` in front
of its text in every example in the repo, and the engine adds it; give it the reference
transcript (`voices/refs/male.txt`, or `ref_text` on the voice) to take the zero-shot path.
No Malay.

### Chatterbox Turbo — the English fast path, if anything

`ResembleAI/chatterbox-turbo`, MIT, 350M, single-step decoder. `[laugh]` `[chuckle]` `[cough]`
inline. English only; a reference clip is required. Could be the *live* English voice for
improvised lines beside the Chatterbox-multilingual cache — same family, closest timbre. Two
cautions: the tags are a demo delight and a sign-off question on a compliance-approved script,
and a Mandarin call gets nothing from it, so the English/Mandarin seam runs between two models.

### IndexTTS-2 — worth an hour of listening, then a lawyer

`IndexTeam/IndexTTS-2`, 1.7B, Chinese and English, strong similarity and emotion control.
Weights under the bilibili Model Use License: commercial use permitted below 100M MAU /
RMB 1bn revenue, attribution and downstream-terms duties, a prohibition on "high-risk" uses
that names automated decision-making, PRC law, Shanghai arbitration. Three times the size of
CosyVoice 3.

### Kokoro 82M — already here, in exactly the role proposed

`hexgrad/Kokoro-82M`, Apache-2.0, ~0.2 s a line, presets only. It is the live voice in every
Mac profile and the shipped Mandarin clips are Kokoro's own speakers. The `kokoro` engine puts
the same presets behind the GPU sidecar.

### F5-TTS — for listening

`SWivid/F5-TTS`, `pip install f5-tts`, English and Chinese, 253 ms on an L20 in its own
Triton benchmark. CC-BY-NC-4.0 weights. `F5_MODEL`/`F5_CKPT` point at a differently licensed
checkpoint if one is ever trained or bought. On the Mac, `f5-tts-mlx` is a separate port.

### Fish Speech S2 — for listening, and heavy

`fishaudio/s2-pro`, 80+ languages including Malay, streaming from its own server. Research
licence. Two engines: `fish` runs the model in-process through the same inference engine its
`api_server` wraps; `fish-server` forwards to a running `api_server` (its SGLang-backed
recommended path). Both need the reference clip's transcript. It pins its own torch, so it is
always its own image or venv.

### VibeVoice Realtime — English preset voice

`microsoft/VibeVoice-Realtime-0.5B`, MIT, ~300 ms to first audio, streaming text in. The card:
"intended for English speech only; other languages may produce unpredictable results". The
speaker is one of the shipped `.pt` voice prompts (Carter by default; nine experimental
languages and eleven English styles via the repo's download script), not a clone of a supplied
clip. So here it is a preset English voice with more character than Kokoro, and nothing for a
Mandarin call.

## Running one on the GPU box

Nothing above the TTS seam changed. The console, the call engine, the cache and the CUDA
backend still speak one contract — `POST /tts` with text, language, reference clip — and every
candidate is an **engine** behind that contract in the one sidecar.

```
$ make tts-engines
chatterbox        ResembleAI/chatterbox (multilingual v3)   clones=True   langs: ar da de … ms … zh
chatterbox-nano   ResembleAI/chatterbox-nano                clones=True   langs: en
chatterbox-turbo  ResembleAI/chatterbox-turbo               clones=True   langs: en
cosyvoice3        FunAudioLLM/Fun-CosyVoice3-0.5B-2512      clones=True   langs: de en es fr it ja ko ru yue zh
f5                F5TTS_v1_Base                             clones=True   langs: en zh
fish              checkpoints/s2-pro                        clones=True   langs: any
fish-server       fishaudio/s2-pro via api_server           clones=True   langs: any
indextts2         IndexTeam/IndexTTS-2                      clones=True   langs: en zh
kokoro            hexgrad/Kokoro-82M                        clones=False  langs: en zh
vibevoice         microsoft/VibeVoice-Realtime-0.5B         clones=False  langs: en
```

The rules every engine is held to are the rules the Chatterbox sidecar already had:

- **A language the engine does not speak is a 400**, naming the engine and what it speaks.
  Turbo and VibeVoice have no language argument at all; handed 就是续保的事 they would read it
  through the English front-end and return audio of a plausible length.
- **A cloning engine without a clip is a 400**, not the model's default speaker.
- **A preset-voice engine says so once** in the log and uses a preset chosen by the line's
  language — for Mandarin on Kokoro, the same speaker the cloning voices copy.
- **The reference transcript travels when there is one**: `ref_text` in the request, from the
  voice's profile entry or a `.txt` beside the clip. Chatterbox ignores it; CosyVoice 3 and F5
  clone better for it; Fish refuses without it.
- `/health` reports `engine`, `model`, `languages`, `clones`; the console's *Live backend* panel
  shows the engine the sidecar actually loaded.

Each engine has its own dependency set — four of them are git repositories, Fish pins its own
torch — so there is one image, or one venv, per engine. The default image is unchanged.

```bash
# containers: build the trial image and run it BESIDE the default, on its own port
make tts-build TTS_ENGINE=cosyvoice3
podman run -d --name voicebot-tts-cosy --device nvidia.com/gpu=all \
  -v ./voices:/app/voices:Z -v ~/.cache/huggingface:/root/.cache/huggingface:Z \
  -p 127.0.0.1:8803:8802 localhost/voicebot-tts:cosyvoice3

# or in compose, replacing the default for the whole stack (a trial, not a deploy)
TTS_ENGINE=vibevoice docker compose up -d --build tts

# bare host: one venv per engine, then the sidecar on its own port
uv venv .venv-cosy --python 3.11
PIP=.venv-cosy/bin/pip TTS_ENGINE_PREFIX=$PWD/models ./scripts/tts_engine_deps.sh cosyvoice3
COSYVOICE_HOME=$PWD/models/CosyVoice .venv-cosy/bin/python scripts/tts_sidecar.py --engine cosyvoice3 --port 8803
```

Beside, not instead: the cache was rendered by Chatterbox, and a trial engine on port 8802
makes every improvised line a different speaker from the cached lines around it. Point the
console at a trial engine (`VOICEBOT_TTS_URL`) only on a box nobody is dialling from.

Per-engine environment (defaults in the engine classes and `Dockerfile.tts`): `TTS_ENGINE`,
`COSYVOICE_HOME`, `COSYVOICE_MODEL`, `INDEXTTS_HOME`, `INDEXTTS_MODEL`, `F5_MODEL`, `F5_CKPT`,
`FISH_HOME`, `FISH_MODEL`, `FISH_DECODER_CONFIG`, `FISH_URL`, `VIBEVOICE_HOME`,
`VIBEVOICE_MODEL`, `VIBEVOICE_VOICE`, `KOKORO_VOICE_EN`, `KOKORO_VOICE_ZH`.

## Running one on the Mac

This repo is the MacBook build, and the MLX path was already model-agnostic: `tts.model` is
the live voice, `tts.prerender.model` the cached one, both mlx-audio repo ids. Two environment
variables now override them without editing a profile, and because the model is in the cache
key a trial renders beside the shipped lines rather than over them:

```bash
VOICEBOT_PRERENDER_MODEL=mlx-community/IndexTTS-2-fp16 make prerender --voices male
```

What loads where — checked against mlx-audio's model directory and the model cards:

| Model | MLX repo | Loader | Notes |
|---|---|---|---|
| Chatterbox multilingual | `mlx-community/chatterbox-multilingual-v3` | mlx-audio (pinned) | in use |
| Chatterbox Turbo | `mlx-community/chatterbox-turbo-{fp16,8bit,4bit}` | mlx-audio (`chatterbox_turbo`) | cloning; English |
| IndexTTS-2 | `mlx-community/IndexTTS-2-fp16`, `index-tts2-mlx` | mlx-audio (`indextts`) | cloning |
| VibeVoice Realtime | `mlx-community/VibeVoice-Realtime-0.5B-{fp16,8bit,4bit}` | mlx-audio (`vibevoice`) | preset `voice=`, English |
| Kokoro | `mlx-community/Kokoro-82M-{bf16,8bit,4bit}` | mlx-audio | in use; `voice=` + `lang_code` a/z |
| **CosyVoice 3** | `mlx-community/Fun-CosyVoice3-0.5B-2512-{fp16,8bit,4bit}` | **`mlx-audio-plus`** | a fork that *installs as* `mlx_audio` — separate venv |
| F5-TTS | `lucasnewman/f5-tts-mlx` | **`f5-tts-mlx`** | its own package and API |
| Fish Speech S2 | — | the `fish` engine, torch on MPS | its model manager supports `mps`; slow |

The CosyVoice 3 row is the one to be careful with. Upstream mlx-audio has no CosyVoice
family; the mlx-community builds are read by `mlx-audio-plus`, a fork that removes
incompatibly-licensed code and adds CosyVoice 2/3, and it installs under the same `mlx_audio`
import name. Do not put it in the app's venv — it would replace the pinned mlx-audio under the
live console. A venv of its own, and the benchmark's `--mlx` path pointed at it, is the way:

```bash
uv venv .venv-cosy-mlx --python 3.11 && uv pip install --python .venv-cosy-mlx/bin/python \
  mlx-audio-plus fastapi "uvicorn[standard]" pyyaml numpy
.venv-cosy-mlx/bin/python scripts/tts_bench.py --mlx mlx-community/Fun-CosyVoice3-0.5B-2512-8bit
```

## The benchmark

`make tts-bench` renders a fixed sentence set through one or more candidates and produces a
page to listen to and a table of the numbers an ear cannot judge. It is the "Singapore
insurance benchmark" the shortlist asked for, built from the script rather than written fresh,
so it measures the product. 78 distinct lines today (50 English, 28 Mandarin):

| group | what is in it |
|---|---|
| `script` | the seven scripted turns for every persona, standard register, English and Mandarin |
| `singlish` | the same turns in the Singlish rewording |
| `money` `deductible` `sums` | S$1,284.60 · 23.5% · S$3,500 · 三万五千新元 |
| `policy` `claim` `email` `address` `phone` `date` | TH-4471-0093 · a.tan@example.sg · #08-212 · 6887 8777 · 10 February 2026 |
| `scheme` `cpf` | MediShield Life · Integrated Shield Plan · CPF MediSave · 终身健保 · 公积金 |
| `names` `brand` `question` | Mr Tan, Madam Yeo, Mr Ng, Mr Chew · Etiqa · Tiq Home · Tan先生 |

Two modes, because they answer different questions. **Product** (default) sends each line
through the live call's own path — identifiers spelled, a mixed line split by script, each
piece in its own language. **`--raw`** sends the written line whole, which shows what the
model's *own* normalisation does with "S$1,284.60" — the thing the deterministic layer in
`spoken.py` exists to stop mattering.

```bash
# GPU box: sidecars on their own ports, the same clips to every one
python scripts/tts_sidecar.py --engine chatterbox --port 8802 &
python scripts/tts_sidecar.py --engine cosyvoice3 --port 8803 &
python scripts/tts_sidecar.py --engine vibevoice  --port 8804 &
make tts-bench TARGETS="chatterbox=http://127.0.0.1:8802 cosyvoice3=http://127.0.0.1:8803 vibevoice=http://127.0.0.1:8804"
open voices/bench/latest/index.html

# with MERaLiON in the loop, for character error rate against what was said
make tts-bench TARGETS="..." BENCH_ARGS="--asr-url http://127.0.0.1:8801"

# Mac: mlx-audio models in-process; a preset-voice model names its speaker
python scripts/tts_bench.py --mlx mlx-community/chatterbox-multilingual-v3 \
                            --mlx mlx-community/IndexTTS-2-fp16
python scripts/tts_bench.py --mlx mlx-community/VibeVoice-Realtime-0.5B-8bit --mlx-voice Carter --langs en
python scripts/tts_bench.py --mlx mlx-community/Kokoro-82M-4bit --mlx-voice am_michael --mlx-lang-codes en=a,zh=z
python scripts/tts_bench.py --f5-mlx          # F5 through f5-tts-mlx
```

An English-only model on the Mandarin lines fails those rows and says why; the failures are
rows in the table, not a crash. A model that cannot speak a language is a result.

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

Models run one after another, never interleaved on one GPU. The first line of each includes
the model's first-use cost, reported rather than hidden.

## What to run first

All of them, once, on the `script` and `names` groups, and listen. Then the full set with the
ASR round-trip for **Chatterbox multilingual, CosyVoice 3 and IndexTTS-2** — the three that
speak both languages and clone — reading `drift` and `CER` after the ear has had its say. Turbo
and VibeVoice on `--langs en` beside them, for how much English latency and character the
Mandarin requirement costs. F5 and Fish for calibration.

If CosyVoice 3 wins by ear and by number, the switch is a profile change plus `make prerender`
plus re-measuring `target_f0` per voice — and it puts the production voice on the same model
the Singlish fine-tuning recipe was published for. If anything on the non-commercial half wins
by a margin, that is a licence conversation, not a deployment.
