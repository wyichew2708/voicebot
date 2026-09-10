"""FastAPI host for the demo console.

Serves the console and drives one CallSession per websocket. Audio frames are
accepted as binary messages when a real backend is loaded; in mock mode the
operator drives the caller side with text, which is also the most reliable way
to rehearse a demo.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import struct
from contextlib import aclosing
import time
import wave
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response

from . import config
from .call.engine import CallSession
from .data import personas
from .audio_gate import is_plausible, is_speech
from .call import router
from .events import INTERNAL_KINDS, AgentAudio
from .knowledge import policy as knowledge_policy
from .pcm import trim as trim_silence
from .telemetry import MeasuredBackend, ResponseTrace, milliseconds
from .runtime.workers import InferenceBusy
from .playout import PlayoutWindow
from .realtime import RealtimeTransport
from .recording import Recorder
from .runtime import load_backend
from .runtime import warm as warmup_plan
from .runtime.prerender import for_language
from . import voices as voice_limits
from .voices import CustomVoices, VoiceError

log = logging.getLogger("voicebot")

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "ui" / "demo-console.html"
RECORDER = Recorder(ROOT / "logs" / "calls.jsonl")

app = FastAPI(title="Tiq Renewal Voicebot")
_state: dict[str, Any] = {}


VOICES = CustomVoices(ROOT / "voices")


def _backend():
    if "backend" not in _state:
        cfg = _state.get("cfg") or config.load()
        # Voices an admin recorded belong to the deployment, not the profile.
        # Merged before the backend is built so the pre-render cache sees them
        # as ordinary voices and nothing downstream needs to know the
        # difference.
        VOICES.merge_into(cfg.get("backend", {}).get("tts", {})
                             .setdefault("prerender", {}))
        _state["cfg"] = cfg
        # Resolved once, here, so that every call in this process agrees about
        # whether unsourced wording may be spoken. Logged because it is the
        # setting most likely to differ between the demo and production, and
        # the one whose effect on a transcript is easiest to misread as a bug.
        serving = knowledge_policy.configure(cfg)
        log.info("Knowledge bundle: %s", serving.describe)
        log.info("Loading backend profile=%s", cfg.get("profile"))
        _state["backend"] = load_backend(cfg)
    return _state["backend"]


@app.on_event("startup")
async def warmup() -> None:
    """Synthesise one short line per language at boot.

    The first call into Kokoro builds its G2P pipeline — 2.5 s on this machine,
    and it would otherwise land on the first turn of a live call. Paying it here
    is the difference between a demo that opens crisply and one that opens with
    a pause.
    """
    backend = _backend()
    for lang in _state["cfg"].get("languages", ["en"]):
        try:
            t0 = time.perf_counter()
            async for _ in backend.synthesize("Ready.", lang):
                pass
            log.info("warmed %s in %d ms", lang, int((time.perf_counter() - t0) * 1000))
        except Exception as exc:                        # pragma: no cover
            log.warning("warmup failed for %s: %s", lang, exc)

    # The guardrail model too, if it is on. Loading it lazily would put the
    # whole load on whichever caller first says something unrecognised, and
    # the timeout would abandon it — so it would never be used at all.
    guard = _state["cfg"].get("guardrail", {})
    if guard.get("enabled", True):
        try:
            t0 = time.perf_counter()
            got = await backend.complete(router.system_prompt(),
                                         router.user_prompt("yes, speaking", 1, "en"),
                                         "en", max_tokens=router.MAX_TOKENS)
            ms = int((time.perf_counter() - t0) * 1000)
            label = router.parse(got.text)
            if label is None:
                log.warning("guardrail model answered off-menu at warmup (%r) — "
                            "calls will fall back to asking again", got.text[:60])
            else:
                log.info("guardrail warmed in %d ms -> %s", ms, label)
        except Exception as exc:                        # pragma: no cover
            log.warning("guardrail unavailable (%s) — calls fall back to the "
                        "keyword handlers alone", exc)


@app.get("/")
async def index() -> FileResponse:
    # The console is edited constantly; a cached copy means the browser quietly
    # runs yesterday's client against today's server, which looks like a bug in
    # the app rather than a stale file.
    return FileResponse(UI, headers={"Cache-Control": "no-store, must-revalidate"})


@app.on_event("shutdown")
async def shutdown_backend():
    backend = _state.get("backend")
    if backend is not None and hasattr(backend, "close"):
        backend.close()


@app.get("/realtime-audio.js")
async def realtime_audio_script() -> FileResponse:
    return FileResponse(UI.parent / "realtime-audio.js", media_type="application/javascript")


@app.get("/voice-worklet.js")
async def voice_worklet_script() -> FileResponse:
    return FileResponse(UI.parent / "voice-worklet.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-store"})


def _prerender_cfg() -> dict:
    return (_state.get("cfg", {}).get("backend", {})
            .get("tts", {}).get("prerender", {}) or {})


def _voice_options() -> list[dict[str, str]]:
    """What the console offers in its voice switch."""
    return [{"id": vid, "label": v.get("label", vid)}
            for vid, v in _prerender_cfg().get("voices", {}).items()]


def _default_voice() -> str | None:
    cfg = _prerender_cfg()
    return cfg.get("default_voice") or next(iter(cfg.get("voices", {})), None)


@app.get("/api/health")
async def health() -> JSONResponse:
    h = _backend().health()
    return JSONResponse({
        "profile": h.profile, "asr": h.asr, "llm": h.llm, "tts": h.tts,
        "ready": h.ready, "detail": h.detail,
        "register": _state["cfg"].get("register", "standard"),
        # So a warm-up running outside this process can stand aside too.
        "on_call": _on_call(),
        "voices": _voice_options(),
        "default_voice": _default_voice(),
        "knowledge": knowledge_policy.default_serving().describe,
    })


# --------------------------------------------------------------- voices
# Recording a voice is cloning a person, so the deal is stated plainly in the
# console and everything here is reversible: the clip is a file, the entry is
# a line of JSON, and deleting the voice deletes both.


def _warm_state() -> dict:
    return _state.setdefault("warming", {})


def _warm_from_cache(vid: str) -> dict:
    """How much of this voice is already on disk.

    Read from the cache rather than from a register of jobs this process
    started: a voice warmed by `make prerender`, or shipped with the deploy,
    has no job to its name and was being reported as cold — which is exactly
    backwards for the two voices that ship warm.
    """
    from .runtime.prerender import PrerenderCache

    cfg = _state["cfg"]
    cache = PrerenderCache(_prerender_cfg(), cfg["audio"]["sample_rate"])
    jobs = warmup_plan.plan(cfg.get("languages", ["en"]),
                            ["standard", "singlish"], [vid],
                            knowledge_policy.default_serving())
    missing = len(warmup_plan.outstanding(cache, jobs))
    return {"done": len(jobs) - missing, "total": len(jobs),
            "state": "done" if not missing else "cold", "detail": ""}


def _voice_rows() -> list[dict]:
    """Every voice the console can offer, shipped and recorded alike."""
    custom = VOICES.all()
    rows = []
    for vid, v in _prerender_cfg().get("voices", {}).items():
        mine = custom.get(vid)
        # A voice may carry a clip and a pitch per language; the picker shows
        # the English ones, and says separately whether Mandarin has its own.
        refs = v.get("ref_audio")
        row = {"id": vid, "label": v.get("label", vid),
               "note": v.get("note", ""),
               "custom": mine is not None,
               "target_f0": float(for_language(v.get("target_f0"), "en") or 0),
               "mandarin": isinstance(refs, dict) and "zh" in refs}
        if mine:
            row.update(seconds=mine["seconds"], semitones=mine["semitones"],
                       measured_f0=mine["measured_f0"])
        row["rate"] = float(for_language(v.get("rate"), "en") or 1.0)
        # A running job wins: it knows where it is up to, and re-counting the
        # cache underneath it would report progress it has not committed yet.
        job = _warm_state().get(vid)
        row["warm"] = ({k: job[k] for k in ("done", "total", "state", "detail")}
                       if job and job.get("state") == "running"
                       else _warm_from_cache(vid))
        # Whether there is anything to play. A control that silently does
        # nothing is worse than no control, so the picker hides the button
        # rather than offering a 404.
        ref = for_language(refs, "en")
        row["sample"] = bool(row["warm"]["done"] or (ref and Path(ref).exists()))
        rows.append(row)
    return rows


@app.get("/api/voices")
async def list_voices() -> JSONResponse:
    return JSONResponse({"voices": _voice_rows(), "default": _default_voice(),
                         "limits": {"min_seconds": voice_limits.MIN_SECONDS,
                                    "max_seconds": voice_limits.MAX_SECONDS,
                                    "rate": voice_limits.REFERENCE_RATE}})


@app.post("/api/voices")
async def add_voice(request: Request) -> JSONResponse:
    """One recording, as raw 16-bit mono PCM at the rate in the query string.

    Raw rather than a browser recording container: the console already packs
    PCM for the call path, and decoding webm on the server would mean pulling
    in ffmpeg to hold a file we then only ever resample.
    """
    label = request.query_params.get("label", "")
    try:
        rate = int(request.query_params.get("sample_rate", "0"))
    except ValueError:
        rate = 0
    if rate <= 0:
        return JSONResponse({"error": "missing sample_rate"}, status_code=400)
    audio = await request.body()
    try:
        entry = VOICES.add(label, audio, rate)
    except VoiceError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    # Live now, at the price of a synthesis per line, until it is warmed.
    VOICES.merge_into(_prerender_cfg())
    log.info("voice %r added by console", entry["id"])
    return JSONResponse({"voice": entry}, status_code=201)


@app.post("/api/voices/{vid}/pitch")
async def set_voice_pitch(vid: str, request: Request) -> JSONResponse:
    body = await request.json()
    try:
        entry = VOICES.set_pitch(vid, float(body.get("semitones", 0)))
    except (VoiceError, TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    # The pitch is in the cache key, so every warmed line for this voice is now
    # a miss. Say so rather than letting the console look warm when it is not.
    VOICES.merge_into(_prerender_cfg())
    _warm_state().pop(vid, None)
    return JSONResponse({"voice": entry})


@app.delete("/api/voices/{vid}")
async def delete_voice(vid: str) -> JSONResponse:
    if not VOICES.remove(vid):
        return JSONResponse({"error": "no such voice"}, status_code=404)
    _prerender_cfg().get("voices", {}).pop(vid, None)
    _warm_state().pop(vid, None)
    return JSONResponse({"ok": True})


#: One line, chosen because it is the first thing any caller hears.
SAMPLE_TURN = 1


@app.get("/api/voices/{vid}/sample.wav")
async def voice_sample(vid: str) -> Response:
    """What this voice sounds like, so a voice can be chosen by ear.

    A rendered line where one is cached, because that is what the caller
    actually hears. Falling back to the reference clip is honest but not the
    same thing — a clone is not its reference — so the console says which of
    the two it is playing.
    """
    from .call import script
    from .runtime.prerender import PrerenderCache

    voices = _prerender_cfg().get("voices", {})
    if vid not in voices:
        return Response(status_code=404)

    cache = PrerenderCache(_prerender_cfg(), _state["cfg"]["audio"]["sample_rate"])
    policy = next(iter(personas.all_policies()))
    line = script.render(SAMPLE_TURN, policy, "en")
    pcm = await asyncio.get_running_loop().run_in_executor(
        None, cache.get, line, "en", vid)
    if pcm:
        return Response(content=_wav_bytes(pcm, cache.sample_rate),
                        media_type="audio/wav",
                        headers={"Cache-Control": "no-store", "X-Sample": "rendered"})

    ref = for_language(voices[vid].get("ref_audio"), "en")
    if ref and Path(ref).exists():
        return FileResponse(ref, media_type="audio/wav",
                            headers={"Cache-Control": "no-store",
                                     "X-Sample": "reference"})
    return Response(status_code=404)


def _wav_bytes(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _on_call() -> bool:
    return bool(_state.get("live_calls"))


def _warm_voice(vid: str) -> None:
    """Render every scripted line for one voice. Runs off the event loop.

    Yields to a live call. Warming and speaking use the same GPU, and a line
    the caller is waiting on loses badly to a batch job: a turn that reads
    from a warm cache in 5 ms took 13.6 seconds with a warm-up running behind
    it. The batch can wait; the caller cannot.
    """
    job = _warm_state()[vid]
    cfg = _state["cfg"]
    from .runtime.prerender import PrerenderCache

    cache = PrerenderCache(_prerender_cfg(), cfg["audio"]["sample_rate"])
    langs = cfg.get("languages", ["en"])
    jobs = warmup_plan.plan(langs, ["standard", "singlish"], [vid])
    todo = warmup_plan.outstanding(cache, jobs)
    job.update(total=len(todo), done=0, state="running", detail="")
    if not todo:
        job.update(state="done", detail="already warm")
        return
    backend = _backend()
    for i, (text, _lang, _v) in enumerate(todo, 1):
        if job.get("state") == "cancelled":
            return                        # the endpoint wrote where it stopped
        while _on_call() and job.get("state") != "cancelled":
            job["detail"] = "paused while a call is running"
            job["state"] = "waiting"
            time.sleep(1.0)
        if job.get("state") == "waiting":
            job["state"] = "running"
        if job.get("state") == "cancelled":
            return
        try:
            if hasattr(backend, "_render_resident"):
                # Reuse the resident model on its owner; no second clone model.
                pcm = backend._pool.submit(backend._render_resident, text, _lang, _v).result()
            else:
                pcm = cache.render(text, _lang, _v)
            if not pcm:
                raise RuntimeError("selected voice is unavailable")
        except Exception as exc:
            job.update(state="failed", detail=str(exc))
            return
        job.update(done=i, detail=text[:48])
    job.update(state="done", detail="")


@app.post("/api/voices/{vid}/warm")
async def warm_voice(vid: str) -> JSONResponse:
    if _state["cfg"].get("profile") == "cuda":
        return JSONResponse({"error": "Build the voice cache offline with make prerender and copy it to this server."}, status_code=409)
    if vid not in _prerender_cfg().get("voices", {}):
        return JSONResponse({"error": "no such voice"}, status_code=404)
    job = _warm_state().get(vid)
    if job and job.get("state") in ("running", "waiting"):
        return JSONResponse({"warm": job})
    _warm_state()[vid] = {"done": 0, "total": 0, "state": "running", "detail": ""}
    asyncio.get_running_loop().run_in_executor(None, _warm_voice, vid)
    return JSONResponse({"warm": _warm_state()[vid]})


@app.delete("/api/voices/{vid}/warm")
async def stop_warming(vid: str) -> JSONResponse:
    job = _warm_state().get(vid)
    if job and job.get("state") in ("running", "waiting"):
        # Say where it stopped here rather than in the worker: the console
        # stops polling once nothing is running, so a detail written a second
        # later is never read, and the last line rendered would stand as the
        # explanation for the stop.
        job.update(state="cancelled",
                   detail=f"stopped at {job.get('done', 0)} of {job.get('total', 0)}")
    return JSONResponse({"warm": job or {}})


@app.get("/api/last-reply.wav")
async def last_reply() -> Response:
    """The most recent agent utterance as a plain wav.

    Web Audio is silent in some embedded browsers — an <audio> element and a
    downloadable file use a different output path, so this is the fallback when
    the console appears to play but nothing is audible.
    """
    pcm = _state.get("last_audio")
    if not pcm:
        return Response(status_code=404, content=b"no audio yet")
    sr = _state["cfg"]["audio"]["sample_rate"]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return Response(content=buf.getvalue(), media_type="audio/wav",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/calls")
async def list_calls() -> JSONResponse:
    """Every call this process has handled, newest first."""
    return JSONResponse(RECORDER.summaries())


@app.get("/api/calls/{call_id}")
async def get_call(call_id: str) -> JSONResponse:
    call = RECORDER.get(call_id)
    if call is None:
        return JSONResponse({"error": "unknown call"}, status_code=404)
    from dataclasses import asdict
    return JSONResponse(asdict(call))


@app.get("/api/preview")
async def preview(policy_id: str, lang: str = "en", turn: int = 1) -> JSONResponse:
    """The same line in both registers.

    The console shows this under the register switch so the difference is
    visible rather than asserted — otherwise an operator who only ever runs one
    setting has no way to tell the control does anything.
    """
    from .call import script

    try:
        policy = personas.get(policy_id)
    except KeyError:
        return JSONResponse({"error": "unknown policy"}, status_code=404)
    return JSONResponse({
        "standard": script.render(turn, policy, lang, register="standard"),
        "singlish": script.render(turn, policy, lang, register="singlish"),
    })


def _with_surname(policy, surname: str | None, salutation: str | None):
    """The same record under a different name, for trying a pronunciation.

    A copy, never a mutation: `personas.get` hands back the module-level
    object, and editing it would follow the operator into every later call in
    the process.

    A whole name typed into the box is kept as the record's name, where the
    operator can see what they entered, and reduced to the one word the call
    actually says. Spliced onto the record's given name instead, "Chew Yi
    Feng" made a record called "Andrew Chew Yi Feng" and a call that said all
    of it.
    """
    import dataclasses

    from .spoken import surname_of

    typed = " ".join((surname or "").split())
    salutation = (salutation or "").strip() or policy.salutation
    if not typed:
        return policy
    said = surname_of(typed)
    if said.lower() != typed.lower():
        log.info("name %r on this call is spoken as %r", typed, said)
        return dataclasses.replace(policy, surname=said, salutation=salutation,
                                   name=typed)
    given = policy.name.rsplit(" ", 1)[0] if " " in policy.name else ""
    return dataclasses.replace(
        policy, surname=said, salutation=salutation,
        name=(f"{given} {said}".strip() if given else said))


@app.get("/api/name")
async def name_preview(surname: str, salutation: str = "Mr",
                       lang: str = "en") -> JSONResponse:
    """What the voice will actually be handed for this name.

    The console shows this as the operator types, because the interesting part
    is invisible otherwise: the transcript says "Mr Tan" whatever happens, and
    only the synthesiser ever sees "Mr Dan".
    """
    from .call import script
    from .spoken import (reload_names, sayable, segment_by_script, spoken_names,
                         surname_of)

    reload_names()          # so an edit to voices/names.yaml shows up at once
    surname = " ".join((surname or "").split())
    if not surname:
        return JSONResponse({"error": "no surname"}, status_code=400)
    policy = _with_surname(next(iter(personas.all_policies())), surname, salutation)
    line = script.render(1, policy, lang)
    # What the call will say, which is one name however many were typed. The
    # console shows this back, so a full name in the box is visibly shortened
    # rather than quietly.
    said = surname_of(surname)
    return JSONResponse({
        "surname": surname,
        "said_as": said,
        "shortened": said.lower() != surname.lower(),
        "sayable": bool(said) and sayable(said),
        "spoken_as": spoken_names(f"{policy.salutation} {said}"),
        "line": line,
        "synthesised": "".join(frag for frag, _ in segment_by_script(line, lang)),
    })


@app.get("/api/personas")
async def list_personas() -> JSONResponse:
    return JSONResponse([p.to_dict() for p in personas.all_policies()])


@app.websocket("/ws")
async def ws(sock: WebSocket) -> None:
    await sock.accept()
    counted = False
    session: CallSession | None = None
    record = None
    backend = _backend()
    sample_rate = _state["cfg"]["audio"]["sample_rate"]
    utterance = bytearray()
    overflow = False
    unheard = 0
    audio_id = 0
    playback: dict[tuple[int, int], asyncio.Future] = {}
    windows: dict[tuple[int, int], PlayoutWindow] = {}
    protocol = 1
    client_turn = 0
    active_trace: ResponseTrace | None = None
    # Bound input independently of whether the client sends utterance_end.
    max_audio = sample_rate * 2 * 30

    async def send(payload):
        if isinstance(payload, bytes):
            await sock.send_bytes(payload)
        else:
            await sock.send_text(json.dumps(payload, ensure_ascii=False))

    transport = RealtimeTransport(send)

    def clear_playback():
        for future in playback.values():
            future.cancel()
        playback.clear()
        windows.clear()

    async def respond(generation, current, call, trace, *, text=None, lang=None,
                      pcm=None, started_at=None, opening=False, request_turn=0, flow_control=False):
        nonlocal audio_id, unheard
        trace.start()
        measured = MeasuredBackend(backend, trace)
        current.backend = measured
        response_role = "opening" if opening else "answer"
        result_status = "completed"
        dialogue_started = False
        last_text = None
        incomplete = False
        samples = 0
        sequence = 0
        aid = 0
        ack = None
        # Freeze this job's transport mode across a subsequent call start.
        version = protocol
        flow_control = flow_control and version == 2

        async def emit(ev):
            nonlocal audio_id, last_text, incomplete, samples, sequence, aid, ack, response_role
            if generation != transport.generation:
                raise asyncio.CancelledError
            if isinstance(ev, AgentAudio):
                if ev.start:
                    audio_id += 1
                    aid = audio_id
                    trace.audio_ready(aid, response_role, current.lang)
                    samples = sequence = 0
                    incomplete = True
                    _state["last_audio"] = bytearray()
                    if version == 2:
                        ack = asyncio.get_running_loop().create_future()
                        playback[(generation, aid)] = ack
                        if flow_control:
                            windows[(generation, aid)] = PlayoutWindow(ev.sample_rate)
                    await transport.send({"kind": "audio_begin",
                        "sample_rate": ev.sample_rate, "generation": generation,
                        "audio_id": aid, "audio_protocol": version,
                        "client_turn": request_turn, "response_role": response_role,
                        "flow_control": flow_control}, generation)
                _state["last_audio"] += ev.pcm
                samples += len(ev.pcm) // 2
                if samples > ev.sample_rate * 120:
                    raise ValueError("agent utterance exceeds 120 seconds")
                frame = int(ev.sample_rate * 0.02) * 2
                for i in range(0, len(ev.pcm), frame):
                    data = ev.pcm[i:i + frame]
                    window = windows.get((generation, aid))
                    if window is not None:
                        await window.reserve(len(data) // 2)
                    if version == 2:
                        data = struct.pack("<III", generation, aid, sequence) + data
                    sequence += 1
                    await transport.send(data, generation)
                if ev.final:
                    window = windows.get((generation, aid))
                    if window is not None:
                        window.final = True
                    await transport.send({"kind": "audio_end",
                        "generation": generation, "audio_id": aid,
                        "client_turn": request_turn}, generation)
                    if ack is not None:
                        # Do not advance the engine past a question/final line
                        # until the browser finishes it. Lost/blocked playback
                        # fails closed, never confirms delivery by timeout.
                        try:
                            await asyncio.wait_for(ack, samples / ev.sample_rate + 15)
                        finally:
                            playback.pop((generation, aid), None)
                            windows.pop((generation, aid), None)
                    incomplete = False
                    last_text = None
                    current.speech_delivered()
                return
            if ev.kind in INTERNAL_KINDS:
                return
            payload = ev.to_dict()
            payload.update(generation=generation, session_id=call.id,
                           client_turn=request_turn)
            if ev.kind == "transcript" and ev.speaker == "agent":
                last_text = ev.text
                response_role = "opening" if opening else ev.response_role
                incomplete = True
            if ev.kind == "end":
                trace.finish("completed")  # before the call record is flushed
            RECORDER.event(call, payload)
            await transport.send(payload, generation)

        async def drain(events):
            # Explicit closure is essential when cancellation lands in emit,
            # outside the generator's own await (e.g. waiting for playback).
            async with aclosing(events):
                async for ev in events:
                    await emit(ev)

        try:
            if pcm is not None:
                pcm = trim_silence(pcm, keep_ms=100, sample_rate=sample_rate)
                seconds = len(pcm) / 2 / sample_rate
                ok, why = is_speech(pcm, sample_rate)
                if not ok:
                    result_status = "input_rejected"
                    unheard += 1
                    await transport.send({"kind": "status", "generation": generation,
                        "client_turn": request_turn,
                        "text": "Didn't catch that — go again"}, generation)
                    if unheard >= 2:
                        unheard = 0
                        dialogue_started = True
                        await drain(current.unheard())
                    return
                unheard = 0
                result = await measured.transcribe(pcm, sample_rate)
                if generation != transport.generation:
                    raise asyncio.CancelledError
                if not is_plausible(result.text, seconds)[0]:
                    result_status = "input_rejected"
                    await transport.send({"kind": "status", "generation": generation,
                        "client_turn": request_turn,
                        "text": "Didn't catch that — go again"}, generation)
                    return
                text, lang = result.text, result.lang
            dialogue_started = True
            await drain(current.start() if opening else
                        current.on_caller(text, lang, started_at=started_at))
        except asyncio.CancelledError:
            result_status = "interrupted"
            raise
        except InferenceBusy:
            result_status = "overloaded"
            await transport.send({"kind": "status", "generation": generation,
                "client_turn": request_turn,
                "text": "Voice service is busy. Please try again shortly."}, generation)
        except Exception:
            result_status = "error"
            raise
        finally:
            await current.cancel_pending()
            current.backend = backend
            trace.finish(result_status)
            if (generation != transport.generation or result_status == "overloaded") and dialogue_started:
                # Preserve accepted facts/actions; only mark unfinished speech.
                # No rollback of a confirmed customer request or emitted tool.
                current.interrupt(last_text if incomplete else None)
                RECORDER.event(call, {"kind": "interrupted",
                    "generation": generation, "turn": current.turn,
                    "speech_complete": bool(aid) and not incomplete})
            if generation == transport.generation:
                await transport.send({"kind": "response_done", "generation": generation,
                                      "client_turn": request_turn}, generation)
            if ack is not None:
                playback.pop((generation, aid), None)
                if not ack.done():
                    ack.cancel()

    def finish_trace(status):
        if active_trace is not None:
            active_trace.finish(status)

    def launch(*, input_kind="typed", endpoint_ms=None, **kwargs):
        nonlocal active_trace
        finish_trace("interrupted")
        current, call = session, record
        clear_playback()
        request_turn = client_turn
        cfg = _state["cfg"]
        condition = cfg.get("telemetry", {}).get("benchmark_state", "unknown")
        if condition not in ("cold", "warm", "unknown"):
            condition = "unknown"
        models = {k: cfg.get("backend", {}).get(k, {}).get("model", "unknown")
                  for k in ("asr", "llm", "tts")}
        if getattr(backend, "_cached_voice", False):
            models["tts"] = backend.prerender.cfg["model"]
        trace = ResponseTrace({
            "session_id": call.id, "client_turn": request_turn,
            "profile": cfg.get("profile", "unknown"), "input_kind": input_kind,
            "declared_model_state": condition, "endpoint_ms": endpoint_ms,
            "language": current.lang, "voice": current.voice or "default",
            "models": models,
        }, lambda payload: RECORDER.event(call, payload))
        active_trace = trace
        generation = transport.submit(
            lambda g: respond(g, current, call, trace, request_turn=request_turn, **kwargs),
            lambda: None, client_turn=request_turn)
        trace.metadata["generation"] = generation

    try:
        while True:
            msg = await transport.receive(sock.receive)
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                if session is not None and not overflow:
                    data = msg["bytes"]
                    if len(utterance) + len(data) > max_audio:
                        utterance.clear()
                        overflow = True
                    else:
                        utterance.extend(data)
                continue
            if msg.get("text") is None:
                continue
            data = json.loads(msg["text"])
            kind = data.get("type")
            if kind in ("start", "say", "barge_in", "hangup", "utterance_end"):
                client_turn = int(data.get("client_turn", client_turn))
            if kind == "start":
                finish_trace("interrupted")
                transport.invalidate("start", client_turn)
                clear_playback()
                RECORDER.finish(record)
                policy = _with_surname(personas.get(data["policy_id"]),
                                       data.get("surname"), data.get("salutation"))
                register = data.get("register", _state["cfg"].get("register", "standard"))
                voice = data.get("voice") or _default_voice()
                lang = data.get("lang") or policy.language
                guard = _state["cfg"].get("guardrail", {})
                session = CallSession(policy, backend, lang=lang, register=register,
                    voice=voice, guardrail=guard.get("enabled", True),
                    guardrail_timeout_ms=guard.get("timeout_ms", 1500),
                    knowledge=knowledge_policy.default_serving())
                # A caller may speak before the opening task even gets a turn.
                # Identity must already be pending; the opening is not heard yet.
                session.prepare_start()
                session.interrupt()
                record = RECORDER.start(policy_id=policy.policy_id, name=policy.name,
                                        register=register, voice=voice, lang=lang)
                protocol = 2 if data.get("audio_protocol") == 2 else 1
                if not counted:
                    _state["live_calls"] = _state.get("live_calls", 0) + 1
                    counted = True
                utterance.clear()
                overflow = False
                unheard = 0
                launch(opening=True, input_kind="opening", flow_control=data.get("audio_flow") is True)
                transport.notify({"kind": "call_started", "id": record.id,
                    "generation": transport.generation, "client_turn": client_turn})
            elif kind == "utterance_end" and session is not None:
                if overflow:
                    overflow = False
                    transport.notify({"kind": "status", "client_turn": client_turn,
                        "text": "Please keep each reply under 30 seconds."})
                    continue
                if not utterance:
                    continue
                pcm = bytes(utterance)
                utterance.clear()
                if len(pcm) % 2:
                    continue
                trailing = min(5000, max(0, float(data.get("trailing_ms") or 0))) / 1000
                launch(pcm=pcm, started_at=time.perf_counter() - trailing,
                       flow_control=data.get("audio_flow") is True,
                       input_kind="microphone", endpoint_ms=milliseconds(data.get("endpoint_ms")))
            elif kind == "say" and session is not None:
                utterance.clear()
                overflow = False
                launch(text=str(data["text"])[:4000], lang=data.get("lang"),
                       flow_control=data.get("audio_flow") is True)
            elif kind == "audio_consumed":
                key = (data.get("generation"), data.get("audio_id"))
                if key[0] == transport.generation and key in windows:
                    windows[key].acknowledge(data.get("samples"))
            elif kind == "playback_error":
                key = (data.get("generation"), data.get("audio_id"))
                if key[0] == transport.generation and key in playback:
                    raise RuntimeError("Client playback failed")
            elif kind == "playback_started":
                key = (data.get("generation"), data.get("audio_id"))
                if (key in playback and key[0] == transport.generation
                        and active_trace is not None
                        and active_trace.metadata["generation"] == key[0]):
                    row = active_trace.playback_started(key[1], data.get("first_audio_ms"),
                                                        data.get("method"))
                    if row is not None:
                        event = {"kind": "playback_started", "generation": key[0],
                                 "client_turn": active_trace.metadata["client_turn"],
                                 "input_kind": active_trace.metadata["input_kind"], **row}
                        RECORDER.event(record, event)
                        # Only the first answer-bearing utterance feeds the UI p50.
                        answers = [a for a in active_trace.audio.values()
                                   if a["role"] == "answer" and a["client_first_audio_ms"] is not None]
                        event["first_answer"] = row["role"] == "answer" and len(answers) == 1
                        transport.notify(event)
            elif kind == "playback_done":
                key = (data.get("generation"), data.get("audio_id"))
                future = playback.get(key)
                window = windows.get(key)
                if window is not None and (not window.final or window.consumed != window.sent):
                    continue
                if future is not None and not future.done() and key[0] == transport.generation:
                    RECORDER.event(record, {"kind": "playback_done",
                        "generation": key[0], "audio_id": key[1],
                        "underrun_ms": milliseconds(data.get("underrun_ms"))})
                    future.set_result(None)
            elif kind == "discard_utterance":
                utterance.clear()
                overflow = False
            elif kind == "barge_in":
                finish_trace("interrupted")
                transport.invalidate("barge_in", client_turn)
                clear_playback()
                utterance.clear()
                overflow = False
                RECORDER.event(record, {"kind": "barge_in",
                    "generation": transport.generation,
                    "client_stop_ms": data.get("stop_ms")})
            elif kind == "hangup":
                finish_trace("interrupted")
                transport.invalidate("hangup", client_turn)
                clear_playback()
                RECORDER.finish(record)
                session, record = None, None
                utterance.clear()
                overflow = False
                if counted:
                    _state["live_calls"] = max(0, _state.get("live_calls", 1) - 1)
                    counted = False
                transport.notify({"kind": "status", "client_turn": client_turn,
                                  "text": "Call cancelled"})
    except WebSocketDisconnect:
        pass
    except Exception:
        finish_trace("error")
        log.exception("websocket error")
        await transport.close()
        await sock.close()
    finally:
        finish_trace("interrupted")
        transport.invalidate("disconnect")
        clear_playback()
        await transport.close()
        RECORDER.finish(record)
        if counted:
            _state["live_calls"] = max(0, _state.get("live_calls", 1) - 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Tiq renewal voicebot console")
    parser.add_argument("--profile", default=None,
                        help="config profile: mock (default) or mac")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    _state["cfg"] = config.load(args.profile)
    _backend()

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
