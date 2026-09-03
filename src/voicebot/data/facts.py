"""Versioned product fact store.

Every figure the bot may speak lives here. The LLM is never permitted to
generate a premium, a discount or a benefit limit: a wrong number is a
misrepresentation of an insurance product, not a typo.

TODO(before any real use): repopulate from Etiqa's own policy wording and
current rate card. The values below are illustrative placeholders.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..spoken import _en_date, zh_date, zh_decimal, zh_number

FACTS_VERSION = "2026-08-31-placeholder"


@dataclass(frozen=True)
class ProductFact:
    key: str
    en: str
    zh: str


# Cross-sell copy. Fixed wording — pre-rendered to audio at build time.
CROSS_SELL = {
    "product": ProductFact("product", "Tiq Personal Accident", "Tiq Personal Accident 保险"),
    "discount": ProductFact("discount", "40 percent", "百分之四十"),
    "from_price": ProductFact("from_price", "150 dollars a year", "一年一百五十新币"),
    "monthly": ProductFact("monthly", "around 12 dollars 50 a month", "每个月大约十二块五"),
    "inpatient": ProductFact("inpatient", "up to 2,000 dollars of inpatient medical expenses",
                             "最高两千新币的住院医疗费用"),
}

# Coverage answers used to live here as a two-entry table with no source
# behind either entry. They now live in the OKF bundle under
# knowledge/wiki/product/general/home/, where they are marked draft and
# unsourced, and where a deployment can refuse to speak them. Keeping a copy
# here as well would be a second source of truth, which is the failure the
# bundle exists to prevent.
#
# See coverage_lookup() below and docs/knowledge-layer.md.

# Questions that are advice, not fact. These escalate to a licensed human.
ADVICE_TRIGGERS = (
    "should i", "do i need", "is it enough", "recommend", "better plan",
    "worth it", "advise", "advice", "increase my cover", "which plan",
    "值得吗", "建议", "该不该", "够不够", "推荐",
)


def cross_sell_line(lang: str) -> str:
    f = CROSS_SELL
    if lang == "zh":
        return (f"我们现在有一个促销活动：{f['product'].zh}，"
                f"{f['discount'].zh}折扣，{f['from_price'].zh}起，"
                f"{f['monthly'].zh}，包括新冠肺炎保障和{f['inpatient'].zh}，也包括骨痛热症。")
    return (f"there's an ongoing promotion on {f['product'].en} at {f['discount'].en} off, "
            f"from {f['from_price'].en}, {f['monthly'].en}, which includes COVID-19 coverage "
            f"and {f['inpatient'].en} including dengue fever.")


# "How should I proceed?" is not a request for financial advice — it is a
# customer asking what to do next, and turn 5 of the script is the answer.
# Escalating it to a licensed adviser derails a servicing call over a phrase
# match on "should i".
PROCEDURE_TRIGGERS = (
    "how should i proceed", "how do i proceed", "how to proceed", "what next",
    "what's next", "whats next", "what do i do", "what should i do next",
    "how do i renew", "how to renew", "how do i pay", "how to pay",
    "where do i pay", "how does it work", "what happens next",
    "怎么续保", "怎麼續保", "怎么付款", "怎麼付款", "接下来", "接下來", "下一步",
    "怎么办", "怎麼辦", "要做什么", "要做什麼", "该做什么", "該做什麼",  # "哦将我要做什么咧"
)

RENEWAL_PROCESS = {
    "en": ("I'll send the renewal to your email — look through it, renew before "
           "the due date, and reply once payment is made."),
    "zh": "我会把续保资料发到您的电邮。您看过之后，在到期日前续保，付款后回复一下就可以了。",
}


def is_procedure_request(text: str) -> bool:
    low = text.lower()
    return any(t in low for t in PROCEDURE_TRIGGERS)


def is_advice_request(text: str) -> bool:
    low = text.lower()
    if is_procedure_request(low):
        return False
    return any(t in low for t in ADVICE_TRIGGERS)


# The caller correcting their email is the commonest off-script turn on a
# renewal call — turn 4 literally invites it — and ignoring it reads as the
# bot not listening.
# Matched on stems, not on whole phrases. "I changed my email address" and
# "may I change my email address?" both went unheard against a fixed phrase
# list, and the caller had to ask three times before anything happened.
_EMAIL_WORDS = ("email", "e-mail", "mail address", "电邮", "邮箱", "郵箱", "電郵")
_CHANGE_WORDS = ("chang", "updat", "amend", "correct", "fix", "new", "differ",
                 "wrong", "not my", "another", "other", "switch", "换", "改",
                 "更新", "不是", "不对")

EMAIL_CHANGE_TRIGGERS = ("change it to", "use my other", "send it to")

_FILLER_WORDS = ("uh", "um", "erm", "ah", "eh", "oh", "sorry", "lah", "leh", "lor")


def _stem(low: str, stem: str) -> bool:
    """Stem match anchored at a word start. Plain containment made "renew"
    match the stem "new", so "please renew by the due date" read as a request
    to change the email address."""
    if not stem.isascii():
        return stem in low
    return re.search(rf"(?<![a-z]){re.escape(stem)}[a-z]*", low) is not None


def wants_email_change(text: str, after_confirm: bool = False) -> bool:
    """`after_confirm` is set only when we have just read an address back.

    A bare "no" means "that address is wrong" in reply to the confirmation
    question, and means nothing of the sort anywhere else in the call.
    """
    low = text.lower()
    if any(t in low for t in EMAIL_CHANGE_TRIGGERS):
        return True
    # Both ideas have to be present, so "look through the email" is not a
    # request to change one.
    if (any(_stem(low, w) for w in _EMAIL_WORDS)
            and any(_stem(low, c) for c in _CHANGE_WORDS)):
        return True
    if not after_confirm:
        return False
    # A rejection of the read-back, with or without the filler people wrap
    # around it: "uh, no." is the same answer as "no".
    words = [w for w in re.findall(r"[a-z']+|[\u4e00-\u9fff]+", low)
             if w not in _FILLER_WORDS]
    return " ".join(words) in ("no", "nope", "wrong", "that's wrong", "thats wrong",
                               "no lah", "not correct", "incorrect", "不对", "不是")


#: Details we hold about the customer rather than about the cover.
_PROFILE_WORDS = _EMAIL_WORDS + (
    "address", "phone", "mobile", "handphone", "contact number", "postal",
    "住址", "地址", "电话", "電話", "手机", "手機", "联络", "聯絡")

#: The cover itself. Changing any of it is underwriting, not servicing.
_POLICY_WORDS = ("sum insured", "coverage", "cover", "policy", "plan",
                 "保额", "保額", "保单", "保單")

#: Deliberately narrower than _CHANGE_WORDS, which includes "correct" and
#: "fix". "The address is correct" is a customer confirming turn 2, not asking
#: us to change anything, and routing that to customer care would end the call
#: for a caller who was agreeing with us.
_RECORD_CHANGE_WORDS = ("chang", "updat", "amend", "new", "differ", "switch",
                        "wrong", "not my", "mov", "换", "改", "更新", "不对")

#: Verbs that act on the cover without naming a change: "cancel my policy",
#: "add my wife", "increase the sum insured".
_POLICY_CHANGE_WORDS = _RECORD_CHANGE_WORDS + (
    "increas", "decreas", "reduc", "rais", "lower", "add", "remov", "cancel",
    "terminat", "upgrad", "downgrad", "增加", "减少", "減少", "取消", "退保")


def wants_record_change(text: str) -> str | None:
    """"data" for a detail we hold about the customer, "policy" for the cover
    itself, None for anything else.

    Both are customer-care work and neither is safe to take down over a voice
    line. An address misheard by one character is a renewal notice that never
    arrives; a cover change recorded wrong is a claim that does not pay. On a
    recorded call the bot spent four turns trying to capture a dictated email
    and wrote yi@hotmail.com for "w y i a" — which is why the bot's job here is
    to route, and not to record.
    """
    low = text.lower()
    # Advice outranks a change request, and the two overlap badly: "increase my
    # cover" is in ADVICE_TRIGGERS and also reads as an instruction to change
    # the cover. Whether to increase it is a licensed adviser's call under the
    # Financial Advisers Act, so that path wins — the same order the handoff
    # PRIORITY table already uses.
    if is_advice_request(low):
        return None
    if wants_email_change(low):
        return "data"
    if (any(_stem(low, w) for w in _PROFILE_WORDS)
            and any(_stem(low, c) for c in _RECORD_CHANGE_WORDS)):
        return "data"
    if (any(_stem(low, w) for w in _POLICY_WORDS)
            and any(_stem(low, c) for c in _POLICY_CHANGE_WORDS)):
        return "policy"
    return None


def spoken_email(text: str) -> str | None:
    """Best-effort recovery of a dictated address; None when unsure.

    ASR renders addresses as "w m dot tan at example dot s g", so the spoken
    separators fold back first. Returning None is a perfectly good outcome —
    a misheard address on a renewal call means the customer never receives
    their notice, so the caller confirms it or an adviser takes it.
    """
    import re

    # A sentence stop is not part of the address. "…dot com." parsed all the
    # way to "wyichew@hotmail.com." and then failed its own validation.
    stripped = re.sub(r"[.?!,;]+\s*$", "", text.lower().strip())
    low = f" {stripped} "
    # "at" is what the recogniser usually writes for "@", but not always:
    # "alias" is what it made of it in one recorded call. The long tail of
    # these is the model's job, in `call/dictation.py`; this list is only the
    # ones seen often enough to be worth a millisecond.
    low = re.sub(r"\s+(?:at|@|alias|at the rate|attherate)\s+", " @ ", low)
    low = re.sub(r"\s+(?:dot|point|period)\s+", " . ", low)
    for word, sym in ((" underscore ", " _ "), (" dash ", " - "), (" hyphen ", " - ")):
        low = low.replace(word, sym)
    low = re.sub(r"[,;]", " ", low)

    # Already joined, e.g. "please use wm.tan@example.sg instead"
    m = re.search(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", low)
    if m:
        return m.group(0)

    tokens = low.split()
    if "@" not in tokens:
        return None
    at = tokens.index("@")

    PUNCT = {".", "_", "-"}
    PART = re.compile(r"[a-z0-9._%+\-]+")

    def take_left() -> list[str]:
        """Walk back from the @, accepting spelled letters and punctuation.
        A whole word is allowed when it starts the address or follows a
        separator — "weiming _ tan" is three tokens of one local part, while
        "one is jimmy" is a carrier phrase and must stop the walk."""
        out, prev_punct = [], True
        for tok in reversed(tokens[:at]):
            if len(tok) == 1 or tok in PUNCT:
                out.append(tok)
                prev_punct = tok in PUNCT
            elif PART.fullmatch(tok) and (not out or prev_punct):
                out.append(tok)
                prev_punct = False
            else:
                break
        return list(reversed(out))

    def take_right() -> list[str]:
        out, seen_dot = [], False
        for tok in tokens[at + 1:]:
            if tok in PUNCT:
                out.append(tok)
                seen_dot = seen_dot or tok == "."
            elif PART.fullmatch(tok):
                out.append(tok)
                # Stop once a TLD has landed, so trailing words like "instead"
                # do not get pulled into the domain.
                if seen_dot and len(tok) >= 2:
                    break
            else:
                break
        return out

    candidate = "".join(take_left()) + "@" + "".join(take_right())
    return candidate if re.fullmatch(
        r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", candidate) else None


def coverage_lookup(text: str, lang: str, serving=None):
    """A coverage answer from the knowledge bundle, or None.

    Deterministic throughout: a frontmatter filter and an alias match, both
    ordinary code. The bundle returns pre-approved wording verbatim or nothing
    at all, so this cannot invent a benefit any more than the old table could.

    What it adds over the table it replaced is provenance. Every answer names
    the page and the source document behind it, a Singapore call can never be
    answered from a Malaysian document, and a deployment can refuse wording
    that has no source at all -- which today is all of the home-insurance
    wording, because no home policy document has been ingested.
    """
    from ..knowledge import lookup
    from ..knowledge.policy import default_serving

    s = serving or default_serving()
    try:
        return lookup(text, lang, jurisdiction=s.jurisdiction,
                      allow_unsourced=s.allow_unsourced, products=s.products)
    except Exception:                                    # pragma: no cover
        # A malformed bundle must not take a live call down with it. Losing
        # coverage answers costs a callback; raising here drops the call.
        import logging
        logging.getLogger(__name__).exception("knowledge lookup failed")
        return None


def coverage_answer(text: str, lang: str, serving=None) -> str | None:
    found = coverage_lookup(text, lang, serving)
    return found.text if found is not None else None


# --- questions about the caller's own record ------------------------------
# A servicing call invites these: the customer is being read details of their
# own policy and asks about one of them. Answering from the record is the
# whole point of the call — the engine used to march on to the next scripted
# line instead, which is what "it doesn't answer my reply" means in practice.
#
# Never generated: every one of these is a figure or a fact from the policy,
# and a wrong one is a misrepresentation, not a typo.

_ASK = ("what", "where", "which", "when", "how", "who", "confirm", "remind",
        "tell me", "say", "repeat", "again", "?",
        "什么", "什麼", "哪", "多少", "几", "幾", "吗", "嗎", "怎么", "怎麼")

# First match wins, so the loosest words go last. "May I change my email
# address?" was answered with the *property* address, because "address" sat at
# the top of this table and matched the wrong half of the question.
_TOPICS: dict[str, tuple[str, ...]] = {
    "email":    ("email", "e-mail", "mail address", "电邮", "電郵", "邮箱", "郵箱"),
    "address":  ("property address", "home address", "property", "house", "unit",
                 "flat", "地址", "房子", "单位", "單位", "address"),
    "due":      ("due date", "due", "expiry", "expire", "when is it", "deadline",
                 "到期", "期限"),
    "policy":   ("policy number", "policy no", "reference number", "保单号", "保單號"),
    "insured":  ("sum insured", "sums insured", "how much am i covered",
                 "how much are we covered", "covered for", "coverage amount",
                 "insured for", "保额", "保額"),
    "term":     ("how many years", "term", "how long", "几年", "幾年"),
    # Last: "how much" is the loosest phrase here and would otherwise swallow
    # "how much am I covered for", which is a question about the sum insured.
    "premium":  ("premium", "how much", "price", "cost", "pay", "amount",
                 "保费", "保費", "多少钱", "多少錢"),
}


def _asks(text: str) -> bool:
    low = text.lower()
    return any(a in low for a in _ASK)


def policy_topic(text: str) -> str | None:
    """Which field of their own policy the caller is asking about, if any.

    Requires an interrogative as well as the topic word: turn 4 ends with "can
    I confirm that's correct?", and "yes the email is right" must stay an
    answer to that question rather than becoming a new one.
    """
    if not _asks(text):
        return None
    low = text.lower()
    if any(w in low for w in ("yes", "correct", "right", "that's it", "thats it")) \
            and "?" not in text and not any(
                low.strip().startswith(q) for q in ("what", "where", "which", "when",
                                                    "how", "who")):
        return None
    for topic, words in _TOPICS.items():
        if any(w in low for w in words):
            return topic
    return None


# "Can I get a discount?" is the most predictable question on a renewal call
# and the bot had no answer for it at all — it asked the caller to repeat
# themselves three times and then handed the call over as a line-quality
# problem. It is not advice under the Financial Advisers Act (it is about
# price, not suitability), so it is answered here rather than escalated as
# one; what it is *not* is something the bot may negotiate.
PRICE_TRIGGERS = (
    "discount", "cheaper", "cheap", "reduce", "reduction", "lower the",
    "lower price", "too expensive", "expensive", "any promo", "promotion for me",
    "better price", "best price", "bring it down", "come down", "negotiate",
    "waive", "any rebate", "rebate", "折扣", "便宜", "太贵", "太貴", "优惠", "優惠",
    "减一点", "減一點", "降价", "降價",
)


def is_price_request(text: str) -> bool:
    """True when the caller is asking for a lower premium."""
    low = text.lower()
    return any(w in low for w in PRICE_TRIGGERS)


def price_answer(p, lang: str) -> str:
    """What is already applied, and the honest limit of what this call can do.

    States the discount from the record — never a figure the model made up —
    and then offers the one thing that could actually change the price: a
    colleague who is allowed to look at it.
    """
    if lang == "zh":
        return (f"这次续保已经给您打了百分之{zh_decimal(p.discount_pct)}的折扣，"
                f"包含在{zh_decimal(p.premium)}新币里面了。"
                f"保费我这边没办法调整，不过我可以请同事帮您看看还有没有其他优惠。"
                f"需要我帮您安排吗？")
    return (f"There's already a {p.discount_pct} percent discount applied to this "
            f"renewal — that's included in the {p.premium} dollars. I'm not able to "
            f"change the price myself, but I can have a colleague check whether "
            f"anything further applies to your policy. Would you like me to?")


def policy_answers(p, lang: str) -> dict[str, str]:
    """Every answer we could give about one policy, so the pre-render pass can
    warm them. They are spoken by the same model as the script — a call that
    changes voice halfway through is the first thing a caller notices."""
    en = {
        "address": f"The property on your policy is {p.property_address}.",
        "due":     f"It's due on {_en_date(p.due_date)}.",
        "premium": f"The premium is {p.premium} dollars for the {p.term_years}-year plan.",
        "email":   f"We have {p.email} on file.",
        "policy":  f"Your policy number is {p.policy_id}.",
        "insured": (f"You're insured for {p.contents_si} on home contents and "
                    f"{p.reno_si} on renovation."),
        "term":    f"It's a {p.term_years}-year plan.",
    }
    zh = {
        "address": f"您保单上的房产地址是 {p.property_address}。",
        "due":     f"到期日是{zh_date(p.due_date)}。",
        "premium": f"保费是{zh_decimal(p.premium)}新币，{zh_number(p.term_years)}年配套。",
        "email":   f"我们记录的电邮是 {p.email}。",
        "policy":  f"您的保单号码是 {p.policy_id}。",
        "insured": (f"家庭财物保额是{zh_decimal(p.contents_si)}元，"
                    f"装修保额是{zh_decimal(p.reno_si)}元。"),
        "term":    f"这是{zh_number(p.term_years)}年的配套。",
    }
    return zh if lang == "zh" else en


def policy_answer(text: str, p, lang: str) -> tuple[str, str] | None:
    """(spoken answer, crm tool arg) for a question about the caller's record."""
    topic = policy_topic(text)
    if topic is None:
        return None
    return policy_answers(p, lang)[topic], f"policy.{topic}"
