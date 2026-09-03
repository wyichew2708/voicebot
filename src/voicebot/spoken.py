"""Turning written slot values into something a voice can say.

Shared by the script renderer and the fact store, because a date read out in
an answer has to sound the same as the same date read out in a scripted line.
"""
from __future__ import annotations

import re
from pathlib import Path

_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")

_ONES = ("", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen")
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
         "eighty", "ninety")


def _two_digits(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return _TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else "")


def _spoken_year(year: str) -> str:
    """"2026" -> "twenty twenty-six".

    The pre-render model reads a bare four-digit year wrong: rendered from the
    numeral it says "twenty fifty-six", which is a different year from the one
    on the customer's policy. Written out, it reads correctly. Verified by
    transcribing the rendered audio back — the numeral form comes back as the
    wrong year, the spelled form comes back right.
    """
    if not (year.isdigit() and len(year) == 4):
        return year
    hi, lo = int(year[:2]), int(year[2:])
    if lo == 0:
        return f"{_two_digits(hi)} hundred"
    if lo < 10:
        return f"{_two_digits(hi)} oh {_ONES[lo]}"
    return f"{_two_digits(hi)} {_two_digits(lo)}"


def _en_date(value: str) -> str:
    """"10 February 2026" -> "10 February twenty twenty-six"."""
    parts = value.replace(",", " ").split()
    if len(parts) != 3:
        return value
    day, month, year = parts
    if month.lower() not in _MONTHS:
        return value
    return f"{day} {month} {_spoken_year(year)}"




# --- Mandarin numerals ----------------------------------------------------
# The pre-render voice reads Arabic digits inside Chinese text as though they
# were not there: "2026年2月10日" came back from the recogniser as
# "屯溪区棉亭站". Written out in characters it reads correctly. Same class of
# fault as the English year, found the same way — transcribing our own audio.

_ZH_DIGITS = "零一二三四五六七八九"
_ZH_UNITS = ((100_000_000, "亿"), (10_000, "万"), (1_000, "千"),
             (100, "百"), (10, "十"))


def zh_number(n: int, _top: bool = True) -> str:
    """A cardinal, the way it is said rather than the way it is written.

    Follows the conventions a Singaporean listener expects: 十二 not 一十二,
    三万五千 not 三十五千, and a 零 wherever a place is skipped.

    `_top` is the difference between 十二 and 四百一十二: the leading 一 is
    dropped only when 十 opens the number, never inside a larger one.
    """
    if n < 0:
        return "负" + zh_number(-n, _top)
    if n < 10:
        return _ZH_DIGITS[n]
    for value, unit in _ZH_UNITS:
        if n >= value:
            head, rest = divmod(n, value)
            prefix = "" if (_top and value == 10 and head == 1) else zh_number(head, False)
            if rest == 0:
                return prefix + unit
            # A skipped place is spoken as 零: 一百零五, 三万零二十.
            gap = "零" if rest < value // 10 else ""
            return prefix + unit + gap + zh_number(rest, False)
    return _ZH_DIGITS[n]


def zh_decimal(value: str) -> str:
    """"23.5" -> "二十三点五". Digits after the point are read one at a time."""
    whole, _, frac = value.replace(",", "").partition(".")
    if not whole.isdigit():
        return value
    out = zh_number(int(whole))
    if frac.isdigit():
        out += "点" + "".join(_ZH_DIGITS[int(d)] for d in frac)
    return out


def zh_year(year: str) -> str:
    """Years are read digit by digit: 二零二六年, never 两千零二十六年."""
    if not (year.isdigit() and len(year) == 4):
        return year
    return "".join(_ZH_DIGITS[int(d)] for d in year)


def zh_digits(run: str) -> str:
    """Arabic digits inside a Mandarin line, read one at a time.

    A phone number, a unit number or a policy number is a sequence of digits,
    not a quantity: 六八八七 八七七七, never 六千八百八十七.
    """
    return "".join(_ZH_DIGITS[int(ch)] if ch.isdigit() else ch for ch in run)


