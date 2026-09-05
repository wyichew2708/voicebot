"""TTS sidecar for the CUDA deployment.

Only improvised lines reach this service — the seven scripted turns are served
from voices/cache, which is why a slow, good-sounding model is affordable for
those and a fast one is needed here.

One process, one engine. The default is Chatterbox multilingual on CUDA via
torch — the *same checkpoint* as the Mac pre-render path, so a line generated
here and a line from the cache sound like one speaker. The other engines exist
so a candidate model can be tried behind the same `/tts` contract without the
console, the backend or the cache learning anything new:

    python scripts/tts_sidecar.py --engine cosyvoice3 --port 8803
    TTS_ENGINE=f5 python scripts/tts_sidecar.py --port 8804

Each engine is a thin adapter: load once, then turn (text, language, reference
clip, reference transcript) into float audio at the model's own rate. What an
engine cannot do is refused with a 400 that says so — an English-only model
handed a Mandarin line, a cloning model handed no clip — rather than rendered
into something of plausible length that nothing downstream can tell apart from
speech. See docs/tts-models.md for what each one is and how to install it.

It renders one fragment in one language and nothing more. Splitting a mixed
script line, joining the pieces and putting the result on the voice's own
pitch all happen in `runtime/cuda_backend.py`, which does it by calling the
same code the Mac uses rather than a second implementation of it.
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import sys
import time
import wave
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

log = logging.getLogger("tts-sidecar")
app = FastAPI(title="voicebot TTS sidecar")
_state: dict = {}

ROOT = Path(__file__).resolve().parents[1]
REFS = {"male": ROOT / "voices/refs/male.wav",
        "female": ROOT / "voices/refs/female.wav"}

DEFAULT_ENGINE = "chatterbox"


class Unsupported(ValueError):
    """The engine cannot do what this request asks. A 400, not a guess."""


# ----------------------------------------------------------------- chatterbox

#: The multilingual class has moved between releases of `chatterbox-tts`.
#: Try the known homes and fail at boot naming what was tried — the failure
#: this replaces was silent: the English-only class loaded happily, ignored
#: the language, and read Mandarin through the English phonemiser.
_MULTILINGUAL = (("chatterbox.mtl_tts", "ChatterboxMultilingualTTS"),
                 ("chatterbox.tts", "ChatterboxMultilingualTTS"),
                 ("chatterbox", "ChatterboxMultilingualTTS"))

#: What the multilingual checkpoint is documented to speak. Malay is on the
#: list; Tamil is not.
CHATTERBOX_LANGS = frozenset(
    "ar da de el en es fi fr he hi it ja ko ms nl no pl pt ru sv sw tr zh".split())


def _multilingual_class():
    import importlib
    for module, name in _MULTILINGUAL:
        try:
            return getattr(importlib.import_module(module), name)
        except (ImportError, AttributeError):
            continue
    raise RuntimeError(
        "no multilingual Chatterbox class found; tried "
        + ", ".join(f"{m}.{n}" for m, n in _MULTILINGUAL)
        + ". The English-only ChatterboxTTS is NOT a substitute: it ignores "
          "the language and reads Mandarin through the English phonemiser.")


def _tensor_audio(wav: Any):
    """A torch tensor, or something already array-like, as float32 samples."""
    import numpy as np
    if hasattr(wav, "detach"):
        wav = wav.squeeze().detach().cpu().numpy()
    return np.asarray(wav, dtype=np.float32).squeeze()


# -------------------------------------------------------------------- engines

class Engine:
    """One TTS model behind the `/tts` contract.

    `languages` is what the model is documented to speak; a request outside
    it is a 400. `clones` says whether a reference clip changes the speaker —
    a preset-voice model ignores it, and says so once in the log rather than
    silently rendering a stranger.
    """
    name = ""
    model_id = ""
    languages: frozenset[str] | None = None      # None: no fixed list
    clones = True
    #: What to install; printed when the import fails so the fix is named.
    needs = ""

    def load(self, device: str) -> None:            # pragma: no cover - models
        raise NotImplementedError

    def synth(self, text: str, lang: str, ref: Path | None,
              ref_text: str | None) -> tuple[Any, int]:  # pragma: no cover
        raise NotImplementedError

    def supports(self, lang: str) -> bool:
        return self.languages is None or lang in self.languages


class Chatterbox(Engine):
    name = "chatterbox"
    model_id = "ResembleAI/chatterbox (multilingual v3)"
    languages = CHATTERBOX_LANGS
    needs = "pip install chatterbox-tts"

    def load(self, device: str) -> None:            # pragma: no cover - model
        _state["m"] = _multilingual_class().from_pretrained(device=device)

    def synth(self, text, lang, ref, ref_text):
        model = _state["m"]
        wav = model.generate(text, language_id=lang,
                             audio_prompt_path=str(ref) if ref else None,
                             temperature=0.5)
        return _tensor_audio(wav), int(getattr(model, "sr", 24000))


class ChatterboxTurbo(Engine):
    """Resemble's low-latency English model. Takes [laugh] [chuckle] [cough]
    inline; single-step decoder. English only — so on this product it can
    only ever be the *English* voice, and a Mandarin call needs another."""
    name = "chatterbox-turbo"
    model_id = "ResembleAI/chatterbox-turbo"
    languages = frozenset({"en"})
    needs = "pip install chatterbox-tts  (>= the release that ships chatterbox.tts_turbo)"
    nano = False

    def load(self, device: str) -> None:            # pragma: no cover - model
        from chatterbox.tts_turbo import ChatterboxTurboTTS
        _state["m"] = ChatterboxTurboTTS.from_pretrained(device=device, nano=self.nano)

    def synth(self, text, lang, ref, ref_text):
        model = _state["m"]
        if ref is None and getattr(model, "conds", None) is None:
            raise Unsupported(f"{self.name} needs a reference clip (ref_audio)")
        # No language_id: the model has no such argument. The 400 upstream is
        # what keeps a Mandarin line from reaching this call at all.
        wav = model.generate(text, audio_prompt_path=str(ref) if ref else None)
        return _tensor_audio(wav), int(getattr(model, "sr", 24000))


class ChatterboxNano(ChatterboxTurbo):
    name = "chatterbox-nano"
    model_id = "ResembleAI/chatterbox-nano"
    nano = True


class CosyVoice3(Engine):
    """Fun-CosyVoice3-0.5B. Apache 2.0; zh/en/ja/ko/de/es/fr/it/ru plus
    Chinese dialects; no Malay or Tamil. Streams, clones, and is the model
    the Singlish fine-tuning paper used alongside Chatterbox.

    Not on PyPI as a package that works: the repo is cloned and put on
    sys.path, which is what its own README does. COSYVOICE_HOME points at
    the clone, COSYVOICE_MODEL at a local model dir or a HF repo id.
    """
    name = "cosyvoice3"
    model_id = os.environ.get("COSYVOICE_MODEL", "FunAudioLLM/Fun-CosyVoice3-0.5B-2512")
    languages = frozenset("zh en ja ko de es fr it ru yue".split())
    needs = ("git clone --recursive https://github.com/FunAudioLLM/CosyVoice $COSYVOICE_HOME "
             "&& pip install -r $COSYVOICE_HOME/requirements.txt")
    #: CosyVoice 3 carries a system prompt in front of its text — the
    #: repo's own examples put it on every call, and without it the model
    #: is being prompted differently from how it was trained.
    SYSTEM = "You are a helpful assistant.<|endofprompt|>"

    def load(self, device: str) -> None:            # pragma: no cover - model
        home = os.environ.get("COSYVOICE_HOME", "/opt/CosyVoice")
        for p in (home, os.path.join(home, "third_party", "Matcha-TTS")):
            if p not in sys.path:
                sys.path.insert(0, p)
        from cosyvoice.cli.cosyvoice import AutoModel
        model_dir = self.model_id
        if not os.path.isdir(model_dir):
            from huggingface_hub import snapshot_download
            model_dir = snapshot_download(model_dir)
        _state["m"] = AutoModel(model_dir=model_dir, fp16=(device == "cuda"))

    def synth(self, text, lang, ref, ref_text):
        import numpy as np
        model = _state["m"]
        if ref is None:
            raise Unsupported(f"{self.name} clones from a reference clip and none was given")
        v3 = type(model).__name__ == "CosyVoice3"
        prefix = self.SYSTEM if v3 else ""
        if ref_text:
            # Zero-shot proper: the clip and what is said on it. Better
            # similarity than the transcript-free path.
            gen = model.inference_zero_shot(text, prefix + ref_text, str(ref), stream=False)
        else:
            gen = model.inference_cross_lingual(prefix + text, str(ref), stream=False)
        parts = [_tensor_audio(j["tts_speech"]) for j in gen]
        audio = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
        return audio, int(model.sample_rate)


class F5(Engine):
    """F5-TTS. MIT code, but the pretrained weights are CC-BY-NC-4.0 (Emilia
    training data) — a trial engine, not a production one, unless a
    commercially licensed checkpoint is pointed at with F5_MODEL/F5_CKPT."""
    name = "f5"
    model_id = os.environ.get("F5_MODEL", "F5TTS_v1_Base")
    languages = frozenset({"en", "zh"})
    needs = "pip install f5-tts"

    def load(self, device: str) -> None:            # pragma: no cover - model
        from f5_tts.api import F5TTS
        _state["m"] = F5TTS(model=self.model_id, ckpt_file=os.environ.get("F5_CKPT", ""),
                            device=device)

    def synth(self, text, lang, ref, ref_text):
        model = _state["m"]
        if ref is None:
            raise Unsupported(f"{self.name} clones from a reference clip and none was given")
        # An empty ref_text makes F5 transcribe the clip with Whisper on
        # every call — slow, and a transcription error becomes a cloning
        # error. Put the transcript in a .txt beside the clip instead.
        if not ref_text:
            log.warning("%s: no transcript for %s — F5 will transcribe it itself",
                        self.name, ref.name)
        wav, sr, _spec = model.infer(ref_file=str(ref), ref_text=ref_text or "",
                                     gen_text=text, show_info=lambda *a, **k: None)
        return _tensor_audio(wav), int(sr)


class IndexTTS2(Engine):
    """IndexTTS-2, 1.7B. zh/en, expressive, bilibili Model Use License
    (commercial use allowed below 100M MAU / RMB 1bn revenue; PRC law).
    Heaviest engine here; installed from the repo, not PyPI."""
    name = "indextts2"
    model_id = "IndexTeam/IndexTTS-2"
    languages = frozenset({"zh", "en"})
    needs = ("git clone https://github.com/index-tts/index-tts $INDEXTTS_HOME && "
             "pip install -e $INDEXTTS_HOME && hf download IndexTeam/IndexTTS-2 "
             "--local-dir $INDEXTTS_HOME/checkpoints")

    def load(self, device: str) -> None:            # pragma: no cover - model
        home = os.environ.get("INDEXTTS_HOME", "/opt/index-tts")
        if home not in sys.path:
            sys.path.insert(0, home)
        from indextts.infer_v2 import IndexTTS2 as Model
        ckpt = os.environ.get("INDEXTTS_MODEL", os.path.join(home, "checkpoints"))
        _state["m"] = Model(cfg_path=os.path.join(ckpt, "config.yaml"), model_dir=ckpt,
                            use_fp16=(device == "cuda"), device=device)

    def synth(self, text, lang, ref, ref_text):
        import numpy as np
        model = _state["m"]
        if ref is None:
            raise Unsupported(f"{self.name} clones from a reference clip and none was given")
        out = model.infer(spk_audio_prompt=str(ref), text=text, output_path=None,
                          verbose=False)
        # `infer` hands back (sampling_rate, int16 samples) — via a generator
        # in some releases and directly in others.
        if not isinstance(out, tuple):
            last = None
            for last in out:
                pass
            out = last
        sr, wav = out
        audio = np.asarray(wav).squeeze().astype(np.float32)
        if np.issubdtype(np.asarray(wav).dtype, np.integer):
            audio = audio / 32768.0
        return audio, int(sr)


class Kokoro(Engine):
    """Kokoro-82M through the `kokoro` package. Apache 2.0, ~0.2 s a line on
    a CPU, no cloning at all: the speaker is a preset picked by language,
    which is why this is the fallback voice and never the premium one."""
    name = "kokoro"
    model_id = "hexgrad/Kokoro-82M"
    # Kokoro speaks more than these; these are the two this product speaks
    # and the two it has presets picked for.
    languages = frozenset({"en", "zh"})
    clones = False
    needs = "pip install kokoro 'misaki[zh]' espeakng-loader"
    #: Kokoro's own code for each language, and which preset reads it. The
    #: presets match the clips the cloning voices use for Mandarin, so a
    #: Kokoro fallback line is at least the same Mandarin speaker.
    CODES = {"en": "a", "zh": "z"}
    PRESETS = {("male", "en"): "am_michael", ("male", "zh"): "zm_yunjian",
               ("female", "en"): "af_heart", ("female", "zh"): "zf_xiaobei"}

    def preset(self, gender: str, lang: str) -> str:
        """KOKORO_VOICE_EN / KOKORO_VOICE_ZH win; else the gender's preset;
        else the male one. `gender` is what the request says, or the voice
        id when it happens to be one of the two."""
        return (os.environ.get(f"KOKORO_VOICE_{lang.upper()}")
                or self.PRESETS.get((gender, lang))
                or self.PRESETS[("male", lang)])

    def load(self, device: str) -> None:            # pragma: no cover - model
        from kokoro import KPipeline                # noqa: F401  fail at boot
        _state["m"] = {}                            # pipelines, one per language

    def _pipeline(self, lang: str):
        pipes = _state["m"]
        code = self.CODES[lang]
        if code not in pipes:
            from kokoro import KPipeline
            pipes[code] = KPipeline(lang_code=code)
        return pipes[code]

    def synth(self, text, lang, ref, ref_text, gender: str = "male"):
        import numpy as np
        parts = [_tensor_audio(audio) for _gs, _ps, audio in
                 self._pipeline(lang)(text, voice=self.preset(gender, lang))]
        audio = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
        return audio, 24000


class Fish(Engine):
    """Fish Speech S2 (fishaudio/s2-pro) in-process, through the same
    inference engine its own api_server wraps: a text-to-semantic model on a
    worker thread and the DAC decoder. ~5B parameters, 80+ languages, needs
    the clip's transcript to clone. Research licence — for experiments.

    Installed from the repository (it pins its own torch), which is why it is
    one image on its own. FISH_HOME is the clone, FISH_MODEL the checkpoint
    directory holding the model files and codec.pth.
    """
    name = "fish"
    model_id = os.environ.get("FISH_MODEL", "checkpoints/s2-pro")
    languages = None
    needs = ("git clone https://github.com/fishaudio/fish-speech $FISH_HOME && "
             "pip install -e '$FISH_HOME[cu129]' && hf download fishaudio/s2-pro "
             "--local-dir $FISH_HOME/checkpoints/s2-pro")

    def load(self, device: str) -> None:            # pragma: no cover - model
        import torch
        home = os.environ.get("FISH_HOME", "/opt/fish-speech")
        if home not in sys.path:
            sys.path.insert(0, home)
        from fish_speech.inference_engine import TTSInferenceEngine
        from fish_speech.models.dac.inference import load_model as load_decoder
        from fish_speech.models.text2semantic.inference import launch_thread_safe_queue
        ckpt = self.model_id
        if not os.path.isabs(ckpt) and not os.path.isdir(ckpt):
            ckpt = os.path.join(home, ckpt)
        precision = torch.bfloat16 if device == "cuda" else torch.float32
        llama = launch_thread_safe_queue(checkpoint_path=ckpt, device=device,
                                         precision=precision, compile=False)
        decoder = load_decoder(config_name=os.environ.get("FISH_DECODER_CONFIG", "modded_dac_vq"),
                               checkpoint_path=os.path.join(ckpt, "codec.pth"), device=device)
        _state["m"] = TTSInferenceEngine(llama_queue=llama, decoder_model=decoder,
                                         precision=precision, compile=False)

    def _request(self, text: str, ref: Path | None, ref_text: str | None):
        from fish_speech.utils.schema import ServeReferenceAudio, ServeTTSRequest
        refs = []
        if ref is not None:
            if not ref_text:
                raise Unsupported(f"{self.name} needs the reference clip's transcript "
                                  "(ref_text, or a .txt beside the clip)")
            refs.append(ServeReferenceAudio(audio=ref.read_bytes(), text=ref_text))
        return ServeTTSRequest(text=text, references=refs, format="wav", streaming=False)

    def synth(self, text, lang, ref, ref_text):
        import numpy as np
        if ref is None:
            raise Unsupported(f"{self.name} clones from a reference clip and none was given")
        engine = _state["m"]
        final = None
        for result in engine.inference(self._request(text, ref, ref_text)):
            if result.code == "error":
                raise RuntimeError(f"fish-speech: {result.error}")
            if result.code == "final":
                final = result.audio
        if final is None:
            raise RuntimeError("fish-speech produced no audio")
        sr, audio = final
        return np.asarray(audio, dtype=np.float32).squeeze(), int(sr)


class FishServer(Engine):
    """Fish Speech reached over HTTP: the model runs in fish-speech's own
    `tools/api_server.py` (its recommended serving path, SGLang-backed), and
    this forwards to it. Same rules as the in-process engine."""
    name = "fish-server"
    model_id = "fishaudio/s2-pro via api_server"
    languages = None
    needs = ("pip install ormsgpack; run fish-speech's api_server and set FISH_URL "
             "(default http://127.0.0.1:8888)")

    def load(self, device: str) -> None:            # pragma: no cover - service
        import urllib.request
        import ormsgpack                            # noqa: F401  fail at boot
        url = os.environ.get("FISH_URL", "http://127.0.0.1:8888").rstrip("/")
        with urllib.request.urlopen(url + "/v1/health", timeout=5) as r:
            if r.status != 200:
                raise RuntimeError(f"fish-speech api_server at {url} answered {r.status}")
        _state["m"] = url

    def synth(self, text, lang, ref, ref_text):
        import urllib.request
        import ormsgpack
        url = _state["m"]
        refs = []
        if ref is not None:
            if not ref_text:
                raise Unsupported(f"{self.name} needs the reference clip's transcript "
                                  "(ref_text, or a .txt beside the clip)")
            refs.append({"audio": ref.read_bytes(), "text": ref_text})
        body = {"text": text, "format": "wav", "references": refs,
                "normalize": True, "streaming": False}
        req = urllib.request.Request(url + "/v1/tts", data=ormsgpack.packb(body),
                                     headers={"Content-Type": "application/msgpack"})
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
        import numpy as np
        with wave.open(io.BytesIO(raw)) as w:
            sr = w.getframerate()
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            if w.getnchannels() > 1:
                pcm = pcm.reshape(-1, w.getnchannels())[:, 0]
        return pcm.astype(np.float32) / 32768.0, sr


class VibeVoice(Engine):
    """Microsoft VibeVoice-Realtime-0.5B. MIT, ~300 ms to first audio,
    streaming text in. English only, and the speaker is one of the shipped
    preset `.pt` prompts rather than a clone of a supplied clip — so on this
    product it is a preset English voice, like Kokoro with more character.

    Installed from the repository with its `streamingtts` extra; the voice
    presets live in the clone under demo/voices/streaming_model as
    `en-Carter_man.pt`, `en-Emma_woman.pt` and so on. VIBEVOICE_HOME is the
    clone, VIBEVOICE_MODEL the HF repo or local path. The request's gender
    picks the preset — VIBEVOICE_VOICE_MALE / VIBEVOICE_VOICE_FEMALE
    (default Carter / Emma), matched by name the way the authors' own
    script matches them, so "Carter" finds en-Carter_man.
    """
    name = "vibevoice"
    model_id = os.environ.get("VIBEVOICE_MODEL", "microsoft/VibeVoice-Realtime-0.5B")
    languages = frozenset({"en"})
    clones = False
    needs = ("git clone https://github.com/microsoft/VibeVoice $VIBEVOICE_HOME && "
             "pip install -e '$VIBEVOICE_HOME[streamingtts]'")
    CFG_SCALE = 1.5
    DEFAULTS = {"male": "Carter", "female": "Emma"}

    @staticmethod
    def presets(home: str) -> dict[str, str]:
        """The shipped voice prompts, by file stem."""
        import glob
        found = glob.glob(os.path.join(home, "demo", "voices", "streaming_model",
                                       "**", "*.pt"), recursive=True)
        return {os.path.splitext(os.path.basename(p))[0]: p for p in sorted(found)}

    @staticmethod
    def match(presets: dict[str, str], want: str) -> str | None:
        """The stem for a name: exact, else the one stem containing it
        (case-insensitive), else None. "Carter" -> "en-Carter_man"."""
        if want in presets:
            return want
        hits = [k for k in presets if want.lower() in k.lower()]
        return hits[0] if len(hits) == 1 else None

    def preset_for(self, gender: str) -> str:
        want = (os.environ.get(f"VIBEVOICE_VOICE_{gender.upper()}")
                or os.environ.get("VIBEVOICE_VOICE")
                or self.DEFAULTS.get(gender, self.DEFAULTS["male"]))
        presets = _state["m"]["presets"]
        stem = self.match(presets, want)
        if stem is None:
            stem = next(iter(presets))
            log.warning("no VibeVoice preset matching %r; using %s (have: %s)",
                        want, stem, ", ".join(presets))
        return stem

    def load(self, device: str) -> None:            # pragma: no cover - model
        import torch
        from transformers.cache_utils import DynamicCache
        from transformers.modeling_outputs import BaseModelOutputWithPast
        from vibevoice.modular.modeling_vibevoice_streaming_inference import (
            VibeVoiceStreamingForConditionalGenerationInference as Model)
        from vibevoice.processor.vibevoice_streaming_processor import (
            VibeVoiceStreamingProcessor)

        home = os.environ.get("VIBEVOICE_HOME", "/opt/VibeVoice")
        presets = self.presets(home)
        if not presets:
            raise RuntimeError(f"no VibeVoice voice presets under {home}/demo/voices/"
                               "streaming_model — VIBEVOICE_HOME must point at the clone")

        processor = VibeVoiceStreamingProcessor.from_pretrained(self.model_id)
        dtype = torch.bfloat16 if device == "cuda" else torch.float32
        try:
            model = Model.from_pretrained(
                self.model_id, torch_dtype=dtype, device_map=device,
                attn_implementation="flash_attention_2" if device == "cuda" else "sdpa")
        except Exception as exc:
            if device != "cuda":
                raise
            log.warning("flash_attention_2 unavailable (%s); falling back to sdpa, "
                        "which the authors say is less tested", exc)
            model = Model.from_pretrained(self.model_id, torch_dtype=dtype,
                                          device_map=device, attn_implementation="sdpa")
        model.eval()
        model.set_ddpm_inference_steps(num_steps=5)
        _state["m"] = {"model": model, "processor": processor, "presets": presets,
                       "prompts": {}, "device": device}

    def prompt_for(self, gender: str):
        """The cached voice prompt for this gender's preset, loaded once."""
        m = _state["m"]
        stem = self.preset_for(gender)
        if stem not in m["prompts"]:
            import torch
            from transformers.cache_utils import DynamicCache
            from transformers.modeling_outputs import BaseModelOutputWithPast
            with torch.serialization.safe_globals([BaseModelOutputWithPast, DynamicCache]):
                m["prompts"][stem] = torch.load(m["presets"][stem], map_location=m["device"],
                                                weights_only=True)
        return m["prompts"][stem]

    def synth(self, text, lang, ref, ref_text, gender: str = "male"):
        import copy
        import torch
        m = _state["m"]
        model, processor = m["model"], m["processor"]
        prompt = self.prompt_for(gender)
        inputs = processor.process_input_with_cached_prompt(
            text=text, cached_prompt=prompt, padding=True,
            return_tensors="pt", return_attention_mask=True)
        for k, v in list(inputs.items()):
            if torch.is_tensor(v):
                inputs[k] = v.to(m["device"])
        out = model.generate(**inputs, max_new_tokens=None, cfg_scale=self.CFG_SCALE,
                             tokenizer=processor.tokenizer,
                             generation_config={"do_sample": False}, verbose=False,
                             all_prefilled_outputs=copy.deepcopy(prompt))
        speech = out.speech_outputs[0] if getattr(out, "speech_outputs", None) else None
        if speech is None:
            raise RuntimeError("VibeVoice produced no audio")
        return _tensor_audio(speech.float() if hasattr(speech, "float") else speech), 24000


