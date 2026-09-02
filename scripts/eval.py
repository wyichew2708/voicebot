"""Replay every recorded call through the engine and measure what happens.

Every fix this project has made was found by someone pasting a transcript and
saying "this is weird". The transcripts are in logs/calls.jsonl — 61 calls,
225 caller turns at the time of writing — and there was no way to run them
all again after a change. This does that.

Two things come out. A **profile** of how the engine handles real callers:
what share of turns the keyword layer settles for free, what share reaches
the model, how often the call ends up offering an escalation or asking the
caller to repeat themselves. And a **check** against tests/eval/expectations.jsonl,
where each line names a caller utterance and what the reply must or must not
contain. A failed expectation is a regression against a real call.

    scripts/eval.py                   # keyword layer only (mock backend), fast
    scripts/eval.py --live            # with the real models, ~1-2 s a turn
    scripts/eval.py --show            # print every turn, not just the summary
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voicebot import config                                  # noqa: E402
from voicebot.knowledge import policy as knowledge_policy
from voicebot.call.engine import CallSession                 # noqa: E402
from voicebot.data import personas                           # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CALLS = ROOT / "logs" / "calls.jsonl"
EXPECT = ROOT / "tests" / "eval" / "expectations.jsonl"

GUARD = re.compile(r"Guardrail routed an unrecognised reply to '(\w+)' in (\d+) ms")


def load_calls():
    for line in CALLS.read_text().splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        turns = [(e["text"], (e.get("lang") or c.get("lang") or "en").lower())
                 for e in c.get("events", [])
                 if e.get("kind") == "transcript" and e.get("speaker") == "caller"]
        if turns:
            yield c, turns


def load_expectations():
    if not EXPECT.exists():
        return []
    return [json.loads(l) for l in EXPECT.read_text().splitlines() if l.strip()]


async def replay(call, turns, backend, guardrail):
    policy = personas.get(call.get("policy_id") or "TH-4471-0093")
    s = CallSession(policy, backend, lang=call.get("lang", "en"),
                    register=call.get("register", "standard"),
                    guardrail=guardrail, guardrail_timeout_ms=4000)
    async for _ in s.start():
        pass
    rows = []
    resumed = False
    for text, lang in turns:
        if s.ended:
            # The replay diverged from the recording and ended early — a
            # handoff the real call did not take, say. The remaining turns
            # are still real caller speech worth measuring, so carry on in a
            # fresh session rather than dropping them; the rows are marked.
            s = CallSession(policy, backend, lang=call.get("lang", "en"),
                            register=call.get("register", "standard"),
                            guardrail=guardrail, guardrail_timeout_ms=4000)
            async for _ in s.start():
                pass
            resumed = True
        said, notes, tools, label, ms = [], [], [], None, None
        async for ev in s.on_caller(text, lang):
            if ev.kind == "transcript" and ev.speaker == "agent":
                said.append(ev.text)
            elif ev.kind == "system":
                notes.append(ev.text)
                m = GUARD.search(ev.text)
                if m:
                    label, ms = m.group(1), int(m.group(2))
            elif ev.kind == "tool":
                tools.append(ev.tool)
        reply = " ".join(said)
        path = ("guardrail:" + label if label
                else "handoff" if s.handoff and any("handoff" in t for t in tools)
                else "clarify" if any("asking again" in n for n in notes)
                else "noise" if any("noise" in n for n in notes)
                else "keyword")
        rows.append({"caller": text, "lang": lang, "reply": reply, "path": path,
                     "label": label, "ms": ms, "resumed": resumed,
                     "officer": "customer care" in reply or "客服专员" in reply,
                     "handoff": s.handoff is not None})
    return rows


class _RoutingOnly:
    """The real backend with its mouth taped shut.

    This measures how caller turns are *routed*, and routing is the router
    and the dictation reader — one 23 GB model. Loading the synthesiser and
    the recogniser beside it, as the console does, put the eval over this
    machine's GPU memory and every model call failed with Metal
    out-of-memory: a run that measured nothing. Speech comes back empty; the
    engine treats it as a cache hit and carries on.
    """

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def speak(self, text, lang, prerendered, voice=None):
        from voicebot.runtime.base import Speech
        return Speech(voice_source="cache", pcm=b"", sample_rate=16000, latency_ms=0)

    async def synthesize(self, text, lang, voice=None):
        return
        yield  # pragma: no cover — an empty async generator


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="real models, not the mock")
    ap.add_argument("--profile", default="mac-polyglot")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    # The eval must judge the engine under the same knowledge rules the named
    # profile runs with, or its numbers describe a bot nobody deploys. The
    # demo profile speaks unsourced placeholder wording; the RHEL profile
    # refuses it and offers a colleague, which is a different transcript.
    serving = knowledge_policy.configure(config.load(args.profile))
    print(f"knowledge: {serving.describe}\n")

    if args.live:
        from voicebot.runtime import load_backend
        from voicebot.call import router
        backend = _RoutingOnly(load_backend(config.load(args.profile)))
        # The first call into the model pays for loading it — six seconds
        # here — and a 4 s router timeout would count that as a failure on
        # whatever caller line happened to come first. The server does this
        # at startup for the same reason.
        asyncio.run(router.route(backend, "yes, speaking", 1, "en", timeout_ms=60000))
    else:
        from voicebot.runtime.mock import MockBackend
        backend = MockBackend()

    rows = []
    for call, turns in load_calls():
        rows.extend(asyncio.run(replay(call, turns, backend, guardrail=args.live)))

    n = len(rows)
    paths = collections.Counter(r["path"].split(":")[0] for r in rows)
    labels = collections.Counter(r["label"] for r in rows if r["label"])
    guard_ms = [r["ms"] for r in rows if r["ms"]]
    resumed = sum(r["resumed"] for r in rows)
    print(f"\n{n} caller turns across {sum(1 for _ in load_calls())} recorded calls"
          f"  ({'live models' if args.live else 'keyword layer only'})"
          + (f"\n  {resumed} turns replayed after the replay had ended a call the "
             f"recording did not" if resumed else "") + "\n")
    for path, k in paths.most_common():
        print(f"  {path:10} {k:4d}  {k / n:5.1%}")
    print(f"\n  offered customer care : {sum(r['officer'] for r in rows):3d}"
          f"  ({sum(r['officer'] for r in rows) / n:.1%} of turns)")
    if guard_ms:
        print(f"  guardrail latency     : median {statistics.median(guard_ms):.0f} ms, "
              f"p90 {sorted(guard_ms)[int(len(guard_ms) * .9)]} ms")
    if labels:
        print("  guardrail labels      : " +
              ", ".join(f"{l} {k}" for l, k in labels.most_common()))

    if args.show:
        print()
        for r in rows:
            print(f"  [{r['path']:>20}] {r['caller'][:40]!r:44} -> {r['reply'][:60]!r}")

    # -- expectations ------------------------------------------------------
    failures = 0
    expects = load_expectations()
    by_caller = collections.defaultdict(list)
    for r in rows:
        by_caller[r["caller"].strip().lower()].append(r)
    print(f"\n{len(expects)} expectations")
    for ex in expects:
        hits = by_caller.get(ex["caller"].strip().lower(), [])
        if not hits:
            print(f"  ?  {ex['caller'][:50]!r} — never said in a recorded call")
            continue
        for r in hits:
            bad = []
            for want in ex.get("contains", []):
                if want.lower() not in r["reply"].lower():
                    bad.append(f"missing {want!r}")
            # The same call may be answered in either language, depending on
            # where the caller had steered it by then.
            anys = ex.get("contains_any", [])
            if anys and not any(a.lower() in r["reply"].lower() for a in anys):
                bad.append(f"none of {anys!r}")
            for nope in ex.get("not_contains", []):
                if nope.lower() in r["reply"].lower():
                    bad.append(f"has {nope!r}")
            if ex.get("path") and not r["path"].startswith(ex["path"]):
                bad.append(f"path {r['path']} not {ex['path']}")
            anyp = ex.get("path_any", [])
            if anyp and not any(r["path"].startswith(a) for a in anyp):
                bad.append(f"path {r['path']} not in {anyp}")
            if bad:
                failures += 1
                print(f"  ✗  {ex['caller'][:44]!r} -> {r['reply'][:50]!r}\n"
                      f"       {'; '.join(bad)}")
    print(f"  {len(expects) - failures} met, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