def zh_inline_numbers(text: str) -> str:
    """Every Arabic digit in a Mandarin line, spoken as Mandarin.

    Quantities are already written out by the script; what reaches here is
    the incidental kind — a unit number, a phone number, a policy number —
    and those are read digit by digit the way anyone reads them aloud. The
    dashes inside a code become spaces, so it is read as groups rather than
    as a subtraction.
    """
    out = re.sub(r"(?<=[0-9A-Za-z])-(?=[0-9])", " ", text)
    return re.sub(r"\d", lambda m: _ZH_DIGITS[int(m.group())], out)


def zh_date(value: str) -> str:
    """"10 February 2026" -> "二零二六年二月十日"."""
    parts = value.replace(",", " ").split()
    if len(parts) != 3:
        return value
    day, month, year = parts
    if month.lower() not in _MONTHS or not day.isdigit():
        return value
    m = _MONTHS.index(month.lower()) + 1
    return f"{zh_year(year)}年{zh_number(m)}月{zh_number(int(day))}日"


# --- mixed scripts --------------------------------------------------------
# A Chinese sentence with a Singapore address, an email or a policy number in
# it is two languages, and one voice cannot read both. Rendered wholly in
# Chinese, "TH-4471-0093" came back from the recogniser as
# "t h 四 four seven one zero 三 three nine three" — different digits from the
# ones on the policy. Rendered wholly in English, the Chinese is nonsense.
# So each run is rendered by the front-end that can read it.

_LATIN_RUN = re.compile(r"[A-Za-z0-9@#][A-Za-z0-9@#._\-+/,' ]*[A-Za-z0-9@#.]|[A-Za-z0-9@#]")


_HAS_SPEECH = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")


#: Brand and product names that stay English inside a Mandarin line. "Etiqa"
#: and "Tiq Home" are what is printed on the policy and on the letter in the
#: customer's hand; a Mandarin rendering is a company and a product they have
#: never heard of.
#:
#: The product is listed as the whole name, never the bare "Tiq". Three
#: letters alone did not survive the seam — the recogniser lost it in two
#: Mandarin lines out of three — where "Tiq Home" and "Tiq Personal Accident"
#: are long enough to hold. The Mandarin script is worded to match.
_ENGLISH_TERMS = ("etiqa", "tiq home", "tiq personal accident")


def _NEEDS_ENGLISH(run: str) -> bool:
    """Whether this run is worth a seam.

    A Mandarin call is spoken in Mandarin, digits included — 六八八七 rather
    than an English voice cutting in to read a phone number. Three things
    earn the seam anyway: an email address, the brand, and the property
    address, because all three are read back against something written down.
    """
    if "@" in run:
        return True
    if run.strip().strip(".,;:!?").lower() in _ENGLISH_TERMS:
        return True
    # Everything else stays in Mandarin. The Chinese voice reads a couple of
    # letters perfectly well — "TH 四四七一" comes back verbatim — and pulling
    # them out made it worse, not better: "T H" alone was too short a
    # fragment and the model filled it with invented speech
    # ("t h money and so trusted 四四七一零零九三").
    letters = sum(1 for ch in run if ch.isalpha() and ch.isascii())
    return letters >= 6                  # a phrase or an address, not a name
_CODE = re.compile(r"^[A-Za-z]{1,4}[-\s]?\d{2,}(?:[-\s]?\d+)*$")
_DIGITS_ONLY = re.compile(r"^[#\d][\d\s\-#]*$")


