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


# --- the two backends have to offer the call the same choices --------------

def test_both_backends_expose_the_same_capabilities():
    """A method on one backend and not the other is a call that behaves
    differently on the GPU box for reasons nobody wrote down.

    `cached` is the example this test was added for: the streaming budget asks
    it whether a chunk is free, and a backend that cannot answer is read as
    "nothing is cached", so the RHEL box quietly declined to split lines the
    Mac would have split.
    """
    import inspect

    from voicebot.runtime.cuda_backend import CUDABackend
    from voicebot.runtime.mlx_backend import MLXBackend

    def public(cls):
        return {n for n, _ in inspect.getmembers(cls, callable)
                if not n.startswith("_")}

    mlx, cuda = public(MLXBackend), public(CUDABackend)
    # `load` is Apple-only on purpose: the CUDA backend talks to services that
    # are already up, so it has nothing to load.
    assert mlx - cuda == {"load"}, f"only on MLX: {sorted(mlx - cuda - {'load'})}"
    assert not cuda - mlx, f"only on CUDA: {sorted(cuda - mlx)}"


def test_the_cache_question_is_answered_the_same_way():
    """Both read the same PrerenderCache, so a line is a hit on both boxes or
    neither. Divergence here is a voice change mid-call on one of them."""
    import inspect

    from voicebot.runtime.cuda_backend import CUDABackend
    from voicebot.runtime.mlx_backend import MLXBackend

    for cls in (MLXBackend, CUDABackend):
        src = inspect.getsource(cls.cached)
        assert "self.prerender.path(text, lang, voice).exists()" in src, cls.__name__
