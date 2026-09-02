"""Synthetic CRM.

Every record here is invented. Demo environments get screen-shared, recorded
and left running on laptops — no real policyholder data may enter this file.
The console marks these records as synthetic so nobody later mistakes the
environment for production.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class Policy:
    policy_id: str
    name: str
    salutation: str
    surname: str
    #: Spoken in English on every call, Mandarin included. A Mandarin
    #: rendering (裕廊西第四街) is not what is printed on the policy, not what
    #: the customer would read back, and not what a colleague picking up a
    #: transfer would search for.
    property_address: str
    due_date: str
    premium: str
    term_years: int
    contents_si: str
    reno_si: str
    discount_pct: str
    email: str
    #: The number a callback goes to. Read back four digits at a time when we
    #: promise one — a callback to a number nobody answers is worse than none.
    phone: str
    language: str               # preferred language on file
    # Compliance state — the gates read these, they are not decoration.
    dnc_listed: bool            # on the No Voice Call register
    marketing_consent: bool     # explicit, unambiguous consent on file
    dnc_checked_days_ago: int   # a check is valid for 21 days

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


POLICIES: dict[str, Policy] = {
    "TH-4471-0093": Policy(
        policy_id="TH-4471-0093",
        name="Tan Wei Ming", salutation="Mr", surname="Tan",
        property_address="Jurong West Street 4, #08-212",
        due_date="10 February 2026",
        premium="412", term_years=5,
        contents_si="35,000", reno_si="60,000", discount_pct="23.5",
        email="wm.tan@example.sg",
        phone="+65 9123 4417",
        language="en",
        dnc_listed=False, marketing_consent=True, dnc_checked_days_ago=3,
    ),
    "TH-5120-7742": Policy(
        policy_id="TH-5120-7742",
        name="Nurul Aisyah", salutation="Ms", surname="Nurul",
        property_address="Tampines Street 21, #11-408",
        due_date="3 March 2026",
        premium="388", term_years=5,
        contents_si="30,000", reno_si="45,000", discount_pct="23.5",
        email="n.aisyah@example.sg",
        phone="+65 9224 8830",
        language="en",
        # On the register and no consent on file: servicing is fine,
        # the cross-sell is not.
        dnc_listed=True, marketing_consent=False, dnc_checked_days_ago=2,
    ),
    "TH-8802-1156": Policy(
        policy_id="TH-8802-1156",
        name="Lim Hui Ling", salutation="Ms", surname="Lim",
        property_address="Ang Mo Kio Avenue 10, #05-77",
        due_date="22 February 2026",
        premium="455", term_years=5,
        contents_si="40,000", reno_si="75,000", discount_pct="23.5",
        email="hl.lim@example.sg",
        phone="+65 9337 6152",
        language="zh",
        dnc_listed=False, marketing_consent=True, dnc_checked_days_ago=30,  # stale check
    ),
}


def get(policy_id: str) -> Policy:
    return POLICIES[policy_id]


def all_policies() -> list[Policy]:
    return list(POLICIES.values())