def spell_identifier(run: str, lang: str = "en") -> str:
    """A policy number or an email address, the way a person reads one out.

    Handed over whole, the voice loses characters and invents others:
    "TH-4471-0093" came back as "t h 四 four seven one zero 三 three nine
    three" — different digits from the ones on the policy, spoken to the
    customer as though they were theirs.
    """
    if "@" in run:
        local, _, domain = run.partition("@")
        said = " dot ".join(_spell_word(w) for w in local.split("."))
        return f"{said} at " + " dot ".join(_spell_word(w) for w in domain.split("."))
    if lang == "zh":
        # Letters stay letters — there is no Mandarin for "T H" — but the
        # digits beside them are Mandarin like everything else in the line.
        return " ".join(zh_digits(ch) if ch.isdigit() else ch
                        for ch in run if ch.isalnum())
    if _CODE.match(run):
        return " ".join(ch for ch in run.upper() if ch.isalnum())
    # A phone number, a unit number, a reference: four or more digits and
    # nothing else to go on. Read as a quantity it becomes something else
    # entirely — "6887 8777" came back as "sixteen eight seven".
    if _DIGITS_ONLY.match(run) and sum(ch.isdigit() for ch in run) >= 4:
        return " ".join(ch for ch in run if ch.isdigit())
    return run


def _spell_word(word: str) -> str:
    """Letter by letter for an initialism, whole for something pronounceable."""
    if len(word) <= 3 and not any(v in word.lower() for v in "aeiou"):
        return " ".join(word)
    return word


def _fragments(run: str, lang: str) -> list[tuple[str, str]]:
    """One latin run, split into the pieces each voice can actually say.

    An email goes to the English voice whole — its letters and dots are one
    thing and cutting it up would read as a stammer. Anything else is split
    into letters and digits, so a Mandarin call says 四四七一 in Mandarin and
    only the "T H" comes from elsewhere.
    """
    if "@" in run:
        return [(spell_identifier(run), "en")]
    if lang != "zh":
        return [(spell_identifier(run), "en")]

    if _CODE.match(run):
        # A policy number is letters plus digits, and the digits belong to the
        # Mandarin voice: "TH 四四七一 零零九三".
        out: list[tuple[str, str]] = []
        for m in re.finditer(r"[A-Za-z]+|\d+", run):
            piece = m.group()
            if piece[0].isdigit():
                out.append((zh_digits(piece), "zh"))
            else:
                out.append((_spell_word(piece) if piece.isupper() else piece, "en"))
        return out or [(run, "en")]

    # An address or the brand: one voice, whole. Split the same way as a code,
    # "Jurong West Street 4, #08-212" comes out as an English street followed
    # by Mandarin numerals — an address in neither language, and not the one
    # the customer would read off their own letter.
    return [(run, "en")]


_UNIT = re.compile(r"#(\d+)\s*-\s*(\d+)")
#: A floor and unit are read as digits: "#08-212" is "zero eight, two one
#: two". "Oh eight" is how a phone number is read, not an address, and a
#: customer checking it against their letter should hear the digit.
_DIGIT_WORD = ("zero", "one", "two", "three", "four", "five",
               "six", "seven", "eight", "nine")


def spoken_address(text: str) -> str:
    """"#08-212" -> "unit zero eight, two one two".

    The "#" of a Singapore unit number has no reading, and the voice invents
    one: "#08-212" came back from the recogniser as "neiro eight two one two"
    — a floor and a unit the customer does not live on. Spelled out it comes
    back exactly. Applies to English and Mandarin alike: the address is the
    one thing on the call read back against a letter in someone's hand.
    """
    def _spell(run: str) -> str:
        return " ".join(_DIGIT_WORD[int(ch)] for ch in run)

    return _UNIT.sub(lambda m: f"unit {_spell(m.group(1))}, {_spell(m.group(2))}",
                     text)


_ZERO_TO_NINE = ("zero", "one", "two", "three", "four", "five",
                 "six", "seven", "eight", "nine")
_EMAIL = re.compile(r"\S+@\S+\.\S+")
_POLICY = re.compile(r"\b[A-Za-z]{1,4}-\d{2,}(?:-\d+)*\b")
_PHONE = re.compile(r"\b\d{4}\s\d{4}\b")


