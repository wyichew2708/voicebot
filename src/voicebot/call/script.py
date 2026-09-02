"""The Tiq Home renewal script, decomposed into seven turns.

Six of the seven are fixed wording with slots, which is why most of a call
costs no inference at all: those lines are rendered to audio at build time
and played from disk. Only customer questions run the full pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..data.facts import CROSS_SELL, cross_sell_line
from ..data.personas import Policy
from ..spoken import _en_date, zh_date, zh_decimal, zh_number

TurnKind = Literal["static", "template", "generated"]


@dataclass(frozen=True)
class Turn:
    n: int
    name: str
    kind: TurnKind
    # True when this turn discloses personal data and therefore must not be
    # spoken before right-party verification passes.
    discloses_pii: bool = False
    # True when this turn is marketing rather than servicing.
    is_marketing: bool = False


TURNS: tuple[Turn, ...] = (
    Turn(1, "Greeting + right-party check", "template"),
    Turn(2, "Servicing purpose + property", "template", discloses_pii=True),
    Turn(3, "Due date + renewal notice", "template", discloses_pii=True),
    Turn(4, "Premium, sums insured, email", "template", discloses_pii=True),
    Turn(5, "Call to action", "static"),
    Turn(6, "Cross-sell — Tiq PA", "static", is_marketing=True),
    Turn(7, "Close", "static"),
)


_ZH_SALUTATION = {"Mr": "先生", "Ms": "女士", "Mrs": "女士", "Mdm": "女士"}

_MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
           "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
           "december": 12}


def _zh_date(value: str) -> str:
    """"10 February 2026" -> "二零二六年二月十日".

    Dates are slot values, so they must be localised with the rest of the turn —
    an English month name inside a Mandarin sentence is the kind of detail a
    customer notices immediately. Spelled in characters rather than digits,
    because the pre-render voice reads Arabic numerals inside Chinese text as
    though they were not there: "2026年2月10日" came back from the recogniser
    as "屯溪区棉亭站".
    """
    return zh_date(value)


def _zh_address(p: Policy) -> str:
    """Chinese honorific follows the surname: 陈先生, not 陈先生女士."""
    return f"{p.surname}{_ZH_SALUTATION.get(p.salutation, '先生')}"


def _greeting(lang: str, part_of_day: str) -> str:
    if lang == "zh":
        return {"morning": "早上好", "afternoon": "下午好", "evening": "晚上好"}[part_of_day]
    return {"morning": "Good morning", "afternoon": "Good afternoon",
            "evening": "Good evening"}[part_of_day]


def render(turn: int, p: Policy, lang: str, part_of_day: str = "afternoon",
           agent_name: str = "Dave", register: str = "standard") -> str:
    """Render one scripted turn. Slots come from the policy record; the wording
    around them is fixed and never model-generated."""
    if lang == "en" and register == "singlish":
        return _render_singlish(turn, p, part_of_day, agent_name)

    g = _greeting(lang, part_of_day)

    if lang == "zh":
        return {
            1: f"{g}，{_zh_address(p)}。我是 Etiqa 保险的{agent_name}。请问是{_zh_address(p)}本人吗？",
            2: f"我是来跟您确认您在{p.property_address}的居家保险续保事项。",
            3: f"您的保单在{_zh_date(p.due_date)}到期。我想确认一下，您应该已经收到我们寄出的续保通知了吧？",
            4: (f"这次的保费是{zh_decimal(p.premium)}新币，{zh_number(p.term_years)}年配套，"
                f"家庭财物保额{zh_decimal(p.contents_si)}元，"
                f"装修保额{zh_decimal(p.reno_si)}元，"
                f"已经给您打了百分之{zh_decimal(p.discount_pct)}的折扣。"
                f"我会把详情发到{p.email}，请问这个邮箱正确吗？"),
            5: "请您在到期日之前完成续保，付款以后回复一下电邮就可以了。",
            6: cross_sell_line("zh"),
            7: "如果您有任何问题，欢迎记下来，我们会协助您。谢谢您的时间，祝您愉快。",
        }[turn]

    return {
        1: (f"{g} {p.salutation} {p.surname}. This is {agent_name} calling from "
            f"Etiqa Insurance. Am I speaking with {p.salutation} {p.surname}?"),
        2: (f"I'm doing a servicing call regarding your Tiq Home Insurance renewal "
            f"for your property at {p.property_address}?"),
        3: (f"Your due date is {_en_date(p.due_date)}, and I assume you've received a renewal "
            f"notice from Etiqa Insurance?"),
        4: (f"The final premium is {p.premium} dollars for a {p.term_years}-year plan, "
            f"with sum insured of Home Contents {p.contents_si} and Renovation {p.reno_si}. "
            f"A {p.discount_pct} percent discount has been applied. I'll send an email to "
            f"{p.email} — can I confirm that's correct?"),
        5: ("Please look through the email and renew by the due date, and do reply "
            "once payment is made."),
        6: f"Before I let you go — {cross_sell_line('en')}",
        7: (f"Feel free to note down any questions and we'll assist. Thank you for your "
            f"time, {p.salutation} {p.surname}. Have a good day."),
    }[turn]


# Singapore-English wording for the same seven turns. Call-centre Singlish is
# lighter than the internet version — "correct or not", "already", "can",
# sentence-final "ah" — so this aims at how an Etiqa agent actually talks, not
# at a caricature.
#
# COMPLIANCE: this is a rewording of the client's approved script. The facts,
# figures and disclosures are identical and still come from the fact store, but
# the phrasing itself needs Etiqa sign-off before it is used on a real call.
def _render_singlish(turn: int, p: Policy, part_of_day: str, agent_name: str) -> str:
    g = _greeting("en", part_of_day)
    return {
        1: (f"{g} {p.salutation} {p.surname} ah. I'm {agent_name}, calling from "
            f"Etiqa Insurance. Speaking to {p.salutation} {p.surname}, is it?"),
        2: (f"I'm calling about your Tiq Home Insurance renewal, for your place at "
            f"{p.property_address}. Correct or not?"),
        3: (f"Your due date is {_en_date(p.due_date)}. You got receive our renewal notice "
            f"already or not?"),
        4: (f"Okay so the premium is {p.premium} dollars, for the {p.term_years}-year "
            f"plan. Home contents {p.contents_si}, renovation {p.reno_si}. We already "
            f"give you {p.discount_pct} percent discount. I send the details to "
            f"{p.email} — this one correct?"),
        5: ("Just look through the email, then renew before the due date. After you "
            "pay, reply to the email can already."),
        6: (f"Oh ya, before I let you go — we got promotion now for "
            f"{CROSS_SELL['product'].en}, {CROSS_SELL['discount'].en} off, "
            f"from {CROSS_SELL['from_price'].en}, so about "
            f"{CROSS_SELL['monthly'].en}. Got COVID-19 coverage, and "
            f"{CROSS_SELL['inpatient'].en}, dengue also covered."),
        7: (f"Any questions just write down, we help you. Thank you ah "
            f"{p.salutation} {p.surname}, you take care."),
    }[turn]


#: Where turn 4 divides into a part that is fixed for the policy and a part
#: that carries the email address. Everything before the marker is warmed by
#: the pre-render pass; only the short tail can miss.
_EMAIL_SPLIT = ("I'll send an email to", "I send the details to", "我会把详情发到",
                "我把详情发到", "详情我会发到")


def split_on_email(text: str) -> tuple[str, str] | None:
    """Turn 4 with the email sentence separated out, or None.

    The address is the only slot that can change mid-call, and it sits inside
    the longest line in the script. Rendering the whole turn on a miss cost
    eight seconds of silence; rendering only the sentence that carries the
    address costs about two, and the rest comes off the cache as usual.
    """
    for marker in _EMAIL_SPLIT:
        i = text.find(marker)
        if i > 0:
            head, tail = text[:i].strip(), text[i:].strip()
            if head and tail:
                return head, tail
    return None


def source_label(turn: int) -> str:
    """What the console shows: pre-rendered audio, or live synthesis."""
    t = TURNS[turn - 1]
    if t.kind == "static":
        return "pre-rendered"
    if t.kind == "template":
        return "pre-rendered + slots"
    return "generated"
