from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


#: Environment overrides for values that differ per deployment. Service
#: addresses change between a laptop, compose (where they are service names)
#: and a bare host, so they must not be baked into the image.
_ENV_OVERRIDES = {
    "VOICEBOT_ASR_URL": ("backend", "asr", "base_url"),
    "VOICEBOT_ASR_MODEL": ("backend", "asr", "model"),
    "VOICEBOT_LLM_URL": ("backend", "llm", "base_url"),
    "VOICEBOT_LLM_MODEL": ("backend", "llm", "model"),
    "VOICEBOT_TTS_URL": ("backend", "tts", "base_url"),
    "VOICEBOT_CACHE_DIR": ("backend", "tts", "prerender", "cache_dir"),
    "VOICEBOT_REGISTER": ("register",),
}


def _apply_env(cfg: dict[str, Any]) -> list[str]:
    applied = []
    for env, path in _ENV_OVERRIDES.items():
        value = os.environ.get(env)
        if not value:
            continue
        node = cfg
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value
        applied.append(f"{env}={value}")
    return applied


def load(name: str | None = None) -> dict[str, Any]:
    """Load a runtime profile.

    VOICEBOT_PROFILE overrides the argument; the VOICEBOT_* variables above
    override individual values, so one image can serve every environment."""
    name = os.environ.get("VOICEBOT_PROFILE") or name or "mock"
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"No config {path}. Available: "
            + ", ".join(sorted(p.stem for p in CONFIG_DIR.glob('*.yaml'))))
    with path.open() as fh:
        cfg: dict[str, Any] = yaml.safe_load(fh)
    cfg.setdefault("audio", {}).setdefault("sample_rate", 16000)
    applied = _apply_env(cfg)
    if applied:
        logging.getLogger("voicebot.config").info("env overrides: %s", ", ".join(applied))
    return cfg