def spoken_identifiers(text: str) -> str:
    """Read a number back the way a person reads one out, in English.

    The Mandarin path has done this since the policy number came back as
    different digits from the ones on the policy. English never did, and it
    is just as wrong there: "TH-4471-0093" was heard back as "t h four four
    seven one zero nine three" — one zero short — "wm.tan@example.sg" as
    "w m two ten at example dot x a", and the callback number "6887 8777" as
    "sixteen eight eight seven eight seven seven seven eight seven seven",
    which is not a number anyone can dial.
    """
    def _digits(run: str) -> str:
        return " ".join(_ZERO_TO_NINE[int(ch)] if ch.isdigit() else ch
                        for ch in run if ch != " ")

    text = _EMAIL.sub(lambda m: spell_identifier(m.group()), text)
    text = _POLICY.sub(
        lambda m: " ".join(_spell_word(part) if part.isalpha() else _digits(part)
                           for part in re.split(r"[-\s]", m.group()) if part),
        text)
    return _PHONE.sub(lambda m: _digits(m.group()), text)


# ----------------------------------------------------------------- names

#: Salutations a surname can follow. Substitution is anchored to one of these
#: so "Tan" only becomes "Dan" where it is a name: the word appears in
#: ordinary text too, and a lexicon that rewrote all of it would be a bug that
#: only ever showed up in audio.
_SALUTATIONS = ("Mr", "Mrs", "Ms", "Mdm", "Madam", "Miss", "Dr")

# ----------------------------------------------------- which name is said
# A call says a salutation and one name: "Mr Chew", never "Mr Chew Yi Feng".
# Reading the whole name out is what a form letter does, not what a person
# does, and on the turn that asks someone to confirm who they are it sounds
# like a list being worked through rather than a call being made.
#
# The record is supposed to carry the surname on its own, and the three demo
# personas do. This is the net under that: an operator typing a full name into
# the console's name box, or a CRM field that turns out to hold "Chew Yi Feng",
# must not reach the voice whole. Getting the wrong token out of a full name is
# no worse than today, which says all of them; saying nothing extra is the
# floor this guarantees.

#: Markers of a patronymic. A name carrying one has no surname at all: the
#: father's name follows the marker and is not what its owner is called.
_PATRONYMIC = frozenset(("bin", "binti", "binte", "bte", "ibni",
                         "s/o", "d/o", "a/l", "a/p"))

#: Religious and honorific elements that open a name without being the name.
#: "Muhammad Farid bin Abdullah" is addressed as Mr Farid, not Mr Muhammad,
#: which would fit a large share of the men on any Singapore call list.
_NAME_PREFIX = frozenset(("muhammad", "muhammed", "mohammad", "mohamed",
                          "mohd", "md", "nur", "nurul", "siti", "abdul", "abd"))

#: Generational suffixes, which are never the name either.
_NAME_SUFFIX = frozenset(("jr", "jnr", "sr", "snr", "ii", "iii", "iv"))