ENGINES: dict[str, type[Engine]] = {
    e.name: e for e in (Chatterbox, ChatterboxTurbo, ChatterboxNano, CosyVoice3,
                        F5, IndexTTS2, Kokoro, Fish, FishServer, VibeVoice)}


# ------------------------------------------------------------------- serving

def _reference(named: str | None, voice: str) -> Path | None:
    """The clip to clone: named by the caller, else looked up by voice id.

    Named wins, and a name that does not resolve is refused rather than
    quietly replaced. The console resolves the clip per voice *and per
    language* from its profile — a Mandarin line clones a Mandarin speaker —
    and the two-entry table below knows nothing of that. Rendering against
    the model's default speaker is not an error anything downstream can see;
    it is a stranger's voice in the middle of the call.
    """
    if named:
        path = Path(named)
        if not path.is_absolute():
            path = ROOT / path
        return path if path.exists() else None
    ref = REFS.get(voice)
    return ref if ref and ref.exists() else None


def _reference_text(ref: Path | None, given: str | None) -> str | None:
    """What is said on the reference clip.

    CosyVoice 3 and F5-TTS clone measurably better when told, and Fish will
    not clone without it. Sent with the request when the profile has it,
    else read from a `.txt` beside the clip, else absent — Chatterbox never
    needed one and every existing clip has none.
    """
    if given:
        return str(given).strip() or None
    if ref is None:
        return None
    txt = ref.with_suffix(".txt")
    if txt.exists():
        try:
            return txt.read_text(encoding="utf-8").strip() or None
        except OSError:                                 # pragma: no cover
            return None
    return None


