# Working on this codebase

An outbound insurance voicebot that calls real customers about real money. The
rules below are not style preferences; each one exists because breaking it
produces a failure that is invisible from the code and audible on the call.

Read this before changing anything under `src/voicebot/`, `scripts/` or
`config/`.

## The three invariants

**1. Product figures never come from a model.** Every premium, discount, date
and benefit limit lives in `data/facts.py` or the OKF bundle in `knowledge/`,
and reaches the caller through fixed wording with slots filled from the policy
record. A hallucinated number here is a misrepresentation of a regulated
financial product, not a typo. Two models run and neither writes a word the
caller hears: the router picks a handler, the dictation reader extracts a
candidate email. If a change would let generated text reach the voice, it is
the wrong change.

**2. Compliance gates are code, not prompts.** A prompt instruction is a
request; a gate is a precondition and each one fails closed
(`compliance/gates.py`). The rule that catches people out: under the PDPA Do
Not Call provisions the ongoing-relationship exemption covers text and fax
only, so a telemarketing *voice* call needs consent or a valid DNC check even
to a ten-year policyholder. That is why servicing and marketing are gated
separately.

**3. Fail loudly, never silently.** This is the one most often broken, because
the silent failures all look like success. A wav of plausible length is not
speech. Empty audio is indistinguishable from a quiet line. A model that
ignores the language argument returns something, and it plays. Every one of
these has actually happened here. The pattern to follow:

- refuse what cannot be done correctly, with a message that names the fix —
  a `400` from the sidecar, a `RuntimeError` at boot, an error log that says
  what the consequence will be ("this turn will be SILENT");
- never substitute a default for a missing input that changes the output —
  no default language, no default speaker, no default reference clip;
- prefer failing at boot over failing at the first call, and at the first call
  over failing quietly forever.

## The platform seam

`runtime/base.py` defines `Backend`. Everything above it — the call state
machine, the gates, the script, the console — is identical on both targets.
Only model loading and inference differ:

| | MacBook | RHEL GPU |
|---|---|---|
| Profile | `mlx` (`config/mac*.yaml`) | `cuda` (`config/rhel.yaml`) |
| Models | in-process, mlx-audio and mlx-lm | HTTP to vLLM and the TTS sidecar |
| Backend | `runtime/mlx_backend.py` | `runtime/cuda_backend.py` |

Put platform-specific code behind that seam or not at all.
`tests/test_profile_parity.py` fails if the two profiles disagree about
anything audible, and if one backend grows a method the other lacks.

## The pre-rendered cache

Six of seven scripted turns are fixed wording, rendered once by `make
prerender` and played from disk. This is what makes the latency claim true.
Two rules:

- **Everything that changes the audio belongs in the cache key**
  (`runtime/prerender.py`). Model, language, language code, segmentation
  shape, speaker, reference clip, reference transcript, generation
  parameters, pacing, target pitch, text. A value left out of the key does
  not error — it serves the previous voice.
- **Keys are platform-independent on purpose.** A wav rendered on a laptop is
  reused byte-for-byte on the server. Changing a figure in one profile and not
  the other silently re-renders every line into a slightly different voice.

The server does **not** render on a cache miss; it falls through to the live
voice and logs it. Warm the cache and ship it.

## Voices

Seven ship, each a reference clip a cloning model copies. Every voice declares
its `gender`, because a model that cannot clone (Kokoro, VibeVoice) has to
follow the voice picker somehow, and a woman must stay a woman. A recorded
voice has its gender inferred from measured pitch.

Cloning drifts. Every rendered line is normalised to the voice's own
`target_f0`, measured from what the *clone* lands on over several lines — not
from the reference clip, which the clone does not reproduce exactly. A
Mandarin line clones a Mandarin speaker: cloned from an English clip the tones
come out flat, and the recogniser hears every character right and no
punctuation.

## Trying a TTS model

`config/tts-models.yaml` is the registry; `src/voicebot/tts_models.py` is the
switch. Adding a candidate is one stanza — label, languages, whether a clip
changes the speaker, the MLX repo, the GPU engine — and it appears in the
console picker, `make tts-say` and `make tts-bench` on the next request. See
[docs/tts-models.md](docs/tts-models.md).

A selected model speaks every line of a call, cache included, because the
point is to hear the candidate rather than the incumbent's recordings. It is
fixed for the duration of a call, like the voice and the register.

On the GPU box each engine is its own container (`--profile trial`), on its
own port, **beside** the default rather than in its place: the cache was
rendered by Chatterbox, and a trial engine on 8802 would put a different
speaker on every improvised line of every call.

## Text on the way to the voice

`spoken.py` decides what the synthesiser is actually handed, and none of it is
cosmetic. A premium is a quantity and read as one; a policy number, an email,
a unit number and a callback number are checked character by character against
a letter in someone's hand, so they are spelled out. A Mandarin line with an
address in it is two languages and one front-end cannot read both.

Every rule there was found by transcribing our own output back through the
recogniser, and each comment records what was heard before the fix. Do not
change one without repeating that test.

## Tests

```bash
make test          # the suite
make eval          # replay every recorded caller turn through the engine
```

Conventions that matter more than coverage:

- **A test's name is a sentence about behaviour**, not the function it calls.
- **Every regression test says what was heard or seen before the fix**, in the
  docstring. The failures here are subtle and a bare assertion does not
  survive its author.
- **Stub the model, never the contract.** Tests fake `generate()` and assert
  on what the adapter *sent* — the language, the clip, the transcript —
  because the model being wrong is not the failure mode; asking it wrongly is.
- `tests/eval/expectations.jsonl` pins caller lines from real calls to what
  the reply must and must not contain. Add one whenever a transcript exposes
  something.

## Running it

```bash
make run                       # mock profile: whole product, no models
make mac                       # MLX models on Apple silicon
docker compose up -d --build   # the GPU stack
```

The mock profile is the development default and the demo-day fallback. Keep it
working: it is the only configuration that runs everywhere.

## Before you push

- `make test` green.
- If you touched a cache key, say in the commit message that a re-render is
  needed, and why.
- If you touched the script, the personas or a voice, run `make prerender`.
- If you changed something a caller would hear, listen to it. The console's
  SAY box and *Voice samples* panel exist for that.
- Never commit real customer data. `personas.py` is synthetic and must stay
  synthetic; `logs/calls.jsonl` becomes a PDPA liability the moment it is not.