#: Given names that place a name in Western order — given name first, surname
#: last. This is the signal that separates "Andrew Tan" (surname Tan, last)
#: from "Chew Yi Feng" (surname Chew, first): a Singaporean Chinese name
#: written surname-first does not open with one of these.
_WESTERN_GIVEN = frozenset("""
adam adrian agnes alan albert alex alexander alfred alice alicia alison allan
amanda amelia amy andre andrea andrew angela angeline ann anna anne annie
anthony april arthur ashley audrey barbara benjamin bernard bernice beatrice
betty brandon brenda brian bryan caleb calvin carol caroline catherine cecilia
celine charles charlotte cheryl chloe chris christina christine christopher
claire clara clarence clement colin connie constance cynthia daniel danny
daphne darren david dawn deborah dennis derek derrick desmond diana dominic
donald donna doreen dorothy douglas edward edwin eileen elaine eleanor elizabeth
ellen elsie emily emma enoch eric erica ernest esther eugene eunice evelyn
faith felicia felix fiona florence frances francis frank gabriel gary gavin
genevieve geoffrey george gerald geraldine gilbert gladys glenn gloria grace
graham gregory hannah harold harry hazel heather helen henry herbert hilda
hubert hugh ian irene iris isaac isabel ivan ivy jacob jacqueline james jane
janet janice jared jason jasmine jasper jean jeffrey jennifer jenny jeremy
jerome jerry jessica jessie jimmy joan joanna joanne joel john johnny jonathan
jordan joseph josephine joshua joy joyce juan judith judy julia julian julie
juliet justin karen katherine kathleen kathryn keith kelly kenneth kevin kimberly
kirsten laura lauren lawrence leonard leslie lester lewis lillian lily linda
lionel lisa lorraine louis louise lucas lucy luke lydia lynn madeleine malcolm
marcus margaret maria marian marie marilyn marion mark martha martin mary
matthew maureen maurice maxwell megan melanie melissa melvin mercy michael
michelle mildred millie miriam moses nancy naomi natalie nathan neil nelson
nicholas nicole noel norman olivia oscar owen pamela patricia patrick paul
paula pauline pearl peggy peter philip phillip phoebe priscilla rachel ralph
raymond rebecca regina reginald rex richard rita robert roberta robin roger
roland ronald rosalind rose rosemary roy ruby russell ruth ryan sally samantha
samuel sandra sarah scott sean selena serene shane sharon sheila shirley simon
sophia sophie stanley stella stephanie stephen steven stuart susan susanna
suzanne sylvia tanya terence teresa terry theodore theresa thomas timothy tina
tobias tommy tracy travis trevor valerie vanessa vera veronica victor victoria
vincent violet virginia vivian vivien walter wayne wendy wesley william wilson
winnie yvonne zachary zoe
""".split())

#: Romanised Chinese surnames, used only to break a two-token tie: "Xiaoli
#: Tan" is not Western order, and without this the first token would be read
#: as the surname. Deliberately not consulted for longer names, where a known
#: surname sitting last is far more often a given name that happens to double
#: as one — "Chuan Ping Fong" is addressed as Madam Chuan, though Fong is a
#: surname too.
_CHINESE_SURNAME = frozenset("""
ang aw bek boey chai chan chang chay chea chee chen cheng cheong cheung chew
chia chiang chin ching chng cho choo chong chow choy chu chua chuan chum chung
er eng fan fang foo fong fu gan gay geh giam goh gan guan guo ha han heng ho
hoe hon hong hoo hsu hu hua huang hui hung ir jiang jong kam kang kee keng khaw
kho khoo khor koh kok kong koo ku kuah kuek kum kwa kwan kwek kwok lai lam lau
law lay lee leong leow li liang liew lim lin ling liu lo loh loke long loo low
lu luo ma mah mak mao mok moy mun na nah nam nee neo ng ngo oh ong ooi ou pan
pang pau peh pek peng phang pheng phua phun poh pua puah quah quek quek ren
see seah seet seow seng sha shen shi sia siah siew sim sin sng so soh song soo
su sui sun sung sze ta tai tam tan tang tay teh tek teng teo teoh thai tham
thia thio thong thum tian tin ting toh tong tsai tse tu wan wang wee wei wen
weng wong woo wu xie xu xiong yam yan yang yao yap ye yee yeo yeoh yeung yew
yim yip yong yoong yow yu yuan yuen zeng zhang zhao zheng zhong zhou zhu
""".split())


def _name_tokens(name: str) -> list[str]:
    """The name's own words, without salutation, prefixes or suffixes."""
    parts = [p for p in re.split(r"[\s,]+", (name or "").strip()) if p]
    lower = {s.lower() for s in _SALUTATIONS}
    while parts and parts[0].lower().rstrip(".") in lower:
        parts.pop(0)
    while parts and parts[-1].lower().rstrip(".") in _NAME_SUFFIX:
        parts.pop()
    return parts


