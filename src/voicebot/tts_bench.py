"""A TTS benchmark that says what this product says.

Public leaderboards rank models on audiobook prose. This call says "S$412",
"TH-4471-0093", "#08-212", "MediShield Life" and "Tan先生", in two languages,
in a register the model was not trained on — and whether a candidate is any
good here is a question about *those* lines. So the sentence set is the
script itself plus the things the script is made of: money, dates, policy
numbers, addresses, emails, scheme names, surnames, and the Singlish
rewording, in English and Mandarin.

Two ways to send a line, because they answer different questions:

- **product** (default): the line goes through the same path a live call
  uses — `spoken.segment_by_script` spells the identifiers, splits a mixed
  script line, and each piece is rendered in its own language. This measures
  what a caller would hear.
- **raw**: the written line goes to the model whole. This measures the
  model's own text normalisation — does it read "S$1,284.60" as money? — which
  is what the deterministic layer in `spoken.py` exists to stop mattering.

What is measured per line: wall time, audio length, real-time factor, median
pitch and how many voiced frames that pitch rests on. Per model: p50/p95
latency, RTF, failures, and **speaker drift** — the spread of per-line pitch
in semitones, the thing that reads as the agent changing mid-call and that
this repo has spent most of its voice work on. Round-trip the audio through
an ASR and it also reports character error rate against what was said.

Quality — does it sound like a person, does it sound Singaporean — is not a
number this file produces. It produces the page where a person decides.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import math
import re
import statistics
import time
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from . import pcm as P
from .spoken import segment_by_script

log = logging.getLogger("voicebot.tts_bench")

#: The shipped male voice's clips, one per language, as the profile has them.
DEFAULT_REFS = {"en": "voices/refs/male.wav", "zh": "voices/refs/zm_yunjian.wav"}


# ------------------------------------------------------------------ the set

@dataclass(frozen=True)
class Line:
    id: str
    group: str
    lang: str
    text: str

    @property
    def said(self) -> str:
        """What the voice is actually handed in product mode: identifiers
        spelled, digits in a Mandarin line in Mandarin, names respelled."""
        return "".join(frag for frag, _ in segment_by_script(self.text, self.lang))


# The things an insurance call is made of. Written the way they appear in a
# record or a letter — the point is to see what each model, and the spoken
# layer in front of it, does with them.
_STRESS_EN = (
    ("money", "Your annual premium is S$1,284.60, after the 23.5% loyalty discount."),
    ("money-round", "The renewal premium for five years is S$412."),
    ("deductible", "The deductible is S$3,500, and co-insurance applies above that."),
    ("scheme", "MediShield Life and your Integrated Shield Plan are separate from this policy."),
    ("cpf", "CPF MediSave cannot be used to pay a home insurance premium."),
    ("policy", "Your policy number is TH-4471-0093."),
    ("claim", "Your claim reference is HC-2026-018842."),
    ("email", "We have your email as a.tan@example.sg."),
    ("address", "The insured address is Jurong West Street 4, #08-212."),
    ("phone", "You can reach us on 6887 8777."),
    ("date", "Your due date is 10 February 2026."),
    ("sums", "Contents are insured for S$35,000 and renovations for S$60,000."),
    ("names", "Mr Tan, Madam Yeo, Mr Ng and Mr Chew are all on today's list."),
    ("brand", "This is Michelle calling from Etiqa Insurance about your Tiq Home policy."),
    ("singlish", "Can already lah, just renew before the due date, then reply the email can."),
    ("question", "Am I speaking with Mr Tan? Is now a good time?"),
)

_STRESS_ZH = (
    ("money", "您的年保费是四百一十二新元，已经包含百分之二十三点五的忠诚折扣。"),
    ("money-raw", "您的年保费是S$1,284.60。"),
    ("scheme", "终身健保和综合健保双全计划与这份保单是分开的。"),
    ("cpf", "公积金保健储蓄不能用来支付家居保险的保费。"),
    ("policy", "您的保单号码是TH-4471-0093。"),
    ("email", "我们记录的电邮是a.tan@example.sg。"),
    ("address", "投保地址是Jurong West Street 4, #08-212。"),
    ("phone", "您可以拨打6887 8777联系我们。"),
    ("date", "您的保单在2026年2月10日到期。"),
    ("sums", "家居财物的保额是三万五千新元，装修的保额是六万新元。"),
    ("names", "Tan先生、Yeo女士、Ng先生和Chew先生都在今天的名单上。"),
    ("brand", "我是 Etiqa 保险的Michelle，想跟您确认您的Tiq Home保单。"),
    ("question", "请问是Tan先生本人吗？现在方便说话吗？"),
)


def sentences(langs: tuple[str, ...] = ("en", "zh"),
              groups: tuple[str, ...] | None = None,
              registers: tuple[str, ...] = ("standard", "singlish")) -> list[Line]:
    """The benchmark set: the scripted turns plus the stress lines.

    Groups: `script` (the seven turns, standard register), `singlish` (the
    rewording, English only), and one group per stress line kind. Filter with
    `groups`; every id is unique and stable, so wavs from two runs line up.
    """
    from .call import script
    from .data import personas

    out: list[Line] = []
    seen: set[tuple[str, str]] = set()

    def add(line: Line) -> None:
        if (line.lang, line.text) not in seen and line.text.strip():
            seen.add((line.lang, line.text))
            out.append(line)

    # Every persona, not one: they differ in surname, address, premium and
    # sums insured, which is exactly the part of a line a voice gets wrong.
    for n, policy in enumerate(personas.all_policies(), 1):
        for lang in langs:
            for register in registers:
                if register == "singlish" and lang != "en":
                    continue
                group = "script" if register == "standard" else "singlish"
                for turn in range(1, 8):
                    text = script.render(turn, policy, lang, register=register)
                    add(Line(f"{group}-{lang}-p{n}-t{turn}", group, lang, text))
    for lang in langs:
        stress = _STRESS_EN if lang == "en" else _STRESS_ZH if lang == "zh" else ()
        for kind, text in stress:
            add(Line(f"{kind}-{lang}", kind, lang, text))
    if groups:
        wanted = set(groups)
        out = [l for l in out if l.group in wanted]
    return out


# ------------------------------------------------------------------ targets

class Target(Protocol):
    name: str
    sample_rate: int

    async def render(self, line: Line) -> bytes: ...


class SidecarTarget:
    """One TTS sidecar (scripts/tts_sidecar.py), any engine.

    Product mode goes through `CUDABackend.synthesize` — the exact code a live
    call on the GPU box runs, segmenting and all — with a voice that has the
    reference clips but no `target_f0`, so the pitch normaliser stays out of
    the way and the model's own drift is what gets measured.
    """

    def __init__(self, name: str, url: str, sample_rate: int = 16000,
                 refs: dict[str, str] | None = None,
                 ref_texts: dict[str, str] | None = None, raw: bool = False) -> None:
        from .runtime.cuda_backend import CUDABackend

        self.name = name
        self.url = url.rstrip("/")
        self.sample_rate = sample_rate
        self.refs = dict(refs or DEFAULT_REFS)
        self.ref_texts = dict(ref_texts or {})
        self.raw = raw
        voice: dict[str, Any] = {"ref_audio": self.refs}
        if self.ref_texts:
            voice["ref_text"] = self.ref_texts
        self._backend = CUDABackend({
            "sample_rate": sample_rate,
            "tts": {"base_url": self.url,
                    "prerender": {"voices": {"bench": voice}, "default_voice": "bench"}},
        })

    async def render(self, line: Line) -> bytes:
        if self.raw:
            return await asyncio.to_thread(self._post_whole, line)
        chunks = [c async for c in self._backend.synthesize(line.text, line.lang, "bench")]
        return b"".join(chunks)

    def _post_whole(self, line: Line) -> bytes:
        """The written line, whole, to the model. Same wire contract as the
        live path — language always sent — minus the spoken layer."""
        import io
        import urllib.request

        body: dict[str, Any] = {"text": line.text, "lang": line.lang, "voice": "bench",
                                "sample_rate": self.sample_rate}
        ref = self.refs.get(line.lang) or self.refs.get("en")
        if ref:
            body["ref_audio"] = ref
        ref_text = self.ref_texts.get(line.lang)
        if ref_text:
            body["ref_text"] = ref_text
        req = urllib.request.Request(self.url + "/tts", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read()
        with wave.open(io.BytesIO(raw)) as w:
            return w.readframes(w.getnframes())


class MLXTarget:
    """An mlx-audio model rendered in-process, for the Mac. Product mode
    only: it is `PrerenderCache.render` into a scratch cache, which is the
    same call `make prerender` makes.

    A cloning model takes the reference clips. A preset-voice model
    (Kokoro, VibeVoice) takes `speaker` instead — the name mlx-audio's
    `generate(voice=...)` wants — and `lang_codes` maps this repo's `en`/`zh`
    to whatever the model calls them (`a`/`z` for Kokoro).
    """

    def __init__(self, repo: str, cache_dir: Path, sample_rate: int = 16000,
                 refs: dict[str, str] | None = None,
                 ref_texts: dict[str, str] | None = None, name: str | None = None,
                 speaker: str | None = None,
                 lang_codes: dict[str, str] | None = None) -> None:
        from .runtime.prerender import PrerenderCache

        self.name = name or repo.split("/")[-1]
        self.sample_rate = sample_rate
        voice: dict[str, Any] = ({"speaker": speaker} if speaker
                                 else {"ref_audio": dict(refs or DEFAULT_REFS)})
        if ref_texts and not speaker:
            voice["ref_text"] = dict(ref_texts)
        cfg: dict[str, Any] = {"model": repo, "cache_dir": str(cache_dir),
                               "voices": {"bench": voice}, "default_voice": "bench"}
        if lang_codes:
            cfg["lang_codes"] = dict(lang_codes)
        self._cache = PrerenderCache(cfg, sample_rate)

    async def render(self, line: Line) -> bytes:
        got = await asyncio.to_thread(self._cache.render, line.text, line.lang, "bench", 1)
        return got or b""


class F5MLXTarget:
    """F5-TTS on a Mac through the `f5-tts-mlx` package, which is its own
    port rather than an mlx-audio family. Product mode: the line is split by
    script the way a call splits it, each piece cloned from that language's
    clip. `generate` returns 24 kHz float audio; it is resampled here."""

    def __init__(self, sample_rate: int = 16000, refs: dict[str, str] | None = None,
                 ref_texts: dict[str, str] | None = None, name: str = "f5-tts-mlx",
                 model_name: str = "lucasnewman/f5-tts-mlx") -> None:
        self.name = name
        self.sample_rate = sample_rate
        self.refs = dict(refs or DEFAULT_REFS)
        self.ref_texts = dict(ref_texts or {})
        self.model_name = model_name

    def _piece(self, text: str, lang: str) -> bytes:
        import numpy as np
        from f5_tts_mlx.generate import generate

        ref = self.refs.get(lang) or self.refs.get("en")
        audio = generate(generation_text=text, model_name=self.model_name,
                         ref_audio_path=ref, ref_audio_text=self.ref_texts.get(lang))
        audio = np.asarray(audio, dtype=np.float32).squeeze()
        src = 24000
        if src != self.sample_rate and len(audio) > 1:
            n = int(len(audio) * self.sample_rate / src)
            audio = np.interp(np.linspace(0, len(audio) - 1, n),
                              np.arange(len(audio)), audio).astype(np.float32)
        return (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()

    async def render(self, line: Line) -> bytes:
        pieces = segment_by_script(line.text, line.lang)

        def _work() -> bytes:
            out = bytearray()
            for i, (piece, lang) in enumerate(pieces):
                part = self._piece(piece, lang)
                if len(pieces) > 1:
                    part = P.trim(part, head=(i > 0), tail=(i < len(pieces) - 1),
                                  keep_ms=10, sample_rate=self.sample_rate)
                    if i:
                        out += P.silence(40, self.sample_rate)
                out += part
            return bytes(out)

        return await asyncio.to_thread(_work)


# ------------------------------------------------------------------ metrics

@dataclass
class Rendering:
    target: str
    line: str
    ok: bool
    ms: int
    seconds: float = 0.0
    rtf: float | None = None
    f0: float | None = None
    voiced: int = 0
    wav: str | None = None
    heard: str | None = None
    cer: float | None = None
    error: str = ""


def measure(target: str, line: Line, pcm: bytes, sample_rate: int, ms: int,
            wav: str | None = None) -> Rendering:
    if not pcm:
        return Rendering(target=target, line=line.id, ok=False, ms=ms, error="no audio")
    seconds = len(pcm) / 2 / sample_rate
    f0, voiced, _spread = P.f0_stats(pcm, sample_rate)
    return Rendering(target=target, line=line.id, ok=True, ms=ms, seconds=round(seconds, 2),
                     rtf=round(ms / 1000 / seconds, 3) if seconds else None,
                     f0=None if f0 != f0 else round(f0, 1), voiced=voiced, wav=wav)


_KEEP = re.compile(r"[^\w一-鿿]+")


def _norm(text: str) -> str:
    return _KEEP.sub("", (text or "").lower())


def cer(expected: str, heard: str) -> float:
    """Character error rate, over characters with case, spacing and
    punctuation removed — so it means the same thing for 六八八七 as for
    "six eight eight seven". 0 is exact; it can exceed 1."""
    a, b = _norm(expected), _norm(heard)
    if not a:
        return 0.0 if not b else 1.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return round(prev[-1] / len(a), 3)


#: A pitch resting on fewer voiced frames than this says little about the
#: line — the same threshold the pre-render normaliser uses.
MIN_VOICED = 12


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 1)


def summarise(renderings: list[Rendering]) -> dict[str, dict[str, Any]]:
    """Per target: how fast, how often it failed, how steady the speaker."""
    out: dict[str, dict[str, Any]] = {}
    by_target: dict[str, list[Rendering]] = {}
    for r in renderings:
        by_target.setdefault(r.target, []).append(r)
    for target, rows in by_target.items():
        ok = [r for r in rows if r.ok]
        ms = [float(r.ms) for r in ok]
        rtf = [r.rtf for r in ok if r.rtf is not None]
        f0s = [r.f0 for r in ok if r.f0 and r.voiced >= MIN_VOICED]
        cers = [r.cer for r in ok if r.cer is not None]
        drift = None
        if len(f0s) >= 3:
            med = statistics.median(f0s)
            drift = round(statistics.pstdev(12 * math.log2(f / med) for f in f0s), 2)
        out[target] = {
            "lines": len(rows),
            "failed": len(rows) - len(ok),
            "p50_ms": _pct(ms, 0.5),
            "p95_ms": _pct(ms, 0.95),
            "rtf_mean": round(statistics.fmean(rtf), 3) if rtf else None,
            "audio_seconds": round(sum(r.seconds for r in ok), 1),
            "f0_median": round(statistics.median(f0s), 1) if f0s else None,
            "drift_semitones": drift,
            "cer_mean": round(statistics.fmean(cers), 3) if cers else None,
        }
    return out


# ---------------------------------------------------------------------- run

Transcriber = Callable[[bytes, int], Awaitable[str]]


async def run(targets: list[Any], lines: list[Line], out_dir: Path,
              transcribe: Transcriber | None = None,
              progress: Callable[[str], None] | None = None) -> tuple[list[Rendering], dict]:
    """Render every line on every target, write the wavs, measure, summarise.

    Targets run one after another rather than interleaved: two models on one
    GPU would be timing each other. Lines within a target run in order, so
    the first line's number includes whatever the model does on first use —
    it is reported, not hidden, because a live call pays it too.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    renderings: list[Rendering] = []
    for target in targets:
        tdir = out_dir / target.name
        tdir.mkdir(exist_ok=True)
        for line in lines:
            t0 = time.perf_counter()
            try:
                pcm = await target.render(line)
                err = ""
            except Exception as exc:                 # a failure is a data point
                pcm, err = b"", f"{type(exc).__name__}: {exc}"
            ms = int((time.perf_counter() - t0) * 1000)
            wav_name = None
            if pcm:
                path = tdir / f"{line.id}.wav"
                with wave.open(str(path), "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(target.sample_rate)
                    w.writeframes(pcm)
                wav_name = f"{target.name}/{path.name}"
            r = measure(target.name, line, pcm, target.sample_rate, ms, wav_name)
            if err:
                r.error = err
            if pcm and transcribe is not None:
                try:
                    r.heard = await transcribe(pcm, target.sample_rate)
                    r.cer = cer(line.said, r.heard)
                except Exception as exc:             # pragma: no cover - service
                    r.heard, r.error = None, f"asr: {exc}"
            renderings.append(r)
            if progress:
                state = f"{r.ms:5d} ms  {r.seconds:4.1f} s" if r.ok else f"FAILED {r.error[:40]}"
                progress(f"  {target.name:14} {line.id:20} {state}")
    summary = summarise(renderings)
    (out_dir / "results.json").write_text(json.dumps({
        "lines": [asdict(l) | {"said": l.said} for l in lines],
        "renderings": [asdict(r) for r in renderings],
        "summary": summary,
    }, indent=2, ensure_ascii=False))
    render_page(lines, renderings, summary, out_dir)
    return renderings, summary


# --------------------------------------------------------------------- page

def markdown_table(summary: dict[str, dict[str, Any]]) -> str:
    cols = (("lines", "lines"), ("failed", "failed"), ("p50_ms", "p50 ms"),
            ("p95_ms", "p95 ms"), ("rtf_mean", "RTF"), ("f0_median", "f0 Hz"),
            ("drift_semitones", "drift st"), ("cer_mean", "CER"))
    head = "| model | " + " | ".join(h for _, h in cols) + " |"
    sep = "|---|" + "|".join("---:" for _ in cols) + "|"
    rows = [f"| {t} | " + " | ".join("–" if s.get(k) is None else str(s[k]) for k, _ in cols)
            + " |" for t, s in summary.items()]
    return "\n".join([head, sep, *rows])


def render_page(lines: list[Line], renderings: list[Rendering],
                summary: dict[str, dict[str, Any]], out_dir: Path) -> Path:
    """One page: the summary, then every line with every model's rendering
    beside it, so the comparison is made by ear on the same sentence."""
    targets = list(summary)
    by_key = {(r.target, r.line): r for r in renderings}
    parts = ["<meta charset='utf-8'><title>TTS candidates</title>",
             "<style>body{font:14px system-ui;margin:2rem;max-width:80rem}"
             "table{border-collapse:collapse;width:100%;margin-bottom:2rem}"
             "td,th{border-bottom:1px solid #ddd;padding:.4rem .6rem;text-align:left;"
             "vertical-align:top}th{background:#f6f6f6}code{background:#f4f4f4;"
             "padding:.1rem .3rem}audio{width:15rem}.num{text-align:right}"
             ".fail{color:#b00}.said{color:#666;font-size:12px}small{color:#888}</style>",
             "<h1>TTS candidates</h1>",
             "<p>Latency is wall time for the whole line (no streaming here), RTF is "
             "that over the audio's length, drift is the spread of each line's median "
             "pitch in semitones — lower is a steadier speaker. CER is against what "
             "the voice was handed, where an ASR was given. Quality and accent are "
             "yours to judge: play the same row across.</p>",
             "<table><tr><th>model</th><th class=num>lines</th><th class=num>failed</th>"
             "<th class=num>p50 ms</th><th class=num>p95 ms</th><th class=num>RTF</th>"
             "<th class=num>f0 Hz</th><th class=num>drift st</th><th class=num>CER</th></tr>"]
    for t, s in summary.items():
        cells = [s["lines"], s["failed"], s["p50_ms"], s["p95_ms"], s["rtf_mean"],
                 s["f0_median"], s["drift_semitones"], s["cer_mean"]]
        parts.append(f"<tr><td><b>{html.escape(t)}</b></td>"
                     + "".join(f"<td class=num>{'–' if c is None else c}</td>" for c in cells)
                     + "</tr>")
    parts.append("</table>")

    groups: dict[str, list[Line]] = {}
    for line in lines:
        groups.setdefault(line.group, []).append(line)
    for group, rows in groups.items():
        parts.append(f"<h2>{html.escape(group)}</h2><table><tr><th>line</th>")
        parts.extend(f"<th>{html.escape(t)}</th>" for t in targets)
        parts.append("</tr>")
        for line in rows:
            said = "" if line.said == line.text else \
                f"<div class=said>handed to the voice: {html.escape(line.said)}</div>"
            parts.append(f"<tr><td><small>{line.id}</small><br>{html.escape(line.text)}{said}</td>")
            for t in targets:
                r = by_key.get((t, line.id))
                if r is None or not r.ok:
                    why = html.escape((r.error if r else "not run")[:80])
                    parts.append(f"<td class=fail>failed<br><small>{why}</small></td>")
                    continue
                heard = (f"<div class=said>heard: {html.escape(r.heard)}"
                         + (f" <b>CER {r.cer}</b>" if r.cer is not None else "") + "</div>"
                         if r.heard is not None else "")
                parts.append(f'<td><audio controls preload="none" src="{html.escape(r.wav)}">'
                             f"</audio><br><small>{r.ms} ms · {r.seconds} s · RTF {r.rtf}"
                             + (f" · {r.f0} Hz" if r.f0 else "") + f"</small>{heard}</td>")
            parts.append("</tr>")
        parts.append("</table>")
    page = out_dir / "index.html"
    page.write_text("\n".join(parts), encoding="utf-8")
    return page
