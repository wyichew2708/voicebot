from __future__ import annotations

from typing import Any

from .base import Backend, BackendHealth, Completion, TranscriptResult  # noqa: F401
from .mock import MockBackend


def load_backend(cfg: dict[str, Any]) -> Backend:
    profile = cfg.get("profile", "mock")
    if profile == "mock":
        lo, hi = cfg.get("backend", {}).get("latency_ms", [520, 760])
        return MockBackend((lo, hi))
    if profile == "mlx":
        from .mlx_backend import MLXBackend
        backend_cfg = dict(cfg["backend"])
        backend_cfg["sample_rate"] = cfg.get("audio", {}).get("sample_rate", 16000)
        be = MLXBackend(backend_cfg)
        be.load()
        return be
    if profile == "cuda":
        from .cuda_backend import CUDABackend
        backend_cfg = dict(cfg["backend"])
        backend_cfg["sample_rate"] = cfg.get("audio", {}).get("sample_rate", 16000)
        return CUDABackend(backend_cfg)
    raise ValueError(f"Unknown profile: {profile!r}")
