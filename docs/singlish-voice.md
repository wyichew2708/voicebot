# Making it actually sound Singlish

Notes from investigating why the voice still doesn't sound Singaporean, and what
actually fixes it. **Correcting earlier advice in this repo: zero-shot voice cloning
from a reference clip does not solve this.** There is published work measuring
exactly that.

## Three layers, not one

"Singlish" bundles three separate problems with very different difficulty:

| Layer | Example | Status |
|---|---|---|
| **Lexicon & syntax** | "correct or not", "already", "can already", "lah" | ✅ done — `register: singlish` |
| **Phonetics** | clipped final consonants, glottal stops, no vowel-length contrast | ❌ needs a Singlish-trained model |
| **Prosody** | **syllable-timed** rhythm; particles carry pitch | ❌ the hard one |

The third is why writing "lah" into the script isn't enough. Singapore English is
*syllable-timed* — every syllable gets roughly equal weight, giving the chopped,
faster feel — where British and American English are stress-timed. And the
particles are **tonal**: "lah" at different pitches means different things. A
stress-timed TTS reading "lah" as an ordinary unstressed syllable sounds wrong
even with the vocabulary correct.

## Zero-shot cloning does not fix it

["Singlish, Can or Not? Fine-Tuning and Evaluating Zero-Shot TTS for Singapore
English"](https://arxiv.org/abs/2607.23027) measured this directly. Its finding:

> off-the-shelf zero-shot systems **reproduce a speaker's timbre while flattening
> the accent toward generic English**

They keep the discourse particles *lexically* and lose the prosody and phonetic
detail. So cloning a Singaporean colleague gives you a voice that sounds like
them, speaking with the base model's rhythm. That is why the earlier
`reference_audio` suggestion in this repo was not the answer — the plumbing is
still there and still useful for *timbre*, but it will not deliver the accent.

## Fine-tuning does fix it, and generalises

Same paper, the useful half. They fine-tuned **Chatterbox** and **CosyVoice 3** on
IMDA National Speech Corpus Part 3:

| | |
|---|---|
| Speakers | 50 in-domain |
| Data | 55.5 hours, 19,626 utterances, gender-balanced |
| Chatterbox accent similarity | 0.5114 → **0.6376** in-domain (+0.126) |
| CosyVoice 3 accent similarity | 0.5771 → 0.6798 |
| **Held-out speakers (42 unseen)** | **0.5617** vs 0.5114 off-the-shelf |

That last row is the important one: the gain survives on speakers never seen in
training, so **the model learns the Singlish accent itself** rather than
memorising voices.

### The counter-intuitive part

Fine-tuning made the conventional quality metrics *worse*:

- WER rose from an "overly clean" 11.48% toward **17.31%, which is real Singlish WER**
- UT-MOS naturalness fell 3.34 → 2.75

That is the model becoming less generically polished and more like actual
Singaporean speech. **Do not tune for MOS here** — optimising naturalness scores
optimises the accent back out.

## Options, best first

1. **A Singaporean voice actor records the six pre-rendered turns.**
   Perfect phonetics, prosody and particle tone, no model risk, and it covers
   most of every call because six of seven turns are fixed wording. Add a
   recorded digit/name/date bank and the slot values are covered too — classic
   concatenative IVR, and for a demo it beats every synthetic option here.
   **This is the recommendation for the demo.**

2. **Fine-tune Chatterbox on IMDA NSC.** The published recipe above, and
   Chatterbox is already supported by mlx-audio, so the result drops straight
   into this stack with a config change. The paper includes its preprocessing
   pipeline (ASR filtering, VAD, quality screening). Needs NSC access and
   GPU time; this is the right *product* answer.
   **This is the recommendation for production.**

3. **Try `mesolitica/VITS-female-singlish` today.** Already trained on the
   Singapore National Speech Corpus. Caveats: standard VITS in PyTorch at
   22 kHz (`model.pth`, not MLX — another sidecar), it needs malaya-speech's
   symbol set, and it is a *female* voice, which suits the Michelle voices and not the Michael ones. Worth an
   hour purely to hear what NSC-trained Singlish TTS sounds like before
   committing to option 2.

4. **`MERaLiON/MERaLiON-OmniVoice-Hokkien-TTS`** — A*STAR's Hokkien TTS with
   native tone and rhythm, built because generic Chinese TTS gives Mandarin
   pronunciation for Hokkien text. Not Singapore English, but the same class of
   problem solved by the same route, and relevant if Hokkien ever enters scope.

## What not to do

- Don't expect a preset voice to work. Kokoro ships American, British, Chinese,
  Japanese and a few European voices. There is no Singaporean one anywhere in
  mlx-audio.
- Don't rely on cloning alone (§ above).
- Don't spell words phonetically in the script to fake the accent
  ("oreddy", "correc'"). It corrupts the text the compliance team signed off,
  breaks the ASR-facing transcript, and produces a caricature rather than an
  accent.