def surname_of(name: str) -> str:
    """The single name a call says after the salutation.

    "Chew Yi Feng" -> "Chew"; "Andrew Tan Wei Ming" -> "Tan"; "Mr Tan" ->
    "Tan"; and a name that is already one word is returned unchanged, which
    is what keeps every record and every cached line exactly as it was.
    """
    tokens = _name_tokens(name)
    if not tokens:
        return ""
    if len(tokens) == 1:
        return tokens[0]

    # A patronymic names the father, not its owner. Everything before the
    # marker is the person's own name; the last word of it is what they are
    # called, once the religious openers are set aside.
    for i, token in enumerate(tokens):
        if token.lower().strip(".") in _PATRONYMIC:
            own = tokens[:i] or tokens[i + 1:]
            kept = [t for t in own if t.lower() not in _NAME_PREFIX]
            return (kept or own)[-1] if (kept or own) else ""

    # Western order: given name first, so the surname is last — except where a
    # Chinese given name follows it, and "Andrew Tan Wei Ming" is Mr Tan.
    if tokens[0].lower() in _WESTERN_GIVEN:
        return tokens[1] if len(tokens) >= 3 else tokens[-1]

    # Two words, neither of them Western: prefer whichever is a surname.
    if (len(tokens) == 2 and tokens[0].lower() not in _CHINESE_SURNAME
            and tokens[1].lower() in _CHINESE_SURNAME):
        return tokens[1]

    # Otherwise the name is written surname-first, as most Singaporean Chinese
    # and Malay-language names on a policy record are.
    return tokens[0]

#: Anchored to the package, not to the working directory. Located the same way
#: config.py and server.py locate theirs, and for the same reason: a relative
#: path resolves against wherever the process happens to have been started —
#: a systemd unit with no WorkingDirectory, a container entered at /, a test
#: run from elsewhere. The failure is silent, because a lexicon that cannot be
#: read is indistinguishable from an empty one and every name simply keeps its
#: own spelling.
_NAMES_PATH = Path(__file__).resolve().parents[2] / "voices" / "names.yaml"

_NAMES_CACHE: "dict[str, dict] | None" = None


def _names() -> "dict[str, dict]":
    """The pronunciation lexicon, or an empty one.

    Missing or malformed is not fatal: every name simply keeps its own
    spelling, which is what happened before the file existed.
    """
    global _NAMES_CACHE
    if _NAMES_CACHE is None:
        try:
            import yaml
            raw = yaml.safe_load(_NAMES_PATH.read_text()) or {}
            _NAMES_CACHE = {str(k): (v or {}) for k, v in
                            (raw.get("names") or {}).items()}
        except Exception:
            _NAMES_CACHE = {}
    return _NAMES_CACHE


def reload_names() -> None:
    """Pick up an edited lexicon without a restart."""
    global _NAMES_CACHE
    _NAMES_CACHE = None


def sayable(surname: str) -> bool:
    """Whether the voice can say this name acceptably at all.

    A name listed with no spelling is one no respelling fixed. Saying it
    anyway mispronounces someone at the exact moment the call asks them to
    trust it, so the script addresses them without it instead.
    """
    entry = _names().get((surname or "").strip())
    return not (entry is not None and entry.get("say") is None)


def spoken_names(text: str) -> str:
    """Respell surnames so the synthesiser says them correctly.

    Audio only. The transcript, the record and everything an operator reads
    keep the customer's own spelling — this exists solely because the English
    letter-to-sound rules in the voice were not trained on Hokkien, Teochew
    and Cantonese romanisations, and "Tan" comes out rhyming with "tang".
    """
    names = _names()
    if not names:
        return text
    for surname, entry in names.items():
        say = entry.get("say")
        if not say or say == surname:
            continue
        text = re.sub(rf"\b({'|'.join(_SALUTATIONS)})\.?\s+{re.escape(surname)}\b",
                      rf"\1 {say}", text)
    return text


