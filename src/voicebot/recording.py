"""Call recording.

Kept server-side rather than in the browser: a transcript that lives only in a
tab is lost on reload, and an outbound insurance call needs a record that
outlives the operator's session. Every event is captured, so the log carries
the compliance decisions — which gates passed, when the cross-sell was
suppressed and why — not just what was said.

Synthetic personas only, so this is not customer data. That changes the moment
the CRM stub is replaced: at that point retention and access control apply.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("voicebot.recording")


@dataclass
class Call:
    id: str
    started_at: float
    policy_id: str
    name: str
    register: str
    voice: str | None
    lang: str
    events: list[dict[str, Any]] = field(default_factory=list)
    ended_at: float | None = None
    outcome: str | None = None

    @property
    def duration(self) -> float:
        return (self.ended_at or time.time()) - self.started_at

    def summary(self) -> dict[str, Any]:
        """Enough for a list view without shipping every event."""
        turns = sum(1 for e in self.events if e.get("kind") == "transcript")
        gates = {e["gate"]: e["state"] for e in self.events if e.get("kind") == "gate"}
        return {
            "id": self.id, "started_at": self.started_at,
            "policy_id": self.policy_id, "name": self.name,
            "register": self.register, "voice": self.voice, "lang": self.lang,
            "duration": round(self.duration, 1), "turns": turns,
            "gates": gates, "outcome": self.outcome,
        }


class Recorder:
    """In-memory ring plus an append-only JSONL file.

    The file is the record; memory is for the console's list view. Bounded so a
    long-running demo cannot exhaust RAM — the JSONL keeps everything.
    """

    def __init__(self, path: Path | None = None, keep: int = 50) -> None:
        self.path = path
        self.keep = keep
        self._calls: list[Call] = []
        self._lock = threading.Lock()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    def start(self, **kw: Any) -> Call:
        call = Call(id=f"c{int(time.time()*1000):x}", started_at=time.time(), **kw)
        with self._lock:
            self._calls.append(call)
            del self._calls[:-self.keep]
        return call

    def event(self, call: Call | None, ev: dict[str, Any]) -> None:
        if call is None:
            return
        call.events.append({"t": round(time.time() - call.started_at, 2), **ev})
        if ev.get("kind") == "end":
            call.ended_at = time.time()
            call.outcome = ev.get("outcome")
            self._flush(call)

    def finish(self, call: Call | None) -> None:
        """Close a call the caller hung up on, so an abandoned call is still
        recorded rather than vanishing."""
        if call is None or call.ended_at is not None:
            return
        call.ended_at = time.time()
        call.outcome = call.outcome or "abandoned"
        self._flush(call)

    def _flush(self, call: Call) -> None:
        if not self.path:
            return
        try:
            with self.path.open("a") as fh:
                fh.write(json.dumps(asdict(call), ensure_ascii=False) + "\n")
        except Exception as exc:                        # pragma: no cover
            log.warning("could not write call record: %s", exc)

    def summaries(self) -> list[dict[str, Any]]:
        with self._lock:
            return [c.summary() for c in reversed(self._calls)]

    def get(self, call_id: str) -> Call | None:
        with self._lock:
            return next((c for c in self._calls if c.id == call_id), None)
