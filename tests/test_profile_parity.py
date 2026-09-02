"""The Mac profile and the RHEL profile have to agree about the audible things.

The two deployments share a pre-rendered cache whose keys cover the voice, its
reference clip, its parameters and its target pitch. A value that differs
between the profiles does not fail — it *misses*, and every line rendered on
one machine is re-rendered on the other into a slightly different voice. The
same goes for the guardrail: a different timeout is a different call.
"""
import pytest

from voicebot import config


@pytest.fixture(scope="module")
def profiles():
    return config.load("mac-polyglot"), config.load("rhel")


def test_the_two_profiles_render_into_the_same_cache(profiles):
    mac, rhel = profiles
    a = mac["backend"]["tts"]["prerender"]
    b = rhel["backend"]["tts"]["prerender"]
    for field in ("model", "cache_dir", "default_voice", "params", "voices"):
        assert a.get(field) == b.get(field), \
            f"{field} differs — every cached line would miss on the other box"


def test_the_guardrail_behaves_the_same_on_both(profiles):
    """Left out of a profile this defaults to a 1500 ms timeout, which is a
    different call from the 2500 ms one — the same caller reaches a customer
    care officer on one box and not on the other."""
    mac, rhel = profiles
    assert mac.get("guardrail") == rhel.get("guardrail")


def test_both_offer_the_same_languages(profiles):
    mac, rhel = profiles
    assert mac["languages"] == rhel["languages"]
    assert mac["audio"]["sample_rate"] == rhel["audio"]["sample_rate"]
