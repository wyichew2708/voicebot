import pytest

from voicebot.call.script import TURNS, render, source_label, _zh_date
from voicebot.data import personas

P = personas.get("TH-4471-0093")


def test_all_turns_render_in_both_languages():
    for turn in range(1, 8):
        for lang in ("en", "zh"):
            text = render(turn, P, lang)
            assert text and "{" not in text, f"unfilled slot in turn {turn}/{lang}"


def test_dates_are_localised_for_chinese():
    assert _zh_date("10 February 2026") == "二零二六年二月十日"
    assert "February" not in render(3, P, "zh")


def test_only_turn_six_is_marketing():
    assert [t.n for t in TURNS if t.is_marketing] == [6]


def test_pii_turns_are_flagged():
    assert [t.n for t in TURNS if t.discloses_pii] == [2, 3, 4]


def test_six_of_seven_turns_are_prerendered():
    generated = [t for t in TURNS if t.kind == "generated"]
    assert len(generated) == 0
    assert source_label(5) == "pre-rendered"
    assert source_label(4) == "pre-rendered + slots"


# --- spoken numbers -------------------------------------------------------

def test_the_year_is_spelled_out_because_the_model_misreads_the_numeral():
    """Rendered from "2026" the pre-render voice says "twenty fifty-six" — a
    different year from the one on the customer's policy. Confirmed by
    transcribing the rendered audio back: the numeral comes back wrong, the
    spelled form comes back right."""
    from voicebot.spoken import _en_date

    assert _en_date("10 February 2026") == "10 February twenty twenty-six"
    assert "2026" not in render(3, personas.get("TH-4471-0093"), "en")


@pytest.mark.parametrize("year,spoken", [
    ("2026", "twenty twenty-six"),
    ("2030", "twenty thirty"),
    ("2005", "twenty oh five"),
    ("2000", "twenty hundred"),
    ("1999", "nineteen ninety-nine"),
])
def test_spoken_year(year, spoken):
    from voicebot.spoken import _spoken_year
    assert _spoken_year(year) == spoken


def test_a_date_it_cannot_parse_is_left_alone():
    from voicebot.spoken import _en_date
    assert _en_date("next month") == "next month"


def test_mandarin_dates_are_spelled_out_too():
    """The English fix spelled the year for the English voice; the Mandarin
    voice needed the same thing for the same reason."""
    from voicebot.call.script import _zh_date
    assert _zh_date("10 February 2026") == "二零二六年二月十日"


# --- the one slot that can change mid-call --------------------------------

@pytest.mark.parametrize("lang,register", [("en", "standard"), ("en", "singlish"),
                                           ("zh", "standard")])
def test_turn_four_splits_around_the_email(lang, register):
    """The address is the only slot a caller can change during the call, and
    it sits inside the longest line in the script. Rendering the whole turn on
    a miss cost eight seconds of silence."""
    from voicebot.call.script import split_on_email

    text = render(4, P, lang, register=register)
    split = split_on_email(text)
    assert split is not None, f"no split point for {lang}/{register}"
    head, tail = split
    assert P.email in tail and P.email not in head
    assert head + " " + tail == text or head + tail in text.replace("  ", " ")
    assert len(head) > len(tail), "the cacheable half should be the long one"


def test_the_other_turns_are_left_whole():
    from voicebot.call.script import split_on_email

    for turn in (1, 2, 3, 5, 6, 7):
        assert split_on_email(render(turn, P, "en")) is None


# --- Mandarin numerals ----------------------------------------------------
# The pre-render voice reads Arabic digits inside Chinese text as though they
# were not there: "2026年2月10日" came back from the recogniser as
# "屯溪区棉亭站". Found the same way as the English year — by transcribing our
# own audio rather than by reading the code.

@pytest.mark.parametrize("n,spoken", [
    (5, "五"), (10, "十"), (12, "十二"), (20, "二十"), (105, "一百零五"),
    (110, "一百一十"), (412, "四百一十二"), (388, "三百八十八"),
    (35000, "三万五千"), (60000, "六万"),
])
def test_zh_number(n, spoken):
    from voicebot.spoken import zh_number
    assert zh_number(n) == spoken


