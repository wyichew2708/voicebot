"""A call says a salutation and one name.

"Mr Chew Yi Feng" is how a form letter opens, not how a person does, and on
turn 1 — which asks someone to confirm they are that person — reading the
whole name out sounds like a list being worked through.

The record is meant to carry the surname on its own, and the three demo
personas do. Everything here is the net under that: an operator typing a full
name into the console, or a CRM field that turns out to hold one, must not
reach the voice whole.
"""
import dataclasses

import pytest

from voicebot.call import script
from voicebot.data import personas
from voicebot.spoken import surname_of

P = personas.get("TH-4471-0093")


def _as(name, salutation="Mr"):
    return dataclasses.replace(P, surname=name, salutation=salutation,
                               name=name)


# --- the two the operator asked for ---------------------------------------

@pytest.mark.parametrize("full,said", [
    ("Chew Yi Feng", "Chew"),
    ("Chuan Ping Fong", "Chuan"),
])
def test_the_names_that_prompted_this(full, said):
    assert surname_of(full) == said


# --- surname-first, which most Singaporean Chinese records are -------------

@pytest.mark.parametrize("full,said", [
    ("Tan Wei Ming", "Tan"),
    ("Lim Mei Ling", "Lim"),
    ("Ng Kok Wah", "Ng"),
    ("Ong Boon Huat", "Ong"),
    ("Sim Hui Ling", "Sim"),
])
def test_a_name_written_surname_first_is_read_from_the_front(full, said):
    assert surname_of(full) == said


# --- Western order, which the demo personas are ---------------------------

@pytest.mark.parametrize("full,said", [
    ("Andrew Tan", "Tan"),
    ("Rachel Ng", "Ng"),
    ("Grace Lim", "Lim"),
    ("Michael Wong", "Wong"),
])
def test_a_western_given_name_puts_the_surname_last(full, said):
    assert surname_of(full) == said


def test_a_western_given_name_before_a_chinese_one_takes_the_middle():
    """"Andrew Tan Wei Ming" is Mr Tan. Reading the last word would make him
    Mr Ming, which is his given name."""
    assert surname_of("Andrew Tan Wei Ming") == "Tan"
    assert surname_of("Jessica Lim Xiu Ying") == "Lim"


def test_two_words_neither_of_them_western_prefers_the_one_that_is_a_surname():
    assert surname_of("Xiaoli Tan") == "Tan"
    assert surname_of("Meiling Wong") == "Wong"


# --- names with no surname at all -----------------------------------------

@pytest.mark.parametrize("full,said", [
    ("Muhammad Farid bin Abdullah", "Farid"),
    ("Siti Nurhaliza binte Rahman", "Nurhaliza"),
    ("Nurul Ain binte Ismail", "Ain"),
    ("Rajesh s/o Kumar", "Rajesh"),
    ("Priya d/o Raman", "Priya"),
    ("Ahmad Faizal bin Osman", "Faizal"),
])
def test_a_patronymic_names_the_father_and_is_not_said(full, said):
    """"bin Abdullah" is whose son he is, not what he is called. And the
    religious opener is not it either: addressing every second man on the list
    as Mr Muhammad is worse than not using a name at all."""
    assert surname_of(full) == said


# --- the shapes that arrive by accident -----------------------------------

def test_a_single_name_is_returned_unchanged():
    """The path every existing record takes. If this ever moved, all three
    personas would be re-addressed and every cached line would miss."""
    for one in ("Tan", "Ng", "Lim", "Balakrishnan"):
        assert surname_of(one) == one


@pytest.mark.parametrize("given,said", [
    ("Mr Chew Yi Feng", "Chew"),
    ("Madam Chuan Ping Fong", "Chuan"),
    ("Mdm. Lim Mei Ling", "Lim"),
    ("Dr Andrew Tan", "Tan"),
])
def test_a_salutation_typed_into_the_box_is_not_taken_for_a_name(given, said):
    assert surname_of(given) == said


def test_a_generational_suffix_is_not_a_name():
    assert surname_of("Andrew Tan Jr") == "Tan"
    assert surname_of("Andrew Tan Jr.") == "Tan"


@pytest.mark.parametrize("messy", ["", "   ", None, ",", "Mr", "Mr."])
def test_nothing_to_say_is_not_an_error(messy):
    """An empty record addresses the caller as the policyholder rather than
    raising in the middle of rendering turn 1."""
    assert surname_of(messy) == ""


def test_extra_whitespace_and_commas_do_not_change_the_answer():
    assert surname_of("  Chew   Yi  Feng ") == "Chew"
    assert surname_of("Tan, Wei Ming") == "Tan"


# --- what the call actually says ------------------------------------------

@pytest.mark.parametrize("lang", ["en", "zh"])
def test_no_scripted_turn_says_more_of_the_name_than_the_surname(lang):
    """The property, checked against every turn rather than the two that were
    known to name the customer."""
    p = _as("Chew Yi Feng")
    for turn in range(1, 8):
        line = script.render(turn, p, lang)
        assert "Yi Feng" not in line and "Chew Yi Feng" not in line, (turn, line)


@pytest.mark.parametrize("register", ["standard", "singlish"])
def test_the_english_turns_address_them_by_the_surname(register):
    line = script.render(1, _as("Chew Yi Feng"), "en", register=register)
    assert "Mr Chew" in line
    assert "Yi Feng" not in line


def test_the_mandarin_turns_address_them_by_the_surname():
    line = script.render(1, _as("Chuan Ping Fong", "Madam"), "zh")
    assert "Chuan女士" in line
    assert "Ping Fong" not in line


def test_the_closing_turn_too():
    """Turn 7 thanks the customer by name, and was the second site."""
    line = script.render(7, _as("Chew Yi Feng"), "en")
    assert "Mr Chew" in line and "Yi Feng" not in line


def test_madam_is_a_salutation_the_script_knows():
    """It was not in the list, so "Madam Chuan" fell through to 先生 on a
    Mandarin call — addressing a woman as a man."""
    assert "女士" in script.render(1, _as("Chuan", "Madam"), "zh")


def test_a_name_no_spelling_fixes_is_still_left_out():
    """The existing `sayable` gate has to see the derived surname, not the
    whole name — "Ng Kok Wah" is not in the lexicon, "Ng" is."""
    assert "policyholder" in script.render(1, _as("Ng Kok Wah"), "en")


# --- the console ----------------------------------------------------------

def test_a_full_name_typed_into_the_console_is_reduced_for_the_call():
    from voicebot.server import _with_surname

    got = _with_surname(P, "Chew Yi Feng", "Mr")
    assert got.surname == "Chew"
    assert got.name == "Chew Yi Feng"        # the operator sees what they typed
    assert personas.get("TH-4471-0093").surname == "Tan"      # not mutated


def test_a_bare_surname_still_keeps_the_record_given_name():
    from voicebot.server import _with_surname

    got = _with_surname(P, "Chew", "Mr")
    assert (got.surname, got.name) == ("Chew", "Andrew Chew")


def test_the_console_is_told_which_word_will_be_said(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from voicebot import config, server

    server._state.clear()
    server._state["cfg"] = config.load("mock")
    try:
        with TestClient(app=server.app) as c:
            full = c.get("/api/name", params={"surname": "Chew Yi Feng",
                                              "salutation": "Mr"}).json()
            one = c.get("/api/name", params={"surname": "Tan"}).json()
    finally:
        server._state.clear()

    assert full["said_as"] == "Chew" and full["shortened"] is True
    assert "Yi Feng" not in full["line"] and "Mr Chew" in full["line"]
    assert one["said_as"] == "Tan" and one["shortened"] is False
