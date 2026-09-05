"""The TTS benchmark.

No model here. What is worth pinning: that the sentence set actually contains
the things an insurance call trips over, that a candidate is sent exactly what
a live call would send, and that the numbers come out the same way for every
model — a benchmark that measures two engines two ways ranks nothing.
"""
import asyncio
import io
import json
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from voicebot import tts_bench as B


# ---------------------------------------------------------------- the set

def test_the_set_covers_what_an_insurance_call_is_made_of():
    lines = B.sentences()
    text = "\n".join(l.text for l in lines)
    for must in ("S$1,284.60", "TH-4471-0093", "#08-212", "a.tan@example.sg",
                 "MediShield Life", "Integrated Shield Plan", "CPF", "6887 8777",
                 "10 February 2026", "Etiqa", "lah", "终身健保", "公积金", "新元"):
        assert must in text, must
    assert {l.lang for l in lines} == {"en", "zh"}
    assert {"script", "singlish", "money", "policy", "address"} <= {l.group for l in lines}


def test_every_line_has_a_stable_unique_id():
    ids = [l.id for l in B.sentences()]
    assert len(ids) == len(set(ids))
    assert ids == [l.id for l in B.sentences()], "ids must not depend on order of a run"


def test_the_scripted_turns_are_the_real_ones():
    """Not a paraphrase: the exact wording the call says, in both languages
    and both registers, so the benchmark measures the product."""
    from voicebot.call import script
    from voicebot.data import personas

    policies = personas.all_policies()
    by_id = {l.id: l for l in B.sentences()}
    assert by_id["script-en-p1-t1"].text == script.render(1, policies[0], "en")
    assert by_id["script-zh-p1-t5"].text == script.render(5, policies[0], "zh")
    assert by_id["singlish-en-p1-t1"].text == \
        script.render(1, policies[0], "en", register="singlish")
    # Every persona: the surnames, addresses and figures that differ between
    # them are the parts of a line a voice gets wrong.
    assert by_id[f"script-en-p{len(policies)}-t2"].text == script.render(2, policies[-1], "en")


def test_product_mode_hands_the_voice_what_a_call_would():
    """The written policy number never reaches the model on a live call —
    the spoken layer spells it. The benchmark's `said` is that spelling, so
    CER is scored against what was actually asked for."""
    by_id = {l.id: l for l in B.sentences()}
    assert "T H four four seven one zero zero nine three" in by_id["policy-en"].said
    assert "四四七一" in by_id["policy-zh"].said
    assert by_id["money-en"].said == by_id["money-en"].text, \
        "money is a quantity and is left for the model to read"


def test_groups_and_languages_filter():
    only = B.sentences(("zh",), ("money", "policy"))
    assert only and all(l.lang == "zh" and l.group in {"money", "policy"} for l in only)


# ---------------------------------------------------------------- metrics

def test_cer_is_over_characters_with_noise_removed():
    assert B.cer("Six eight eight seven.", "six eight eight seven") == 0.0
    assert B.cer("abc", "abd") == pytest.approx(1 / 3, abs=1e-3)
    assert B.cer("六八八七", "六八八") == 0.25
    assert B.cer("", "") == 0.0
    assert B.cer("", "noise") == 1.0


def _r(target, line, ms, f0=None, voiced=0, ok=True, cer=None, seconds=1.0):
    return B.Rendering(target=target, line=line, ok=ok, ms=ms, seconds=seconds,
                       rtf=ms / 1000 / seconds if ok else None, f0=f0, voiced=voiced,
                       cer=cer)


def test_summary_reports_latency_failures_and_drift():
    rows = [_r("a", "1", 100, 160, 40), _r("a", "2", 200, 160, 40),
            _r("a", "3", 300, 160 * 2 ** (1 / 12), 40),          # one semitone up
            _r("a", "4", 999, ok=False),
            _r("b", "1", 50, 200, 40, cer=0.1), _r("b", "2", 70, 200, 40, cer=0.3)]
    s = B.summarise(rows)
    assert s["a"]["lines"] == 4 and s["a"]["failed"] == 1
    assert s["a"]["p50_ms"] == 200 and s["a"]["p95_ms"] == 290
    assert 0.4 < s["a"]["drift_semitones"] < 0.6          # pstdev of {0, 0, 1}
    assert s["a"]["cer_mean"] is None
    assert s["b"]["cer_mean"] == 0.2
    assert s["b"]["drift_semitones"] is None, "two lines are not a spread"


def test_a_pitch_on_too_few_frames_does_not_count_toward_drift():
    """The same rule as the pre-render normaliser: a median over a handful of
    voiced frames does not describe the line, and one such line would put a
    steady speaker at the bottom of the table."""
    rows = [_r("a", "1", 100, 160, 40), _r("a", "2", 100, 160, 40),
            _r("a", "3", 100, 160, 40), _r("a", "4", 100, 90, voiced=3)]
    assert B.summarise(rows)["a"]["drift_semitones"] == 0.0