def test_the_leading_ten_loses_its_one_only_at_the_front():
    """十二, but 四百一十二."""
    from voicebot.spoken import zh_number
    assert zh_number(12) == "十二"
    assert zh_number(412).endswith("一十二")


@pytest.mark.parametrize("value,spoken", [
    ("23.5", "二十三点五"), ("35,000", "三万五千"), ("412", "四百一十二"),
])
def test_zh_decimal(value, spoken):
    from voicebot.spoken import zh_decimal
    assert zh_decimal(value) == spoken


def test_a_mandarin_year_is_read_digit_by_digit():
    """二零二六年, never 两千零二十六年."""
    from voicebot.spoken import zh_date
    assert zh_date("10 February 2026") == "二零二六年二月十日"


@pytest.mark.parametrize("turn", range(1, 8))
def test_no_arabic_digit_survives_into_a_mandarin_line(turn):
    """Except inside the email address and the policy number, which are latin
    strings either way."""
    import re

    text = render(turn, P, "zh")
    # The property address, the email and the policy number are latin strings
    # either way — they are read as identifiers, not as Mandarin numbers.
    for latin in (P.property_address, P.email, P.policy_id):
        text = text.replace(latin, " ")
    assert not re.search(r"\d", text), f"unspoken digits in turn {turn}: {text}"


def test_the_mandarin_record_answers_have_no_digits_either():
    import re

    from voicebot.data.facts import policy_answers, price_answer

    for key, text in policy_answers(P, "zh").items():
        if key in ("email", "policy", "address"):
            continue                       # latin identifiers, spoken as such
        assert not re.search(r"\d", text), f"unspoken digits in {key}: {text}"
    assert not re.search(r"\d", price_answer(P, "zh"))


# --- mixed scripts --------------------------------------------------------
# A Chinese sentence with a Singapore address, an email or a policy number in
# it is two languages, and one voice cannot read both. Rendered wholly in
# Chinese, "TH-4471-0093" came back as "t h 四 four seven one zero 三 three
# nine three" — different digits from the ones on the customer's policy.

def test_english_lines_are_never_split():
    from voicebot.spoken import segment_by_script
    text = "Your due date is 10 February twenty twenty-six."
    assert segment_by_script(text, "en") == [(text, "en")]


def test_a_policy_number_is_read_entirely_in_mandarin():
    """The Chinese voice reads a couple of letters perfectly well — pulling
    them out made it worse, not better: "T H" alone was too short a fragment
    and the model filled it with invented speech ("t h money and so trusted
    四四七一零零九三")."""
    from voicebot.spoken import segment_by_script
    parts = segment_by_script("您的保单号码是 TH-4471-0093。", "zh")
    assert [lang for _, lang in parts] == ["zh"]
    assert "TH 四四七一 零零九三" in parts[0][0]


def test_only_an_email_leaves_mandarin():
    """The one thing with no Mandarin reading at all."""
    from voicebot.spoken import segment_by_script
    parts = segment_by_script("电邮是 wm.tan@example.sg。", "zh")
    assert [lang for _, lang in parts] == ["zh", "en"]
    assert parts[1][0].startswith("w m dot tan at")


def test_a_phone_number_in_a_mandarin_line_stays_mandarin():
    from voicebot.spoken import segment_by_script
    parts = segment_by_script("请他方便时致电 6887 8777。", "zh")
    assert [lang for _, lang in parts] == ["zh"]
    assert "六八八七 八七七七" in parts[0][0]


def test_the_property_address_is_english_on_a_mandarin_call():
    """The address is read back against something written down — the policy,
    the letter, a colleague's CRM search — so it is spoken as it is written,
    whole and in one voice. Split at all it comes out as an English street
    followed by Mandarin numerals: an address in neither language."""
    from voicebot.spoken import segment_by_script

    from voicebot.spoken import spoken_address
    text = render(2, P, "zh")
    english = [frag for frag, lang in segment_by_script(text, "zh") if lang == "en"]
    assert spoken_address(P.property_address) in english