def segment_by_script(text: str, lang: str) -> list[tuple[str, str]]:
    """[(fragment, language)] for a line that mixes scripts.

    Only splits for a non-latin target language. A Mandarin call is spoken in
    Mandarin — digits included, because 六八八七 is what a Mandarin speaker
    says and an English voice cutting in to read a phone number is not. The
    exception is letters, which have no Mandarin reading: an email address
    and a policy prefix go to the English voice and nothing else does.
    """
    if not text:
        return [(text, lang)]
    text = spoken_names(spoken_address(text))
    if lang == "en":
        return [(spoken_identifiers(text), lang)]
    out: list[tuple[str, str]] = []
    last = 0
    for m in _LATIN_RUN.finditer(text):
        run = m.group().strip()
        # The Chinese front-end reads a short name perfectly well — "Tan先生"
        # comes back as 谭先生 — and every seam is a join the ear can hear:
        # pulled out on its own, "Dave" rendered as "dave dave concentration".
        if len(run) < 2 or not _NEEDS_ENGLISH(run):
            continue
        head = text[last:m.start()]
        if _HAS_SPEECH.search(head):
            out.append((head, lang))
        out.extend(_fragments(run, lang))
        last = m.end()
    tail = text[last:]
    if _HAS_SPEECH.search(tail):
        out.append((tail, lang))
    elif out and tail.strip():
        # Punctuation with nothing to say. Rendered on its own the model
        # invents a sentence to fill it — a trailing "。" produced several
        # seconds of speech that was never in the script.
        out[-1] = (out[-1][0] + tail, out[-1][1])
    if not out:
        out = [(text, lang)]
    # Any digit left on the Mandarin side is spoken as Mandarin.
    out = [(zh_inline_numbers(frag) if flang == "zh" else frag, flang)
           for frag, flang in out]
    # Neighbours in the same language are one fragment: "四四七一" and
    # "零零九三" are one number, and a seam between them is a stumble.
    merged: list[tuple[str, str]] = []
    for frag, flang in out:
        if merged and merged[-1][1] == flang:
            merged[-1] = (merged[-1][0] + frag, flang)
        else:
            merged.append((frag, flang))
    return merged


# --------------------------------------------------------------- chunking

#: How much speech a second buys, measured off the pre-render voice: "Am I
#: speaking with Mr Tan?" is six words in 1.9 s, and 请问是陈先生本人吗？ is
#: ten characters in 2.3 s. Only ever used to decide where to cut, so being a
#: little wrong costs a slightly early or late chunk boundary, nothing more.
_WORDS_PER_SEC = 3.2
_CHARS_PER_SEC = 4.3

#: Sentence ends. Latin punctuation must be followed by whitespace, which is
#: what keeps `a.tan@example.sg` and `1,234.56` in one piece without having to
#: mask them first — an address has no space after its dots. CJK punctuation
#: needs no such guard because nothing else uses those characters. The
#: lookbehinds stop an abbreviation ending a sentence; "Mr. Tan" is the one
#: that would otherwise strand a salutation on its own.
_SENTENCE_END = re.compile(
    r"(?<!\bMr)(?<!\bMrs)(?<!\bMs)(?<!\bMdm)(?<!\bDr)(?<!\bNo)(?<!\b[A-Z])"
    r"(?<=[.!?;])\s+"
    r"|(?<=[。！？；])")

#: Clause ends, used only to find a shorter opening chunk inside a long first
#: sentence. Commas are a worse place to breathe than a full stop, so they are
#: a fallback and not a first choice.
_CLAUSE_END = re.compile(r"(?<=[,;:])\s+|(?<=[，、：])")

#: A chunk shorter than this is merged into its neighbour. Synthesis costs a
#: fixed ~0.4 s whatever the length, so a half-second fragment is most of a
#: second of work for almost no audio, and the voice audibly restarts on it.
_MIN_CHUNK_SEC = 0.8


