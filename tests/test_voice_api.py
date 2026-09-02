"""The console's side of the voice studio.

Runs against the real app with a mock backend, so the request shapes and the
merge into the live profile are exercised — the part that decides whether a
recorded voice is actually offered on the next call.
"""
import math
import shutil

import pytest
from fastapi.testclient import TestClient

RATE = 24000


def _voiced(seconds=12.0, f0=150.0, rate=RATE, amp=9000):
    n = int(seconds * rate)
    out = bytearray()
    for i in range(n):
        t = i / rate
        v = sum(math.sin(2 * math.pi * f0 * k * t) / k for k in (1, 2, 3, 4))
        env = 0.55 + 0.45 * math.sin(2 * math.pi * 3.5 * t)
        out += int(max(-1, min(1, v / 2.1)) * amp * env).to_bytes(2, "little", signed=True)
    return bytes(out)


@pytest.fixture
def client(tmp_path, monkeypatch):
    from voicebot import config, server
    from voicebot.voices import CustomVoices

    monkeypatch.setattr(server, "VOICES", CustomVoices(tmp_path))
    cfg = config.load("mock")
    cfg.setdefault("backend", {}).setdefault("tts", {}).setdefault(
        "prerender", {"cache_dir": str(tmp_path / "cache"),
                      "voices": {"male": {"label": "Male", "target_f0": 162,
                                          "ref_audio": "voices/refs/male.wav"},
                                 "ghost": {"label": "Ghost", "target_f0": 200}}})
    server._state.clear()
    server._state["cfg"] = cfg
    with TestClient(app=server.app) as c:      # startup event loads the backend
        yield c
    server._state.clear()


def _post(client, audio, label="Wei Ming", rate=RATE):
    return client.post(f"/api/voices?sample_rate={rate}&label={label}", content=audio,
                       headers={"Content-Type": "application/octet-stream"})


def test_a_recorded_voice_is_offered_on_the_next_call(client):
    """The whole point. If it does not reach /api/health the console never
    shows it in the voice switch."""
    r = _post(client, _voiced())
    assert r.status_code == 201, r.text
    vid = r.json()["voice"]["id"]
    assert vid in [v["id"] for v in client.get("/api/health").json()["voices"]]
    assert vid in [v["id"] for v in client.get("/api/voices").json()["voices"]]


def test_a_bad_recording_comes_back_with_something_to_do_about_it(client):
    r = _post(client, _voiced(3.0))
    assert r.status_code == 400
    assert "at least" in r.json()["error"]


def test_a_request_without_a_sample_rate_is_refused(client):
    """The rate is not guessable from raw PCM, and guessing it wrong stores
    someone's voice at the wrong pitch and speed."""
    r = client.post("/api/voices?label=X", content=_voiced(),
                    headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 400


def test_only_recorded_voices_are_marked_as_ours_to_edit(client):
    _post(client, _voiced())
    rows = {v["id"]: v for v in client.get("/api/voices").json()["voices"]}
    assert rows["wei-ming"]["custom"] is True
    assert rows["male"]["custom"] is False


def test_the_clip_can_be_played_back(client):
    _post(client, _voiced())
    r = client.get("/api/voices/wei-ming/sample.wav")
    assert r.status_code == 200 and r.content[:4] == b"RIFF"
    assert client.get("/api/voices/nobody/sample.wav").status_code == 404


def test_pitch_survives_a_round_trip(client):
    _post(client, _voiced())
    base = client.get("/api/voices").json()["voices"]
    base = [v for v in base if v["id"] == "wei-ming"][0]["target_f0"]
    r = client.post("/api/voices/wei-ming/pitch", json={"semitones": -2})
    assert r.status_code == 200
    assert r.json()["voice"]["target_f0"] < base


def test_deleting_a_voice_removes_it_from_the_switch(client):
    _post(client, _voiced())
    assert client.delete("/api/voices/wei-ming").status_code == 200
    assert "wei-ming" not in [v["id"] for v in client.get("/api/health").json()["voices"]]
    assert client.delete("/api/voices/wei-ming").status_code == 404


def test_warming_an_unknown_voice_is_a_404_not_a_crash(client):
    assert client.post("/api/voices/nobody/warm").status_code == 404


def test_every_voice_can_be_warmed_from_the_console(client):
    """A shipped voice needs warming too — the first call in a cold voice
    synthesises each line live, two to four seconds a turn. Before this the
    only way to warm one was the command line."""
    _post(client, _voiced())
    rows = client.get("/api/voices").json()["voices"]
    assert {"male", "wei-ming"} <= {v["id"] for v in rows}
    for vid in ("male", "wei-ming"):
        assert client.post(f"/api/voices/{vid}/warm").status_code == 200


def test_a_shipped_voice_is_not_the_consoles_to_delete_or_retune(client):
    """Its pitch is in the cache key, and every line already rendered against
    it belongs to the deployment, not to whoever opened the console."""
    assert client.delete("/api/voices/male").status_code == 404
    assert client.post("/api/voices/male/pitch",
                       json={"semitones": 2}).status_code == 400


def test_a_voice_is_warm_because_the_cache_says_so(client, tmp_path):
    """Not because this process happens to have run the job. A voice warmed
    by `make prerender`, or shipped with the deploy, has no job to its name —
    and was reported cold, which is exactly backwards for the two voices that
    ship warm."""
    from voicebot import server
    from voicebot.runtime import warm as plan

    rows = {v["id"]: v for v in client.get("/api/voices").json()["voices"]}
    assert rows["male"]["warm"]["state"] == "cold"
    assert rows["male"]["warm"]["total"] > 0, "nothing planned means nothing to warm"

    # Fill the cache for every line the plan asks for, without rendering.
    cfg = server._state["cfg"]
    cache_dir = tmp_path / "cache"
    from voicebot.runtime.prerender import PrerenderCache
    pc = PrerenderCache(server._prerender_cfg(), cfg["audio"]["sample_rate"])
    for job in plan.plan(cfg.get("languages", ["en"]), ["standard", "singlish"],
                         ["male"]):
        p = pc.path(*job)
        p.parent.mkdir(parents=True, exist_ok=True)
        import wave
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 100)

    rows = {v["id"]: v for v in client.get("/api/voices").json()["voices"]}
    assert rows["male"]["warm"]["state"] == "done"
    assert rows["male"]["warm"]["done"] == rows["male"]["warm"]["total"]