def test_measure_reads_a_failure_as_a_failure():
    line = B.Line("x", "g", "en", "Hello.")
    r = B.measure("a", line, b"", 16000, 120)
    assert not r.ok and r.error
    r = B.measure("a", line, b"\x00\x00" * 16000, 16000, 250)
    assert r.ok and r.seconds == 1.0 and r.rtf == 0.25 and r.f0 is None


# ---------------------------------------------------------- end to end

class _Stub(BaseHTTPRequestHandler):
    """A sidecar that returns a tone and remembers what it was asked."""
    bodies: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"ready": True, "engine": "stub"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n))
        type(self).bodies.append(body)
        import math
        sr = int(body.get("sample_rate", 16000))
        pcm = b"".join(int(8000 * math.sin(2 * math.pi * 150 * i / sr)).to_bytes(2, "little", signed=True)
                       for i in range(int(sr * 0.6)))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm)
        out = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture
def stub():
    _Stub.bodies = []
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", _Stub
    srv.shutdown()


def test_a_run_writes_wavs_results_and_a_page(tmp_path, stub):
    url, S = stub
    lines = B.sentences(("en", "zh"), ("policy", "address"))
    target = B.SidecarTarget("stub", url, refs={"en": "voices/refs/male.wav",
                                                "zh": "voices/refs/zm_yunjian.wav"})
    renderings, summary = asyncio.run(B.run([target], lines, tmp_path))
    assert all(r.ok for r in renderings), [r.error for r in renderings]
    assert (tmp_path / "results.json").exists()
    assert (tmp_path / "index.html").exists()
    for line in lines:
        assert (tmp_path / "stub" / f"{line.id}.wav").exists()
    assert summary["stub"]["lines"] == len(lines) and summary["stub"]["failed"] == 0
    assert summary["stub"]["f0_median"] == pytest.approx(150, abs=3)
    page = (tmp_path / "index.html").read_text()
    assert "stub/policy-en.wav" in page and "handed to the voice" in page


def test_product_mode_sends_the_live_call_contract(stub):
    """Language on every request, the clip chosen by the line's language,
    and a mixed Mandarin line arriving as fragments — because that is what
    the GPU box does, and a benchmark of anything else ranks the wrong thing."""
    url, S = stub
    line = next(l for l in B.sentences(("zh",), ("address",)))
    target = B.SidecarTarget("stub", url, refs={"en": "voices/refs/male.wav",
                                                "zh": "voices/refs/zm_yunjian.wav"})
    asyncio.run(target.render(line))
    assert S.bodies and all(b.get("lang") for b in S.bodies)
    assert [b["lang"] for b in S.bodies] == ["zh", "en", "zh"] or \
        [b["lang"] for b in S.bodies] == ["zh", "en"], [b["lang"] for b in S.bodies]
    assert {b["ref_audio"] for b in S.bodies} == {"voices/refs/zm_yunjian.wav"}
    assert any("Jurong West Street 4" in b["text"] for b in S.bodies)


def test_raw_mode_sends_the_written_line_whole(stub):
    """The other question: what does the model itself make of S$1,284.60?"""
    url, S = stub
    line = next(l for l in B.sentences(("en",), ("money",)))
    target = B.SidecarTarget("stub", url, raw=True,
                             ref_texts={"en": "Good afternoon, this is Michael."})
    pcm = asyncio.run(target.render(line))
    assert pcm
    assert len(S.bodies) == 1
    assert S.bodies[0]["text"] == line.text and "S$1,284.60" in S.bodies[0]["text"]
    assert S.bodies[0]["lang"] == "en"
    assert S.bodies[0]["ref_text"] == "Good afternoon, this is Michael."


def test_a_model_that_fails_a_line_is_a_row_not_a_crash(tmp_path):
    class _Broken:
        name = "broken"
        sample_rate = 16000

        async def render(self, line):
            raise RuntimeError("HTTP 400: language 'zh' is not supported")

    lines = B.sentences(("zh",), ("policy",))
    renderings, summary = asyncio.run(B.run([_Broken()], lines, tmp_path))
    assert summary["broken"]["failed"] == len(lines)
    assert "not supported" in renderings[0].error
    assert "failed" in (tmp_path / "index.html").read_text()


def test_the_markdown_table_has_a_row_per_model():
    table = B.markdown_table({"a": {"lines": 2, "failed": 0, "p50_ms": 10, "p95_ms": 11,
                                    "rtf_mean": 0.1, "f0_median": 160.0,
                                    "drift_semitones": 0.5, "cer_mean": None}})
    assert table.splitlines()[2].startswith("| a | 2 | 0 | 10 | 11 | 0.1 | 160.0 | 0.5 | –")