def speech_seconds(text: str, lang: str) -> float:
    """Roughly how long this text takes to say, in seconds."""
    if lang == "zh":
        han = sum(1 for c in text if "一" <= c <= "鿿")
        if han:
            # Latin runs inside a Mandarin line are read letter by letter and
            # take much longer per character than Han does; count them as
            # words so an email does not look like a handful of syllables.
            words = len(re.findall(r"[A-Za-z0-9@._\-]+", text))
            return han / _CHARS_PER_SEC + words / _WORDS_PER_SEC
    return max(1, len(text.split())) / _WORDS_PER_SEC


def _split_keep(pattern: "re.Pattern[str]", text: str) -> list[str]:
    out, last = [], 0
    for m in pattern.finditer(text):
        piece = text[last:m.end()].strip()
        if piece:
            out.append(piece)
        last = m.end()
    tail = text[last:].strip()
    if tail:
        out.append(tail)
    return out or ([text.strip()] if text.strip() else [])


def speech_chunks(text: str, lang: str, first_seconds: float = 2.2,
                  chunk_seconds: float = 3.5) -> list[str]:
    """Break one utterance into pieces that can be synthesised in order.

    The point is time-to-first-audio. Synthesis runs at roughly 0.8x real time
    on this machine but returns nothing until it finishes, so a six-second
    line is four seconds of silence before a word is heard. Cut the same line
    into three and the first is heard in about a second, while the rest are
    still being made — and because generation outruns playback, they arrive
    before the player needs them.

    So the first chunk is deliberately short and later ones are not: the
    opening buys the latency, and longer chunks after it are both more
    efficient per second of audio and better prosody. Splitting is at
    sentence boundaries wherever possible, because that is where a listener
    expects a breath, and every seam is a join the ear can hear.
    """
    text = (text or "").strip()
    if not text:
        return []
    parts = _split_keep(_SENTENCE_END, text)

    # A long opening sentence is the common case for turn 1, and leaving it
    # whole would give back the latency this is here to save. Cut at a clause
    # boundary instead, but only if that actually yields a shorter opener.
    if parts and speech_seconds(parts[0], lang) > first_seconds * 1.6:
        clauses = _split_keep(_CLAUSE_END, parts[0])
        if len(clauses) > 1:
            head, rest = clauses[0], ("" if lang == "zh" else " ").join(clauses[1:])
            if speech_seconds(head, lang) >= _MIN_CHUNK_SEC:
                parts = [head, rest] + parts[1:]

    # Mandarin sentences butt up against each other; a space inserted at the
    # join is a pause the writer did not ask for.
    glue = "" if lang == "zh" else " "

    chunks: list[str] = []
    for part in parts:
        # The budget belongs to the chunk being extended, not to the count of
        # chunks so far: while chunks[-1] is still the opener it stays short.
        budget = first_seconds if len(chunks) <= 1 else chunk_seconds
        if chunks and speech_seconds(chunks[-1], lang) < budget:
            joined = f"{chunks[-1]}{glue}{part}"
            # No slack over the budget. A chunk allowed to overrun is one the
            # chunk before it cannot pay for: the caller hears the opening,
            # then a hole while the long tail is still being synthesised.
            if speech_seconds(joined, lang) <= budget:
                chunks[-1] = joined
                continue
        chunks.append(part)

    # Fold away anything too short to be worth its own synthesis call. Done
    # last so it catches both a stray fragment at the end and a one-word
    # sentence in the middle.
    merged: list[str] = []
    for chunk in chunks:
        if merged and speech_seconds(chunk, lang) < _MIN_CHUNK_SEC:
            merged[-1] = f"{merged[-1]}{glue}{chunk}"
        else:
            merged.append(chunk)
    while len(merged) > 1 and speech_seconds(merged[0], lang) < _MIN_CHUNK_SEC:
        merged[:2] = [f"{merged[0]}{glue}{merged[1]}"]
    return merged
