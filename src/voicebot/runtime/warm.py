"""Which lines a voice needs rendered before it can take a call.

Shared by `scripts/prerender.py` and the console's own warm-up, because the
two must agree exactly: a line the script renders and the warm-up misses is a
line the caller waits two seconds for, in a voice that may not even match the
one either side of it.
"""
from __future__ import annotations

from typing import Any, Iterator

from ..call import handoff, script
from ..call.engine import EMAIL_CONFIRM, EMAIL_ONLY_ONE, prerenderable_lines
from ..data import personas
from ..data.facts import policy_answers, price_answer

Job = tuple[str, str, str | None]          # text, language, voice


def plan(langs: list[str], registers: list[str],
         voices: list[str | None]) -> list[Job]:
    """Every distinct (line, language, voice) a call can reach.

    Every configured voice gets its own entries: the cache is keyed on the
    speaker, so switching voice in the console must never serve the previous
    speaker's audio.
    """
    jobs: list[Job] = []
    seen: set[Job] = set()

    def add(text: str, lang: str, voice: str | None) -> None:
        if text and (text, lang, voice) not in seen:
            seen.add((text, lang, voice))
            jobs.append((text, lang, voice))

    for voice in voices or [None]:
        for policy in personas.all_policies():
            for lang in langs:
                for register in registers:
                    if lang != "en" and register == "singlish":
                        continue             # Singlish register is English-only
                    for turn in range(1, 8):
                        text = script.render(turn, policy, lang, register=register)
                        add(text, lang, voice)
                        # Turn 4 is also warmed split, because the email slot
                        # can change mid-call and only the tail should have to
                        # be rendered when it does.
                        if turn == 4:
                            for part in script.split_on_email(text) or ():
                                add(part, lang, voice)
        # Acknowledgements and repeat lead-ins are spoken from the cache too;
        # a miss would synthesise mid-turn, in front of the line it introduces.
        for text, lang in prerenderable_lines():
            if lang in langs:
                add(text, lang, voice)
        # Answers about the caller's own record. Every agent line comes from
        # this one model, so these need warming like the rest — otherwise the
        # first "what's my premium?" of a demo costs a render.
        for policy in personas.all_policies():
            for lang in langs:
                for text in policy_answers(policy, lang).values():
                    add(text, lang, voice)
                add(EMAIL_CONFIRM[lang].format(email=policy.email), lang, voice)
                # Carries the caller's own address, so it is per-policy too.
                add(EMAIL_ONLY_ONE[lang].format(email=policy.email), lang, voice)
                add({"en": f"Updated — I'll send it to {policy.email}.",
                     "zh": f"已经更新了，我会发到 {policy.email}。"}[lang], lang, voice)
                # The reachability question carries the caller's own number.
                add(handoff.reachable(policy.phone, lang), lang, voice)
                # The discount answer quotes this policy's own figures.
                add(price_answer(policy, lang), lang, voice)
    return jobs


def outstanding(cache: Any, jobs: list[Job], force: bool = False) -> list[Job]:
    return [j for j in jobs if force or cache.get(*j) is None]


def render(cache: Any, jobs: list[Job]) -> Iterator[tuple[int, Job, int | None]]:
    """Render each job, yielding (index, job, milliseconds) as it lands.

    `None` for the timing means the model was unavailable — the caller decides
    whether that ends the run.
    """
    import time

    for i, job in enumerate(jobs, 1):
        t0 = time.time()
        got = cache.render(*job)
        yield i, job, (None if got is None else int((time.time() - t0) * 1000))
