"""The listening gallery: recordings the console can play without a model.

Two sources. `samples/` holds what earlier voice work rendered — the shipped
voices, the Qwen3-TTS speakers the reference clips came from, the described
voices, a handful of other models — catalogued in `samples/index.yaml` by
gender, model and language. `voices/bench/say/` holds whatever `make tts-say`
and the console's SAY box rendered on this machine, so a line said in a
candidate model is in the same list a minute later.

Files are served by name from those two directories and nowhere else.
"""
from __future__ import annotations

import re
import wave
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
SHIPPED = ROOT / "samples"
RENDERED = ROOT / "voices" / "bench" / "say"

_FEMALE = re.compile(r"(^|[_\-])(f\d?|female|woman|girl|vivian|serena|anna|sohee|emma|grace)([_\-.]|$)", re.I)
_MALE = re.compile(r"(^|[_\-])(m\d?|male|man|uncle|aiden|dylan|eric|ryan|carter|michael)([_\-.]|$)", re.I)
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*\.wav$")


def guess_gender(name: str) -> str:
    if _FEMALE.search(name):
        return "female"
    if _MALE.search(name):
        return "male"
    return "unknown"


def _catalogue() -> dict[str, dict[str, Any]]:
    p = SHIPPED / "index.yaml"
    if not p.exists():
        return {}
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {str(k): dict(v or {}) for k, v in (raw.get("samples") or {}).items()}


def _seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path)) as w:
            return round(w.getnframes() / w.getframerate(), 1)
    except Exception:
        return None


def listing() -> list[dict[str, Any]]:
    """Every playable sample, catalogued ones first, newest renders last."""
    out: list[dict[str, Any]] = []
    cat = _catalogue()
    seen: set[str] = set()
    for name, meta in cat.items():
        path = SHIPPED / name
        if not path.exists():
            continue
        seen.add(name)
        out.append({"id": f"shipped/{name}", "name": name, "source": "shipped",
                    "label": meta.get("label", name), "gender": meta.get("gender", "unknown"),
                    "model": meta.get("model", ""), "lang": meta.get("lang", ""),
                    "note": meta.get("note", ""), "seconds": _seconds(path)})
    for path in sorted(SHIPPED.glob("*.wav")):
        if path.name in seen or not _SAFE.match(path.name):
            continue
        out.append({"id": f"shipped/{path.name}", "name": path.name, "source": "shipped",
                    "label": path.stem, "gender": guess_gender(path.name), "model": "",
                    "lang": "", "note": "not in samples/index.yaml", "seconds": _seconds(path)})
    if RENDERED.exists():
        for path in sorted(RENDERED.glob("*.wav"), key=lambda p: p.stat().st_mtime):
            if not _SAFE.match(path.name):
                continue
            out.append({"id": f"rendered/{path.name}", "name": path.name, "source": "rendered",
                        "label": path.stem, "gender": guess_gender(path.name),
                        "model": path.stem.split("--")[0], "lang": "",
                        "note": "rendered on this machine", "seconds": _seconds(path)})
    return out


def resolve(sample_id: str) -> Path | None:
    """The file for an id from `listing`, or None. Only the two directories,
    only plain wav names — an id is a URL segment and must not be a path."""
    source, _, name = sample_id.partition("/")
    if not _SAFE.match(name) or "/" in name:
        return None
    base = {"shipped": SHIPPED, "rendered": RENDERED}.get(source)
    if base is None:
        return None
    path = base / name
    return path if path.exists() and path.parent == base else None