def _device() -> str:
    if "dev" not in _state:
        try:
            import torch
            _state["dev"] = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:                              # engines without torch
            _state["dev"] = "cpu"
    return _state["dev"]


def _engine() -> Engine:
    """The one engine this process runs, loaded on first use.

    Chosen by --engine, else TTS_ENGINE, else Chatterbox. An unknown name is
    an error that names the choices; a missing dependency is an error that
    names the install.
    """
    if "engine" not in _state:
        name = str(_state.get("engine_name") or os.environ.get("TTS_ENGINE")
                   or DEFAULT_ENGINE).lower()
        cls = ENGINES.get(name)
        if cls is None:
            raise RuntimeError(f"unknown TTS engine {name!r}; one of: "
                               + ", ".join(sorted(ENGINES)))
        _state["engine"] = cls()
    engine: Engine = _state["engine"]
    if "m" not in _state:
        dev = _device()
        log.info("loading %s (%s) on %s", engine.name, engine.model_id, dev)
        try:
            engine.load(dev)
        except ImportError as exc:
            raise RuntimeError(f"engine {engine.name!r} is not installed ({exc}). "
                               f"Install: {engine.needs}") from exc
    return engine


def _model():
    """Kept for callers that only knew Chatterbox: the loaded model object."""
    _engine()
    return _state["m"]


