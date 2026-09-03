# Tiq Renewal Voicebot

Multilingual outbound voicebot for **Tiq Home Insurance renewal servicing** (Etiqa Singapore).
Handles English, Singlish and Mandarin; understands Malay and escalates rather than speaking it.

This repo is the **MacBook / Apple Silicon** build. The RHEL GPU deployment shares this codebase
behind the same `Backend` seam — see [docs/deployments.md](docs/deployments.md).

```bash
make setup     # venv + app, no models
make run       # console at http://127.0.0.1:8788
```

## Deployed locally

The console runs as a **launchd user agent** — it starts at login, restarts if it crashes, and
survives closing the terminal:

```bash
make install    # install and start the agent
make status     # agent state + health
make logs       # tail it
make uninstall  # remove it completely
```

| | |
|---|---|
| Console | http://127.0.0.1:8788 |
| Logs | `logs/console.log` (stdout), `logs/console.err.log` (uvicorn) |
| Agent | `com.voicebot.console` in `~/Library/LaunchAgents` |
| Stop once | `launchctl bootout gui/$UID/com.voicebot.console` |

First start takes ~60 s: it loads Whisper and Kokoro and warms the synthesiser so the opening line
of a live call is not the one that pays for the cold pipeline. Crash restarts are throttled to 30 s
so a boot loop cannot spin the GPU.

The MERaLiON sidecar is **not** installed as an agent — it is slow on this hardware (§ Singlish) and
memory-heavy, so it stays a deliberate `make meralion` when you want it.

That runs the whole product — script, state machine, compliance gates, console — with **no models
installed**. You drive the caller side by typing. It is the development default and the demo-day
fallback.

## Running with real models

```bash
make mlx       # MLX extras + the spacy model Kokoro's G2P needs
make mac       # console with MLX models (downloads ~1.7 GB on first run)
```

## Singlish: words and accent are separate problems

Pick the register in the console (**STANDARD / SINGLISH**) before starting a call, or set
`register: singlish` in the profile. They are different problems with very different answers.

### Words — solved

`register: singlish` rewrites all seven scripted turns into Singapore call-centre English:

| | |
|---|---|
| standard | "Good afternoon Mr Tan. This is Dave calling from Etiqa Insurance. Am I speaking with Mr Tan?" |
| singlish | "Good afternoon Mr Tan ah. I'm Dave, calling from Etiqa Insurance. Speaking to Mr Tan, is it?" |
| standard | "Please look through the email and renew by the due date, and do reply once payment is made." |
| singlish | "Just look through the email, then renew before the due date. After you pay, reply to the email can already." |

Aimed at how an agent actually talks, not at the internet version. Tests assert the rewording keeps
every compliance element — company identification, the property address, the due date, and all four
figures still come from the fact store.

⚠ **This rewords the client's approved script.** The facts are identical but the phrasing is not, so
Etiqa must sign it off before it goes near a real call.

### Accent — cloning is not enough