def test_a_voice_with_nothing_to_play_says_so(client):
    """The picker hides the play button rather than offering a control that
    404s."""
    rows = {v["id"]: v for v in client.get("/api/voices").json()["voices"]}
    assert rows["male"]["sample"] is True
    assert rows["ghost"]["sample"] is False
    assert client.get("/api/voices/ghost/sample.wav").status_code == 404


def test_a_voice_can_be_auditioned_before_it_is_chosen(client):
    """Choosing between six voices by name alone is guessing. The console
    plays a rendered line where one is cached and falls back to the reference
    clip otherwise — and says which of the two it played, because a clone is
    not its reference."""
    r = client.get("/api/voices/male/sample.wav")
    assert r.status_code == 200
    assert r.content[:4] == b"RIFF"
    assert r.headers.get("X-Sample") in ("rendered", "reference")
    assert client.get("/api/voices/nobody/sample.wav").status_code == 404


def test_every_voice_carries_the_note_the_picker_shows(client):
    rows = client.get("/api/voices").json()["voices"]
    assert all("note" in v and "target_f0" in v for v in rows)


def test_warming_yields_to_a_live_call(client, monkeypatch):
    """Warming and speaking use the same GPU, and a line the caller is
    waiting on loses badly to a batch job: a turn that reads from a warm
    cache in 5 ms took 13.6 seconds with a warm-up running behind it."""
    import inspect

    from voicebot import server

    src = inspect.getsource(server._warm_voice)
    assert "_on_call()" in src, "the warm-up does not check for a live call"
    assert "time.sleep" in src

    server._state["live_calls"] = 0
    assert server._on_call() is False
    server._state["live_calls"] = 1
    assert server._on_call() is True
    server._state["live_calls"] = 0


def test_the_call_count_cannot_leak(client):
    """It decides whether the warm-up may use the GPU. Leaked, every warm-up
    idles for the life of the process."""
    import inspect

    from voicebot import server

    src = inspect.getsource(server.ws)
    assert "finally:" in src
    assert 'max(0, _state.get("live_calls", 1) - 1)' in src


def test_the_console_says_whether_a_call_is_up(client):
    """So a warm-up running in another process can stand aside too — the
    server-side one checks a flag it owns, and `make prerender` cannot see
    that flag at all."""
    assert client.get("/api/health").json()["on_call"] is False


def test_the_command_line_warm_up_stands_aside_for_a_call():
    from pathlib import Path

    src = Path("scripts/prerender.py").read_text()
    assert "_wait_for_the_line" in src
    assert "on_call" in src
    # A console that is down must not stop a warm-up: this is a courtesy,
    # not a lock.
    fn = src[src.index("def _wait_for_the_line"):]
    assert "except Exception:" in fn and "return waited" in fn
