"""Events the chassis emits. The console renders these; nothing else is public API."""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any, Literal

Speaker = Literal["agent", "caller"]

#: Events the transport consumes itself rather than serialising to the console.
INTERNAL_KINDS = {"audio"}
GateState = Literal["pending", "pass", "block"]


@dataclass
class Event:
    kind: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class Transcript(Event):
    speaker: Speaker
    text: str
    lang: str
    source: str = ""          # "pre-rendered" | "generated" | "pre-rendered + slots"
    latency_ms: int | None = None
    kind: str = field(default="transcript", init=False)


@dataclass
class TurnChange(Event):
    turn: int                 # 1-7
    state: Literal["active", "done", "skip"]
    kind: str = field(default="turn", init=False)


@dataclass
class GateChange(Event):
    gate: str                 # identity | dnc | consent | advice
    state: GateState
    note: str = ""
    kind: str = field(default="gate", init=False)


@dataclass
class ToolCall(Event):
    tool: str
    arg: str
    kind: str = field(default="tool", init=False)


@dataclass
class SystemNote(Event):
    text: str
    ok: bool = False
    kind: str = field(default="system", init=False)


@dataclass
class CallEnded(Event):
    text: str
    outcome: str
    kind: str = field(default="end", init=False)


@dataclass
class AgentAudio(Event):
    """Consumed by the transport, never serialised to JSON — the websocket
    sends these as binary frames alongside the transcript."""
    pcm: bytes
    sample_rate: int
    kind: str = field(default="audio", init=False)


@dataclass
class HandoffRequested(Event):
    """The call is being given to a person.

    Carried as its own event rather than a system note because it is the one
    thing an operator has to act on, and because a telephony layer needs a
    machine-readable signal to bridge a leg on.
    """
    reason: str
    code: str
    summary: str
    warm: bool = False
    outstanding: str = ""
    kind: str = field(default="handoff", init=False)


@dataclass
class Status(Event):
    text: str
    kind: str = field(default="status", init=False)
