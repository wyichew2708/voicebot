"""The listening gallery and the male/female that preset models follow."""
import io
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voicebot import samples as S

ROOT = Path(__file__).resolve().parents[1]


def test_every_catalogued_sample_exists_and_says_who_is_speaking():
    rows = S.listing()
    shipped = [r for r in rows if r["source"] == "shipped"]
    assert len(shipped) >= 50
    genders = {r["gender"] for r in shipped}
    assert {"female", "male"} <= genders
    for r in shipped:
        assert r["seconds"] and r["seconds"] > 0, r["name"]
        assert r["gender"] in ("female", "male", "unknown"), r["name"]
    # The shipped voices themselves are in the gallery, one of each.
    labels = {r["name"]: r for r in shipped}
    assert labels["final_female_greeting.wav"]["gender"] == "female"
    assert labels["final_male_greeting.wav"]["gender"] == "male"
    # Nothing in the directory escaped the catalogue.
    assert all(r["note"] != "not in samples/index.yaml" for r in shipped), \
        [r["name"] for r in shipped if r["note"] == "not in samples/index.yaml"]


def test_a_sample_id_cannot_leave_the_two_directories(tmp_path):
    assert S.resolve("shipped/aiden.wav") == S.SHIPPED / "aiden.wav"
    assert S.resolve("shipped/../config/mock.yaml") is None
    assert S.resolve("shipped/index.yaml") is None
    assert S.resolve("voices/refs/male.wav") is None
    assert S.resolve("rendered/nope.wav") is None


def test_gender_is_guessed_from_a_name_when_nothing_better_is_known():
    assert S.guess_gender("chatterbox--female--en--hello.wav") == "female"
    assert S.guess_gender("kokoro--male--zh--ni-hao.wav") == "male"
    assert S.guess_gender("m2.wav") == "male" and S.guess_gender("f3.wav") == "female"
    assert S.guess_gender("voxcpm2.wav") == "unknown"


@pytest.fixture
def client(tmp_path):
    from voicebot import config, server

    cfg = config.load("mock")
    cfg.setdefault("backend", {}).setdefault("tts", {}).setdefault(
        "prerender", {"cache_dir": str(tmp_path / "cache"), "default_voice": "female",
                      "voices": {"female": {"label": "Female", "gender": "female",
                                            "target_f0": 233},
                                 "michael": {"label": "Michael", "target_f0": 125}}})
    server._state.clear()
    server._state["cfg"] = cfg
    yield TestClient(server.app)
    server._state.clear()


def test_the_console_lists_and_serves_samples(client):
    rows = client.get("/api/samples").json()["samples"]
    one = next(r for r in rows if r["name"] == "final_female_greeting.wav")
    r = client.get("/api/samples/" + one["id"])
    assert r.status_code == 200 and r.headers["content-type"].startswith("audio/wav")
    with wave.open(io.BytesIO(r.content)) as w:
        assert w.getnframes() > 0
    assert client.get("/api/samples/shipped/..%2Fconfig%2Fmock.yaml").status_code in (404, 400)


def test_the_voice_picker_says_female_or_male(client):
    rows = {v["id"]: v for v in client.get("/api/voices").json()["voices"]}
    assert rows["female"]["gender"] == "female"
    assert rows["michael"]["gender"] == "male", "undeclared: inferred from its pitch"


def test_a_line_said_in_the_console_joins_the_gallery(client, monkeypatch, tmp_path):
    monkeypatch.setattr(S, "RENDERED", tmp_path / "say")
    r = client.post("/api/tts/say", json={"text": "Good afternoon Mr Tan.", "model": "kokoro"})
    assert r.status_code == 200
    kept = list((tmp_path / "say").glob("*.wav"))
    assert len(kept) == 1 and kept[0].name.startswith("kokoro--female--en--good-afternoon")
    mine = [s for s in client.get("/api/samples").json()["samples"] if s["source"] == "rendered"]
    assert mine and mine[0]["gender"] == "female" and mine[0]["model"] == "kokoro"


# --------------------------------------------------- presets follow the voice

def test_a_preset_model_follows_the_voices_gender(tmp_path):
    from voicebot import tts_models as T
    from voicebot.runtime.prerender import PrerenderCache

    specs = T.load_registry()
    assert specs["kokoro"].speaker_for("en", "female") == "af_heart"
    assert specs["kokoro"].speaker_for("zh", "male") == "zm_yunjian"
    assert specs["vibevoice"].speaker_for("en", "female") == "Emma"
    kw = T.generate_kwargs(specs["kokoro"], "Hello.", "en", None, None, gender="female")
    assert kw["voice"] == "af_heart"

    pre = PrerenderCache({"cache_dir": str(tmp_path), "voices": {
        "bella": {"gender": "female", "target_f0": 120},      # declared wins
        "eric": {"target_f0": {"en": 161, "zh": 135}},         # inferred: male
        "recorded": {"target_f0": 210}}}, 16000)               # inferred: female
    assert pre.gender_for("bella") == "female"
    assert pre.gender_for("eric") == "male"
    assert pre.gender_for("recorded") == "female"