@pytest.mark.parametrize("turn", range(1, 8))
def test_a_mandarin_turn_is_mandarin_apart_from_names_and_the_email(turn):
    from voicebot.spoken import segment_by_script

    from voicebot.spoken import spoken_address, spoken_identifiers
    from voicebot.call.script import AGENT_NAMES

    # Everything a Mandarin turn is allowed to say in English, and nothing
    # else. The agent's name is on the list because it is an English name:
    # read by the Chinese front-end, "Michelle" is not the word anyone says.
    # Derived from the data rather than typed out, so a renamed persona
    # updates the list and a genuinely new English fragment still fails.
    allowed = {"Etiqa", "Tiq Home", "Tiq Personal Accident",
               spoken_address(P.property_address),
               spoken_identifiers(P.email)}
    allowed |= set(AGENT_NAMES.values())
    text = render(turn, P, "zh")
    for fragment, lang in segment_by_script(text, "zh"):
        if lang == "en":
            assert fragment in allowed, \
                f"turn {turn} leaves Mandarin for {fragment!r}"


@pytest.mark.parametrize("run,spoken", [
    ("TH-4471-0093", "T H 4 4 7 1 0 0 9 3"),
    ("wm.tan@example.sg", "w m dot tan at example dot s g"),
    ("hl.lim@example.sg", "h l dot lim at example dot s g"),
])
def test_identifiers_are_spelled_the_way_a_person_reads_them(run, spoken):
    from voicebot.spoken import spell_identifier
    assert spell_identifier(run) == spoken


def test_a_short_name_does_not_earn_a_seam():
    """The Chinese front-end reads "Tan先生" perfectly well, and every seam is
    a join the ear can hear: pulled out on its own, "Dave" rendered as
    "dave dave concentration"."""
    from voicebot.spoken import segment_by_script
    parts = segment_by_script("下午好，Tan先生。请问是Tan先生本人吗？Dave。", "zh")
    assert len(parts) == 1


def test_punctuation_alone_is_never_rendered():
    """A trailing "。" on its own produced several seconds of speech that was
    never in the script — the model invents a sentence to fill it."""
    from voicebot.spoken import _HAS_SPEECH, segment_by_script
    for text in ("您的保单号码是 TH-4471-0093。", "电邮是 wm.tan@example.sg，对吗？"):
        for fragment, _ in segment_by_script(text, "zh"):
            assert _HAS_SPEECH.search(fragment), f"silent fragment: {fragment!r}"


def test_the_address_keeps_its_digits_rather_than_being_spelled_out():
    """A Singapore address is read, not dictated."""
    from voicebot.spoken import spell_identifier
    assert spell_identifier("Jurong West Street 4, #08-212") == \
        "Jurong West Street 4, #08-212"


@pytest.mark.parametrize("run,spoken", [
    ("6887 8777", "6 8 8 7 8 7 7 7"),
    ("#08-212", "0 8 2 1 2"),
])
def test_a_number_with_no_units_is_read_digit_by_digit(run, spoken):
    """A phone number read as a quantity becomes a different thing entirely:
    "6887 8777" came back as "sixteen eight seven"."""
    from voicebot.spoken import spell_identifier
    assert spell_identifier(run) == spoken


def test_the_cache_key_covers_how_a_line_is_spelled():
    """Otherwise a fix to the spelling silently serves the rendering it was
    meant to replace."""
    from voicebot import config
    from voicebot.runtime.prerender import PrerenderCache

    cfg = config.load("mac-polyglot")
    cache = PrerenderCache(cfg["backend"]["tts"]["prerender"], 16000)
    zh = cache.key("电话是 6887 8777。", "zh")
    en = cache.key("Your due date is 10 February.", "en")

    import voicebot.runtime.prerender as pr
    original = pr.segment_by_script
    pr.segment_by_script = lambda text, lang: [(text.replace("6887", "9999"), lang)]
    try:
        assert cache.key("电话是 6887 8777。", "zh") != zh, "spelling is not keyed"
        assert cache.key("Your due date is 10 February.", "en") == en, \
            "an English key moved for a change that cannot affect it"
    finally:
        pr.segment_by_script = original