@app.get("/health")
async def health() -> JSONResponse:
    engine = _state.get("engine")
    if engine is None and "m" in _state:
        engine = _engine()          # a model is loaded: name the engine it belongs to
    return JSONResponse({
        "ready": "m" in _state,
        "device": _state.get("dev", "?"),
        "engine": engine.name if engine else None,
        "model": engine.model_id if engine else None,
        "languages": sorted(engine.languages) if engine and engine.languages else None,
        "clones": engine.clones if engine else None,
    })


@app.post("/tts")
async def tts(request: Request) -> Response:
    import numpy as np

    body = await request.json()
    text = body.get("text", "")
    voice = body.get("voice", "male")
    # The model's own code for the language, resolved by the caller. Not
    # optional and not defaulted: the checkpoint falls back to English and
    # phonemises whatever it is given accordingly, which is audible as
    # English-sounding nonsense rather than as an error.
    lang = str(body.get("lang") or "").lower()
    target_sr = int(body.get("sample_rate", 16000))
    if not text.strip():
        return Response(status_code=400, content=b"empty text")
    if not lang:
        return Response(status_code=400, content=b"missing lang")

    try:
        engine = _engine()
    except RuntimeError as exc:
        return Response(status_code=503, content=str(exc).encode())
    if not engine.supports(lang):
        # Said, not guessed. A single-language model does not fail on the
        # wrong language — it reads the text as if it were its own.
        return Response(
            status_code=400,
            content=(f"language {lang!r} is not supported by engine {engine.name!r} "
                     f"(speaks: {', '.join(sorted(engine.languages or []))})").encode())

    ref = _reference(body.get("ref_audio"), voice)
    if ref is None and body.get("ref_audio"):
        return Response(status_code=400,
                        content=f"reference clip not found: {body['ref_audio']}".encode())
    if ref is not None and not engine.clones and not _state.get("warned_no_clone"):
        _state["warned_no_clone"] = True
        log.warning("%s does not clone: the reference clip is ignored and a "
                    "preset speaker is used", engine.name)
    ref_text = _reference_text(ref, body.get("ref_text"))

    # Which preset a non-cloning engine picks. The console sends the voice's
    # gender; an older caller sends only a voice id, which is enough when
    # the id is one of the two.
    gender = str(body.get("gender") or "").lower()
    if gender not in ("male", "female"):
        gender = voice if voice in ("male", "female") else "male"
    t0 = time.time()
    try:
        if isinstance(engine, (Kokoro, VibeVoice)):
            audio, src_sr = engine.synth(text, lang, ref, ref_text, gender=gender)
        else:
            audio, src_sr = engine.synth(text, lang, ref, ref_text)
    except Unsupported as exc:
        return Response(status_code=400, content=str(exc).encode())
    audio = np.asarray(audio, dtype=np.float32).squeeze()
    if audio.ndim == 0:
        audio = audio.reshape(1)

    if src_sr != target_sr and len(audio) > 1:
        # Linear resample: adequate for speech, and avoids another dependency.
        n = int(len(audio) * target_sr / src_sr)
        audio = np.interp(np.linspace(0, len(audio) - 1, n),
                          np.arange(len(audio)), audio).astype(np.float32)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(target_sr)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    ms = int((time.time() - t0) * 1000)
    log.info("%d ms  %s [%s] %r", ms, engine.name, lang, text[:48])
    return Response(content=buf.getvalue(), media_type="audio/wav",
                    headers={"X-Engine": engine.name, "X-Synth-Ms": str(ms)})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8802)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--engine", default=os.environ.get("TTS_ENGINE", DEFAULT_ENGINE),
                    choices=sorted(ENGINES),
                    help="which model this process serves (default: $TTS_ENGINE or chatterbox)")
    ap.add_argument("--list-engines", action="store_true")
    args = ap.parse_args()
    if args.list_engines:
        for name, cls in sorted(ENGINES.items()):
            langs = ", ".join(sorted(cls.languages)) if cls.languages else "any"
            print(f"{name:17} {cls.model_id:45} clones={cls.clones!s:5} langs: {langs}")
        return
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    _state["engine_name"] = args.engine
    _engine()                   # fail at boot, not on the first call
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