**Correction to earlier guidance in this repo.** Zero-shot cloning from a reference clip does *not*
deliver a Singaporean accent. [Published measurement](https://arxiv.org/abs/2607.23027) of exactly
this: off-the-shelf zero-shot TTS "reproduces a speaker's timbre while **flattening the accent toward
generic English**". You get your colleague's voice speaking with the base model's rhythm.

The fix is fine-tuning on Singaporean speech — which the same paper shows works and generalises to
unseen speakers. Full analysis and ranked options in **[docs/singlish-voice.md](docs/singlish-voice.md)**.

### The cloning plumbing (still useful, for timbre)

**No preset voice anywhere in mlx-audio is Singaporean.** Kokoro ships American, British, Chinese,
Japanese and a few European voices — that is the whole list. British (`bm_george`) is nearer than
American because Singapore English follows British norms, but nobody will mistake it for local.

The only route to a Singaporean accent in this stack is **zero-shot voice cloning**, which several
mlx-audio models support (chatterbox, indextts, voxcpm, spark, sesame, higgs_audio, qwen3_tts). The
plumbing is built: set `tts.reference_audio` in the profile and the backend clones that speaker
instead of using a preset voice.

```yaml
tts:
  model: mlx-community/chatterbox-8bit
  reference_audio: voices/singaporean_reference.wav   # 10-30 s, clean, one speaker
```

**What is needed is the clip, and only you can supply it** — a colleague, a licensed recording, your
own voice. The accent comes from whoever is on the reference, so a synthetic American clip just
returns the American accent you are trying to leave. Drop a file in `voices/`, uncomment that line,
and the voice becomes theirs.

The alternative remains the best one for a demo: **a Singaporean voice actor records the six
pre-rendered turns.** No cloning artefacts, no model risk, and it covers most of every call.

## The call says one name

A salutation and the surname — **"Mr Chew"**, never "Mr Chew Yi Feng". Reading the whole name out
is what a form letter does, and on turn 1, which asks someone to confirm they *are* that person, it
sounds like a list being worked through rather than a call being made.

The record is meant to carry the surname on its own and the three demo personas do, so
`spoken.surname_of()` is the net under that: an operator typing a full name into the console, or a
CRM column that turns out to hold one, is reduced before it reaches the voice.

| on the record | said | why |
|---|---|---|
| `Chew Yi Feng` | Mr **Chew** | surname-first, as most Singaporean Chinese records are written |
| `Chuan Ping Fong` | Madam **Chuan** | same |
| `Andrew Tan` | Mr **Tan** | a Western given name puts the surname last |
| `Andrew Tan Wei Ming` | Mr **Tan** | …unless a Chinese given name follows it |
| `Muhammad Farid bin Abdullah` | Mr **Farid** | a patronymic names the father, not its owner |
| `Rajesh s/o Kumar` | Mr **Rajesh** | same |

The signal that separates the two orders is the **given name**, not the surname: a name written
surname-first does not open with "Andrew". A single-word surname is returned untouched, which is
what keeps every existing record and every cached line exactly as it was.

⚠ **The patronymic rule needs Etiqa's confirmation.** Malay and Indian names have no surname, and
dropping the religious opener — "Mr Farid", not "Mr Muhammad" — is what a Singaporean agent would
say, but it is a convention rather than a field on the record.

The full name is still shown to the **operator** in the console header and handed to a **colleague**
in a handoff, because both of them need the record. It is only the voice that says one word.

## Numbers the customer reads back

A premium is a quantity and is read as one. A policy number, an email, a unit number and a callback
number are not: someone is checking them character by character against a letter in their hand, so
they are spelled out. Getting this wrong is not cosmetic — it states the wrong fact in the customer's
own words, and nothing downstream can tell.

Every one of these was found by transcribing our own output back through the recogniser. None is
visible from the code:

| written | was heard as | now |
|---|---|---|
| `2026` | "twenty fifty-six" | twenty twenty-six |
| `TH-4471-0093` | "t h four four seven one **zero** nine three" | T H four four seven one zero zero nine three |
| `wm.tan@example.sg` | "w m **two ten** at example dot **x a**" | w m dot tan at example dot s g |
| `#08-212` | "**neiro** eight two one two" | unit oh eight, two one two |
| `6887 8777` | "**sixteen** eight eight seven eight seven seven seven **eight seven seven**" | six eight eight seven eight seven seven seven |

The written form is what appears in the transcript; the spelling happens on the way to the voice.

### What a Mandarin call says in English

Three things, and only three: the **email address**, the **property address**, and **Etiqa**. All
three are read back against something written down, and a Mandarin rendering of an address is not
the address on the policy. Everything else — the premium, the dates, the sums insured, the policy
number's digits, the callback number — is spoken in Mandarin, because 六八八七 is what a Mandarin
speaker says and an English voice cutting in to read a phone number is not.

**Tiq is deliberately not on that list.** It renders cleanly on its own — 0.84 s, heard back as
"tick" — but between Chinese neighbours the recogniser lost it in two lines out of three. Five
letters survive a seam; three do not. The Mandarin voice at least says something in its place.

## Language switching needs evidence

Switching mid-call is disruptive, so it now takes one of two things: the caller **asks**
("可以讲华语吗", "can you speak Mandarin"), or they use the other language on **two consecutive
turns**. Previously a single utterance flipped the whole call — so one stray word, or an ASR wobble,
threw the conversation into Mandarin mid-sentence.

When it does switch, it says so ("当然可以，我们用华语继续") rather than silently continuing in a
different language. Register and language are tracked separately: a Singlish caller softens the
register and leaves the language alone.

## ASR: Polyglot-Lion is the Mac answer

`config/mac-polyglot.yaml` is the deployed default. **Polyglot-Lion** is Qwen3-ASR fine-tuned on
Singapore's four official languages (English, Mandarin, Malay, Tamil). mlx-audio supports the
`qwen3_asr` family natively and the authors publish MLX builds, so it needs no porting and no
sidecar — `knoveleng/polyglot-lion-1.7b-v1.5-mlx-8bit` loads straight through `mlx_audio.stt`.

Measured on this machine, same six Singlish clips, warm:

| ASR | Per utterance | Full voice loop |
|---|---|---|
| **Polyglot-Lion 1.7B (MLX 8-bit)** | **0.16–0.27 s** | **0.6–1.3 s** |
| Whisper turbo + Singlish prompt | ~1.5 s | 1.1–1.8 s |
| MERaLiON-3-3B (PyTorch / MPS) | 6.7–7.9 s | unusable |

Roughly **7× faster than Whisper and 35× faster than MERaLiON on MPS**, from a model actually
trained on the right languages. That is what makes the conversation feel like a conversation.

⚠ **Accuracy is still unmeasured.** All three were compared on American-accented speech synthesised
from Singlish *text* — that tests lexicon, not accent, and is the wrong test for every model here.
Polyglot-Lion returns lowercase, unpunctuated text and still fumbles some particles
("okay lore you send first law"). Its own paper does not evaluate code-switching at all. **Benchmark
on real Singaporean recordings before trusting any of these numbers for accuracy.**

## Singlish, and where MERaLiON actually fits

MERaLiON-3-3B-ASR is the only model in this stack trained on Singlish, Malay and Singapore
Mandarin. It **does run locally on this Mac** — `make meralion-setup && make meralion`, then
`--profile mac-meralion`. Two things make that less useful than it sounds.

**It needs its own interpreter.** MERaLiON's modelling code imports `HybridCache` and other
transformers 4.x internals; mlx-audio requires `transformers>=5.14`. That is a hard conflict, not a
warning — so MERaLiON runs in `.venv-meralion` behind a small HTTP sidecar
(`scripts/meralion_sidecar.py`) and the app calls it. This mirrors MERaLiON's own recommended
deployment, minus vLLM, which is CUDA-only.

**On Apple Silicon it is too slow to converse with.** Measured through the app, MPS backend:

| Model | Per utterance | Singlish particles |
|---|---|---|
| Whisper turbo, no prompt | ~1.5 s | "Wasso expensive mech got cheaper 1.00 anot" |
| **Whisper turbo + Singlish prompt** | **~1.5 s** | "Wah so expensive meh, got cheaper one anot" |
| MERaLiON-3-3B-ASR (MPS) | **6.7–7.9 s** | "Wah, so expensive meh? Got cheaper one anot." |

So the Mac default stays Whisper **with a Singlish vocabulary prompt**, which is the cheap win:
biasing alone rescues the particles that plain Whisper turns into the proper nouns "Lore" and "Law".

⚠ **This comparison cannot settle accuracy.** The test audio is American-accented speech synthesised
from Singlish *text*, which is the worst possible input for judging a model trained on real
Singaporean speakers — it plays to Whisper's training distribution and away from MERaLiON's. It
measures lexicon handling, not accent. **Judge this on real recordings before drawing conclusions.**
What the test does settle is latency, and there MERaLiON on MPS is 4–5× too slow for a live call.

MERaLiON's place is the RHEL box with vLLM, where it reports ~600 ms first-token. On the Mac, treat
the sidecar as an accuracy bench, not a demo path.

### The MLX quant — checked, and not usable yet

`majentik/MERaLiON-3-3B-ASR-MLX-8bit` looks like exactly the answer: 8-bit affine MLX weights,
~4.2 GB, tagged for Singlish, and its own smoke test reports **0.235–1.01 s per clip** — 7–30x
faster than the PyTorch/MPS path, which would put it comfortably inside a conversational budget.

**It cannot be loaded today.** Verified:

- `mlx_audio.stt.utils.load()` → `Model type meralion3 not supported for stt`
- `mlx_lm.load()` → `Model type meralion3 not supported`
- The `modeling_meralion3.py` bundled in the repo imports **torch, not mlx** — it is the upstream
  PyTorch file, carried along for config
- The README says inference runs through `pipelines/meralion3_mlx`, and PROVENANCE.json points at
  `pipelines/mlx_direct_quantize.py` — paths in a source repo that is not published
- No `meralion3-mlx`, `meralion-mlx` or `mlx-meralion` package exists on PyPI

So the weights are quantized for MLX but the runtime that reads them is missing. Two ways forward:

1. **Ask the uploader to publish the loader.** Their own smoke results prove it exists and works.
   Cheapest path by a wide margin.
2. **Port the architecture to MLX ourselves** — Whisper encoder and Gemma2 decoder both already
   exist in MLX; the speech adapter and the glue are the work, roughly 570 lines of PyTorch to
   translate. Bounded, but a real project, and a subtly wrong port produces plausible transcription
   that is quietly incorrect — the worst failure mode for ASR.

Until one of those lands, MERaLiON is not the Mac path — and given Polyglot-Lion runs at 0.2 s from
the same language family, the port is a much lower priority than it looked. MERaLiON's real value is
on the RHEL box, where accuracy matters more than the ~600 ms it costs there.

## The console

Three columns: **controls left, waveform centre, transcript right.**

**Controls are the ones that do something.** The MLX/CUDA toggle was removed — it was a fake: the
backend is fixed by the profile the server starts with, and clicking it only relabelled the ASR.
It is now a read-only *Live backend* panel showing runtime, ASR, TTS and readiness. What remains is
real:

| control | effect |
|---|---|
| Policyholder | which synthetic record the call runs against |
| Opening language | English or 华语 — the language the agent opens in |
| Agent voice | male / female, both pre-rendered |
| Register | standard or Singlish wording |

All four are fixed once a call starts — changing language, voice or register mid-call is jarring,
and the engine treats them the same way.

**Two ways to run a call.** *Simulate a call* plays the caller side automatically, so a demo runs
hands-free and the same way twice. *Start call — I'll speak* opens the mic and the typing box for a
live one.

**Transcripts are never cleared.** Every call accumulates in the right-hand column under a header
recording persona, language, voice and register. **Export** downloads the lot as JSON.

### Recording is server-side

The browser copy is a view. The record is `logs/calls.jsonl` — one line per completed call, written
by `src/voicebot/recording.py`, holding every event with a relative timestamp: what was said, which
gates passed, when the cross-sell was suppressed and why. A transcript that lives only in a tab is
lost on reload, and an outbound insurance call needs a record that outlives the operator's session.

```bash
curl -s localhost:8788/api/calls | jq '.[0]'      # summaries, newest first
curl -s localhost:8788/api/calls/<id> | jq        # full event log
```

Abandoned calls are recorded too — a dropped socket or a hang-up still closes the record, rather
than the call vanishing.

⚠ Synthetic personas only, so this is not customer data. That changes the moment the CRM stub is
replaced: retention and access control apply from that point.

## Hallucinated speech, and why it needs two gates

Fed silence or room noise, Whisper-family recognisers do not return an empty
string — they emit fluent, plausible sentences from their training data. Two
real examples from this build, both from background noise mid-call:

> *"i'm a little bit scared."*
> *"他于二零零二年毕业于香港大学政府及政策研究中心。"*

Both entered the transcript as if the caller had said them. Nothing downstream can catch that: the
dialogue engine, the compliance gates and the recording all take the recogniser at its word. So
`src/voicebot/audio_gate.py` filters on both sides of it.

**Before the recogniser** — buffers shorter than 350 ms, quieter than the room floor, or less than
20% voiced are dropped without transcribing.

**After it** — the strong signal is **speech rate**: text the audio was too short to contain is
invented, whatever it says.

That threshold has to be script-aware, and getting it wrong is how the Chinese example slipped
through the first version. A CJK character is a whole syllable — normal speech runs 5–7 a second —
where Latin script runs 15–20 characters a second for the same content. At 23 characters in 1.1 s
that line sat comfortably under a Latin limit of 25 and at more than double the plausible Chinese
rate:

| script | limit | the hallucination |
|---|---|---|
| Latin | 25 chars/s | *"i'm a little bit scared."* — 27 chars/s ✗ |
| CJK | 10 chars/s | the Chinese line — 22 chars/s ✗ |

Known Whisper artifacts ("thank you for watching", "请不吝点赞 订阅") are blocklisted too, but the rate
check is what generalises.

**The microphone calibrates to your room.** The client VAD used fixed thresholds, which assume a
quiet one — in a noisy room the floor sits above them, every rustle counts as speech, and the
recogniser invents something over it. It now tracks the ambient floor and triggers on a multiple of
it, and requires 350 ms of voiced audio before sending a turn at all.

Rejections are logged (`dropped non-speech`, `rejected transcript`) and the caller sees
*"Didn't catch that — go again"* rather than a diagnostic.

## Audio playback

The console plays agent audio through an **`<audio>` element**, not the Web Audio API. Web Audio
reported success and produced no sound in testing — the transcript advanced, latency counted, and
nothing came out. A media element uses the ordinary playback path, works where Web Audio does not,
autoplays on the sticky activation from the *Start call* click, and pauses cleanly for barge-in.

The player stays visible for the whole call so you can replay a line. If a browser ever refuses to
autoplay, the bar turns amber and reads *"press play"* rather than failing silently.

Still nothing? Fetch the last reply directly and play it in anything:

```bash
curl -o reply.wav http://127.0.0.1:8788/api/last-reply.wav && open reply.wav
```

If that is audible and the console is not, the problem is the browser's audio output rather than the
app — an embedded preview pane often has none.

## Two voices, switchable

The console has a **MALE / FEMALE** switch. Both are **Chatterbox multilingual** (MIT) cloning a
fixed reference clip:

| id | reference |
|---|---|
| `male` | `voices/refs/male.wav` — Qwen3-TTS "aiden" |
| `female` | `voices/refs/female.wav` — Qwen3-TTS "vivian" |

Voice is **fixed for the duration of a call**. Pick before dialling.

### Mandarin is cloned from a Mandarin speaker

Cloned from the English clip, a Mandarin line came back from the recogniser with every character
right and none of the punctuation — the tones and phrasing were flat, because the speaker being
imitated had never spoken Mandarin. Each voice therefore names a clip per language:

```yaml
male:
  ref_audio: {en: voices/refs/male.wav, zh: voices/refs/zm_yunjian.wav}
  target_f0: {en: 162, zh: 135}
```

Every male voice clones Kokoro's `zm_yunjian` for Mandarin and every female voice `zf_xiaobei` —
Kokoro's own speakers reading a Mandarin paragraph, chosen by ear from a side-by-side of the
English-reference clone, Kokoro direct, and the Mandarin-reference clone. The **line's** language
picks the clip, so a call opened in Mandarin keeps one speaker throughout, English fragments
included, and `target_f0` is measured per clip over five scripted lines. A scalar still means "every
language"; a voice recorded in the console has one clip and renders Mandarin from it as before.
English cache keys are untouched by any of this — only the Mandarin lines re-render under
`make prerender`.

### Speaker drift, honestly

Cloning is anchored to a file rather than resampled per line, which is what VoiceDesign got wrong.
It is still **less steady than a named speaker**:

| approach | drift across three lines |
|---|---|
| VoiceDesign (removed) | 192 Hz — a different person per sentence |
| Chatterbox cloning, default temperature | 84 Hz |
| **Chatterbox cloning, `temperature: 0.5`** | **62 Hz** |
| Qwen3-TTS named speaker | 49 Hz |

Chatterbox also does not reproduce its reference exactly — it lifted the male from 139 Hz to
roughly 205 Hz, so it is a natural-sounding speaker *guided by* the reference rather than a copy of
it. Chosen for naturalness with that trade understood. If a call ever sounds like two people, drop
`params.temperature` further and re-render.

Both the reference path and the generation parameters are in the cache key, so changing either
re-renders rather than serving the previous voice.

### Why this is fast rather than slow

Qwen3-TTS takes ~2 s a line, which would be unusable live. It never runs during a call: the scripted
turns are rendered once by `make prerender` and played from disk.

| | Before | Now |
|---|---|---|
| Scripted turn | 206 ms (Kokoro, live) | **1–2 ms** (cache read) |
| Improvised line | 206 ms | 206 ms (Kokoro, unchanged) |

The cache is content-addressed on model + language + voice description + text, so changing the
script, the register or the voice simply misses and re-renders rather than serving a stale line or
mixing two speakers. `make prerender` currently holds **98 lines / 28 MB** — three personas, two
languages, two registers, two voices.

```bash
make prerender    # after changing script, personas, or a voice description
```

A line the cache has never seen — a customer name that was not on the warm list, an answer assembled
at call time — is synthesised in pieces and starts playing while the rest is still being made, so it
does not cost its whole synthesis before a word is heard. It only splits a line when the measured
synthesis rate can sustain it, because below that a split line gaps in the middle instead of merely
starting late. See **[docs/streaming-synthesis.md](docs/streaming-synthesis.md)**.

Add or edit voices under `backend.tts.prerender.voices` in the profile — they are prose
descriptions, so age, pace and warmth all respond. `config/voice-presets.yaml` holds the other six
from the palette if you want to swap one in.

### Speaking to it

Click **Speak** in the console, allow the microphone, and talk. The page captures at the hardware
rate, downsamples to 16 kHz, and runs a small energy VAD with hysteresis: it starts sending on
speech, keeps ~300 ms of pre-roll so the first word is not clipped, and sends `utterance_end` after
700 ms of silence. The bot's reply streams back as binary frames and plays automatically. **Talking
over the agent stops its playback immediately** and tells the server — that is the barge-in path.

The typed box still works and sits alongside the mic; it is the deterministic way to rehearse.

**Measured voice loop** (synthesised caller speech pushed through the real websocket):

| | |
|---|---|
| Caller speech → agent audio | **1.1–1.8 s** |
| Whisper ASR on a ~2 s clip | ~1.5 s — the bottleneck |
| Transcription accuracy | exact on all three test utterances |

That is over the 800 ms bar, and the cause is the ASR: `whisper-large-v3-turbo` is the placeholder,
not the choice. Polyglot-Lion is reported ~20× faster at parity accuracy, which is why swapping it
in is week 1 of the plan and not a nice-to-have.

**Measured on this machine** (M-series, 64 GB), typed path (no ASR in the loop):

| | |
|---|---|
| Voice-to-voice p50 | **401 ms** |
| Range across 6 turns | 271–829 ms |
| TTS warm TTFB | 115 ms (EN) · 194 ms (ZH) |
| TTS real-time factor | 0.04–0.05 — roughly 20× faster than playback |
| Startup warmup | 2.3 s (EN) + 0.4 s (ZH), paid once at boot |

The two ~800 ms turns are the long scripted lines (premium/sums insured, and the cross-sell). Those
are exactly the ones meant to be **pre-rendered to audio at build time** — that cache is not built
yet, so they currently synthesise live. Building it is the single biggest latency win available and
would take those turns to near zero.

### The LLM is not required to run this

`Qwen3.6-35B-A3B` is configured but loads **lazily**, because nothing currently calls it: every agent
line is either scripted or answered from the grounded fact store. So the working deployment is ~1.7 GB
(Whisper + Kokoro), not ~22 GB. The 20 GB download only becomes necessary when the free-form Q&A path
lands. `make models` pulls everything including the LLM if you want it staged in advance.

Recommended hardware is an M4 Max with 48–64 GB if you do load the LLM; without it the footprint is
small enough for any current Apple Silicon Mac.

## Running on the RHEL GPU server

```bash
make rhel-setup   # once per host: driver, CDI, SELinux, firewall
make up           # docker compose: build and start everything
make ps           # what is running
make clogs        # follow the logs
make down         # stop it
```

`docker-compose.yml` brings up three services. `podman compose up -d` works too — swap the
`deploy.resources` GPU block for `devices: ["nvidia.com/gpu=all"]` if your podman uses CDI rather
than the nvidia runtime.

Prefer running the console outside a container while the services stay in one? `make console-only`
starts just the GPU services, then `make rhel` runs the console on the host.

Nothing loads in-process on this path. Each capability is an HTTP service,
which is how vLLM is meant to run and how the reserved Qwen3.6 already runs:

| | service | port | in compose |
|---|---|---|---|
| ASR | MERaLiON-3-3B-ASR on vLLM | 8801 | yes |
| LLM | your existing Qwen3.6 | 8000 | **no — see below** |
| TTS | Chatterbox sidecar, improvised lines only | 8802 | yes |
| console | this app, CPU only | 8788 | yes |

**The LLM is deliberately not in the compose file.** Qwen3.6 already runs on that box, and voice
should point at a dedicated replica or a priority-scheduled queue — a batch job landing mid-call is
dead air to the customer. Point at it with `LLM_URL`:

```bash
LLM_URL=http://10.0.0.5:8000 make up
```

### One image, every environment

Service addresses are environment variables, not baked into the image, so the same build runs on a
laptop, in compose (where they are service names) and on a bare host:

| variable | overrides |
|---|---|
| `VOICEBOT_PROFILE` | which config profile to load |
| `VOICEBOT_ASR_URL` / `VOICEBOT_LLM_URL` / `VOICEBOT_TTS_URL` | service addresses |
| `VOICEBOT_CACHE_DIR` | where the pre-rendered audio lives |
| `VOICEBOT_REGISTER` | `standard` or `singlish` |

The console image is **CPU-only and small** — on this deployment it loads no models at all, so it
builds in seconds and restarts instantly, independent of CUDA versions.

`voices/` is a **mount, not a copy**: 28 MB of pre-rendered audio that changes when the script or
voice changes, not when the code does. Regenerate it with `make prerender` and restart the console;
no rebuild.

**MERaLiON belongs here, not on the Mac.** It managed ~7 s an utterance on Metal; on vLLM it
reports ~600 ms. The Mac runs Polyglot-Lion precisely because MERaLiON is impractical there.

### The cache ships with the deploy

Pre-render keys are platform-independent — model, language, speaker, reference, parameters, text —
so the 98 wavs rendered by `make prerender` **on a laptop are reused byte-for-byte on the server**.
Copy `voices/cache` and `voices/refs` across with the code and the scripted turns cost a disk read
there too.

That also means the TTS sidecar is only needed for improvised lines. If it is down, servicing calls
still work; only unscripted replies fail.

### The improvised line gets the same treatment as a cached one

The sidecar renders **one fragment in one language** and nothing else. Splitting a mixed-script line,
joining the pieces and putting the result on the voice's own pitch all happen in the backend, which
does it by calling the same code the Mac uses — not a second implementation of it. Three things go
wrong when they diverge, and none of them raises an error:

- **No language.** The multilingual checkpoint defaults to English and phonemises accordingly. A
  Mandarin line comes back as English-sounding nonsense of roughly the right duration, and the file
  being present and playable is all the pipeline checks. The sidecar now **rejects a request with no
  language** rather than guessing.
- **The English-only class.** `ChatterboxTTS` loads happily and ignores `language_id` entirely. The
  sidecar refuses to start on it and says so.
- **No pitch normalisation.** Cloning re-derives the speaker per line, so an improvised line lands
  somewhere other than the voice's own median — audible as the speaker changing between a scripted
  turn and an improvised one, in the middle of a sentence.

### Readiness means the right model, not just an open port

`api/health` queries each service's `/v1/models` and checks the configured model is actually being
served. Liveness alone is not enough: during development an unrelated process on port 8801 answered
404s, which an is-it-up probe read as ready — the console would have started calls against nothing.

⚠ **Untested on real hardware.** There is no NVIDIA GPU on this machine, so the CUDA backend is
exercised against a stub of the three services (`tests/test_cuda_backend.py` — request shapes, cache
short-circuit, readiness). Everything except the GPU itself is covered; the first run on the box is
still the first run on the box.

## Layout

```
src/voicebot/
  runtime/          the only platform-aware code
    base.py         Backend protocol: transcribe / complete / synthesize / health
    mock.py         no models at all — the development default
    mlx_backend.py  Apple Silicon (mlx-lm + mlx-audio)
    cuda_backend.py RHEL GPU — HTTP to vLLM services, no in-process models
    prerender.py    cache for scripted turns; keys are platform-independent
  call/
    script.py       the seven scripted turns, EN + ZH, with slot filling
    engine.py       call state machine; emits events, never touches models
  compliance/
    gates.py        identity, DNC, marketing consent, advice — enforced in code
  data/
    personas.py     synthetic CRM (no real policyholder data, ever)
    facts.py        versioned product fact store
  knowledge/        reads the OKF bundle; deterministic, never generates prose
    okf.py          pages, frontmatter, benefit tables
    answer.py       frontmatter filter + alias match -> approved wording
    lint.py         the gate: citations, jurisdiction, figures, links
    policy.py       whether this deployment may speak unsourced wording
  events.py         the event vocabulary the console renders
  server.py         FastAPI + websocket
ui/demo-console.html  operator console — live over websocket, or self-simulating
config/               mock.yaml, mac.yaml
knowledge/            the OKF bundle: raw sources, compiled wiki, benefit tables
```

## The two things this codebase is opinionated about

**Product figures never come from the model.** Every premium, discount and benefit limit lives in
`data/facts.py`. A hallucinated number here is a misrepresentation of an insurance product, not a
typo. The LLM routes and converses; it does not invent prices.

**Compliance gates are code, not prompts.** A prompt instruction is a request; a gate is a
precondition, and each one fails closed. The rule that catches people out: under the PDPA Do Not Call
provisions the ongoing-relationship exemption covers **text and fax only**. A telemarketing *voice*
call still needs consent or a valid DNC check — including to a ten-year policyholder. That is why
servicing (turns 1–5) and marketing (turn 6) are gated separately, and why
`tests/test_compliance.py` asserts the cross-sell is suppressed without consent.

## Measured against real calls

`make eval` replays every caller turn in `logs/calls.jsonl` through the engine and reports how
they were handled — what share the keyword layer settled for free, what share reached the
model, how often the call asked the caller to repeat or offered an escalation. `make eval-live`
does the same with the models in the loop. `tests/eval/expectations.jsonl` pins caller lines
from real calls to what the reply must and must not contain; a miss is a regression against a
real customer. Add a line every time a transcript exposes something.

The readiness picture — what is done, what is measured, what is still open before a real line —
is in [docs/industrial-readiness.md](docs/industrial-readiness.md).

## Tests

```bash
make test
```

Covers the gates, the script rendering in both languages, and the UI/server event contract — that
last one exists because the two drifted once already: the console called the first gate `id` while
the engine emitted `identity`, so a passing right-party check rendered as *Pending*, on the one gate
whose failure matters most.

## Known gaps

- **Mac ASR is unsettled.** `config/mac.yaml` points at `whisper-large-v3-turbo`, which definitely
  works but is weak on Singlish. The preferred path is converting Polyglot-Lion-1.7B to MLX. Measure
  both on your own recordings — that comparison is week 1 of the plan.
- **Scripted turns synthesise live.** The pre-rendered audio cache the design assumes does not exist
  yet, so `speak(prerendered=True)` currently costs a full synthesis. Latency figures above are
  therefore pessimistic for the finished system.
- **No microphone capture in the browser yet.** The console drives the caller side by text. The
  server already accepts binary PCM frames on the websocket and routes them through ASR — the
  remaining work is `getUserMedia` plus downsampling in the page.
- **Malay speech output is on hold** pending a TTS licence. Malay *understanding* is live and routes
  to a human.
- **No telephony.** Browser only. SIP is the RHEL build's job.
- **The home wording contradicts its own filename.** It is published at a v9, 20 October 2023 URL
  and every page of it reads "V10 | 15 March 2025". The bundle records what the document says, but
  a customer's policy cannot be version-matched until Etiqa confirms which is in force. See
  `knowledge/conflicts/0002-home-wording-version-mismatch.md`.
- **Tiq Personal Accident has no source document.** Coverage answers come from the OKF bundle in
  `knowledge/`, where every approved answer cites a policy wording and a page number, and home
  insurance is now fully compiled from its own wording. The cross-sell product is not: its
  discount, starting premium and inpatient limit are still placeholders in `data/facts.py`, and
  they are the numbers most likely to be quoted on a call. See
  [docs/knowledge-layer.md](docs/knowledge-layer.md).
- **The DNC reading is not legal advice.** Confirm with counsel before dialling a real number.
- **Latency figures in mock/typed mode exclude ASR and endpointing**, because neither runs — you are
  typing, not speaking. The clock starts when the caller's audio arrives, so the number is honest for
  the audio path and correspondingly small for the typed one. Don't quote typed-mode numbers.