def test_a_unit_number_is_spelled_out_in_both_languages():
    """The "#" has no reading and the voice invents one: "#08-212" came back
    from the recogniser as "neiro eight two one two" — a floor and a unit the
    customer does not live on."""
    from voicebot.spoken import segment_by_script, spoken_address

    assert spoken_address("Jurong West Street 4, #08-212") == \
        "Jurong West Street 4, unit zero eight, two one two"
    for lang in ("en", "zh"):
        spoken = " ".join(f for f, _ in segment_by_script(render(2, P, lang), lang))
        assert "#" not in spoken, f"{lang} still says the hash"
        assert "unit zero eight, two one two" in spoken


def test_the_brand_is_spoken_in_english_on_a_mandarin_call():
    """Etiqa and the product name are what is printed on the policy and on the
    letter in the customer's hand.

    The bare "Tiq" is deliberately not on the list. It renders cleanly alone
    (0.84 s, heard back as "tick"), but between Chinese neighbours the
    recogniser lost it in two lines out of three. Five letters survive a seam;
    three do not — so the Mandarin script says the whole product name instead,
    which is long enough to hold.
    """
    from voicebot.spoken import segment_by_script

    assert ("Etiqa", "en") in segment_by_script(render(1, P, "zh"), "zh")
    assert ("Tiq Personal Accident", "en") in segment_by_script(render(6, P, "zh"), "zh")


@pytest.mark.parametrize("line,spoken", [
    ("Your policy number is TH-4471-0093.",
     "Your policy number is T H four four seven one zero zero nine three."),
    ("We have wm.tan@example.sg on file.",
     "We have w m dot tan at example dot s g on file."),
    ("You can reach us on 6887 8777.",
     "You can reach us on six eight eight seven eight seven seven seven."),
])
def test_english_reads_identifiers_back_the_way_a_person_does(line, spoken):
    """The Mandarin path has done this since the policy number came back as
    different digits from the ones on the policy. English never did, and was
    just as wrong: one zero short on the policy number, "w m two ten at
    example dot x a" for the email, and a callback number nobody can dial."""
    from voicebot.spoken import spoken_identifiers
    assert spoken_identifiers(line) == spoken


@pytest.mark.parametrize("line", [
    "The final premium is 412 dollars for a 5-year plan.",
    "A 23.5 percent discount has been applied.",
    "You're insured for 35,000 on home contents and 60,000 on renovation.",
    "It's due on 10 February twenty twenty-six.",
])
def test_ordinary_numbers_are_left_alone(line):
    """A premium is a quantity and is read as one. Only what someone reads
    back character by character — a code, an address, an email, a number to
    dial — gets spelled."""
    from voicebot.spoken import spoken_identifiers
    assert spoken_identifiers(line) == line


def test_no_mandarin_line_says_the_product_name_alone():
    """A bare "Tiq" between Chinese neighbours went missing in two lines out
    of three. Every Mandarin mention carries the full product name."""
    import re

    from voicebot.call import engine

    lines = [render(t, P, "zh") for t in range(1, 8)]
    lines += [engine.who_we_are("zh", "Michelle"),
              engine.who_we_are_again("zh", "Michelle"),
              engine.PURPOSE["zh"], engine.PURPOSE_AGAIN["zh"]]
    for line in lines:
        for run in re.findall(r"[A-Za-z][A-Za-z ]*", line):
            assert run.strip().lower() != "tiq", f"bare Tiq in {line!r}"


def test_a_unit_number_is_read_as_digits():
    """"Oh eight" is how a phone number is read. A customer checking their
    address against a letter should hear the digit."""
    from voicebot.spoken import spoken_address

    assert spoken_address("Jurong West Street 4, #08-212") == \
        "Jurong West Street 4, unit zero eight, two one two"
