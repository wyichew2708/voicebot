"""Render every scripted line to disk before a demo.

Six of seven turns are fixed wording, so this covers most of every call. Run it
after changing the script, the personas or the voice instruction — the cache is
keyed on all three, so a stale line simply misses rather than being served.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voicebot import config                                   # noqa: E402
from voicebot.knowledge import policy as knowledge_policy
from voicebot.voices import CustomVoices                       # noqa: E402
from voicebot.runtime import warm                              # noqa: E402
from voicebot.runtime.prerender import PrerenderCache         # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="mac-polyglot")
    ap.add_argument("--registers", nargs="*", default=["standard", "singlish"])
    ap.add_argument("--force", action="store_true", help="re-render cached lines")
    ap.add_argument("--langs", nargs="*", default=None,
                    help="only these languages (default: all in the profile)")
    ap.add_argument("--voices", nargs="*", default=None,
                    help="only these voices (default: every one in the profile)")
    ap.add_argument("--console", default="http://127.0.0.1:8788",
                    help="pause while this console has a call up ('' to ignore)")
    args = ap.parse_args()

    cfg = config.load(args.profile)
    pr = cfg.get("backend", {}).get("tts", {}).get("prerender", {})
    if not pr.get("model"):
        sys.exit(f"profile {args.profile!r} has no tts.prerender.model configured")

    # Voices an admin recorded in the console are rendered here too, or their
    # first call would synthesise every line live.
    CustomVoices().merge_into(pr)

    cache = PrerenderCache(pr, cfg["audio"]["sample_rate"])
    langs = [l for l in cfg.get("languages", ["en"])
             if args.langs is None or l in args.langs]
    if not langs:
        sys.exit(f"no languages to render: profile has {cfg.get('languages')}")
    voices = [v for v in (list(cache.voices()) or [None])
              if args.voices is None or v in args.voices]
    if not voices:
        sys.exit(f"no voices to render: profile has {list(cache.voices())}")

    # Same knowledge policy the server will run under, so the pre-render pass
    # and the console agree on which coverage answers exist.
    serving = knowledge_policy.configure(cfg)
    print(f"knowledge: {serving.describe}")
    jobs = warm.plan(langs, args.registers, voices, serving)
    todo = warm.outstanding(cache, jobs, args.force)
    print(f"{len(jobs)} distinct lines, {len(todo)} to render "
          f"({len(jobs)-len(todo)} already cached)")
    if not todo:
        return

    t_all = time.time()
    paused = 0.0
    for i, (text, lang, voice), ms in warm.render(cache, todo):
        # Warming and speaking use the same GPU, and a line the caller is
        # waiting on loses badly to a batch job: a turn that reads from a warm
        # cache in 5 ms took 13.6 seconds with this running behind it. The
        # console knows whether a call is up; ask it and stand aside.
        paused += _wait_for_the_line(args.console)
        if ms is None:
            sys.exit("pre-render model unavailable — nothing written")
        print(f"  [{i:3d}/{len(todo)}] {ms/1000:5.1f}s  {lang}/{voice}  {text[:44]}")
    took = time.time() - t_all
    note = f" ({paused:.0f}s of it waiting for calls)" if paused > 1 else ""
    print(f"\ndone in {took:.0f}s{note} -> {cache.dir}")


def _wait_for_the_line(console: str) -> float:
    """Block while the console has a call up. Returns seconds spent waiting.

    A console that is not running, or not answering, is not a reason to stop
    warming — this is a convenience, not a lock.
    """
    if not console:
        return 0.0
    import json as _json
    import urllib.request

    waited = 0.0
    while True:
        try:
            with urllib.request.urlopen(console.rstrip("/") + "/api/health",
                                        timeout=2) as r:
                if not _json.loads(r.read()).get("on_call"):
                    return waited
        except Exception:
            return waited
        if not waited:
            print("  ... a call is running — pausing", flush=True)
        time.sleep(1.0)
        waited += 1.0


if __name__ == "__main__":
    main()
