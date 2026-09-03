"""Saying a customer's name correctly.

The voice's English letter-to-sound rules were not trained on Singaporean
romanisations: "Tan" comes out rhyming with "tang", "Yeo" as "ye", "Ng" as the
Korean "ing". These are Hokkien, Teochew and Cantonese spellings, so the
Mandarin front-end is not the answer either — 陈先生 is "Chen", and a customer
called Tan is not called Chen. Respelling what the synthesiser sees is the only
lever this stack leaves.
"""
import dataclasses

import pytest

from voicebot.call import script
from voicebot.data import personas
from voicebot.spoken import sayable, segment_by_script, spoken_names

P = personas.get("TH-4471-0093")


def _as(surname, salutation="Mr"):
    return dataclasses.replace(P, surname=surname, salutation=salutation)


def test_a_surname_is_respelled_for_the_synthesiser():
    assert spoken_names("Good afternoon Mr Tan.") == "Good afternoon Mr Dan."


def test_the_same_name_is_respelled_everywhere_it_appears():
    """Turn 1 says it twice. Fixing only the first is a call that pronounces
    the customer's name two different ways in one sentence."""
    said = spoken_names("Good afternoon Mr Tan. Am I speaking with Mr Tan?")
    assert "Tan" not in said and said.count("Dan") == 2


def test_only_a_name_is_respelled():
    """The lexicon rewrites graphemes, and these words appear in ordinary text.
    A substitution that fired on all of them would be a bug visible only in
    audio, which is the hardest kind to notice."""
    for text in ("a tan leather bag", "suntan lotion", "the tank is full"):
        assert spoken_names(text) == text


def test_a_name_with_no_working_spelling_is_not_said():
    """No respelling produced a syllabic "ng". Getting someone's name wrong in
    the sentence that asks them to confirm they are that person fails the trust
    test the turn exists to run."""
    assert not sayable("Ng")
    line = script.render(1, _as("Ng", "Ms"), "en", agent_name="Michael")
    assert "Ng" not in line
    assert "the policyholder" in line


@pytest.mark.parametrize("lang,register", [("en", "standard"), ("en", "singlish"),
                                           ("zh", "standard")])
def test_the_fallback_holds_in_every_register(lang, register):
    for turn in (1, 7):
        line = script.render(turn, _as("Ng", "Ms"), lang, register=register,
                             agent_name="Michael")
        assert "Ng" not in line, (lang, register, turn)


def test_a_name_the_voice_says_correctly_is_left_alone():
    """Lim, Goh, Chua, Ong, Toh, Koh, Teo and Eng all measured fine. A lexicon
    that touched them would be churn and a re-render for nothing."""
    line = script.render(1, _as("Lim"), "en", agent_name="Michael")
    assert "Mr Lim" in line
    assert spoken_names(line) == line


def test_the_respelling_reaches_the_synthesiser():
    """It has to happen where the audio is actually made, not in the script —
    the transcript, the record and the console keep the customer's own
    spelling."""
    line = script.render(1, P, "en", agent_name="Michael")
    assert "Mr Tan" in line, "the transcript keeps the real name"
    spoken = "".join(frag for frag, _ in segment_by_script(line, "en"))
    assert "Dan" in spoken and "Tan" not in spoken


def test_a_missing_lexicon_is_not_fatal():
    """Every name simply keeps its spelling, which is what happened before the
    file existed."""
    import voicebot.spoken as sp

    saved = sp._NAMES_CACHE
    try:
        sp._NAMES_CACHE = {}
        assert sp.spoken_names("Good afternoon Mr Tan.") == "Good afternoon Mr Tan."
        assert sp.sayable("Ng")
    finally:
        sp._NAMES_CACHE = saved


# --- trying a name from the console ---------------------------------------

def test_a_surname_override_does_not_mutate_the_record():
    """`personas.get` hands back the module-level object. Editing it would
    follow the operator into every later call in the process — the same
    contamination that made a test pass only when another ran first."""
    from voicebot.server import _with_surname

    before = personas.get("TH-4471-0093").surname
    changed = _with_surname(personas.get("TH-4471-0093"), "Balakrishnan", "Mr")
    assert changed.surname == "Balakrishnan"
    assert changed.name.endswith("Balakrishnan")
    assert personas.get("TH-4471-0093").surname == before


def test_a_blank_override_keeps_the_record():
    from voicebot.server import _with_surname

    p = personas.get("TH-4471-0093")
    assert _with_surname(p, "", None) is p
    assert _with_surname(p, None, None) is p


def test_the_console_can_see_what_the_voice_will_be_handed():
    """The transcript always reads "Mr Tan"; only the synthesiser sees
    "Mr Dan". Without this the operator cannot tell the lexicon fired."""
    from fastapi.testclient import TestClient

    from voicebot.server import app

    c = TestClient(app)
    tan = c.get("/api/name", params={"surname": "Tan"}).json()
    assert tan["sayable"] and tan["spoken_as"] == "Mr Dan"
    assert "Mr Dan" in tan["synthesised"] and "Mr Tan" in tan["line"]

    ng = c.get("/api/name", params={"surname": "Ng"}).json()
    assert not ng["sayable"]
    assert "Ng" not in ng["line"] and "the policyholder" in ng["line"]

    unknown = c.get("/api/name", params={"surname": "Balakrishnan"}).json()
    assert unknown["sayable"] and unknown["spoken_as"] == "Mr Balakrishnan"


def test_an_edited_lexicon_shows_up_without_a_restart():
    """The loop this exists for is: type a name, listen, edit the yaml, listen
    again. A cached lexicon would make the second listen a lie."""
    from fastapi.testclient import TestClient

    from voicebot.server import app
    import voicebot.spoken as sp

    c = TestClient(app)
    assert c.get("/api/name", params={"surname": "Tan"}).json()["spoken_as"] == "Mr Dan"
    saved = sp._NAMES_CACHE
    try:
        sp._NAMES_CACHE = {"Tan": {"say": "Tahn"}}
        # The endpoint reloads from disk, so the stale cache does not survive.
        assert c.get("/api/name",
                     params={"surname": "Tan"}).json()["spoken_as"] == "Mr Dan"
    finally:
        sp._NAMES_CACHE = saved


def test_the_lexicon_is_found_from_any_working_directory():
    """A relative path resolves against wherever the process was started — a
    systemd unit with no WorkingDirectory, a container entered at /, a test run
    from elsewhere. The failure is silent: a lexicon that cannot be read looks
    exactly like an empty one, and every name quietly keeps its own spelling
    on the box where it matters most."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r);"
         "from voicebot.spoken import spoken_names;"
         "print(spoken_names('Mr Tan'))" % str(root / "src")],
        cwd=os.sep, capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "Mr Dan", out.stderr[-400:]
