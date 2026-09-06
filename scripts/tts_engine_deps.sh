#!/usr/bin/env bash
# Install what one TTS engine needs, and nothing another one needs.
#
# The candidate models do not share a dependency set — CosyVoice and IndexTTS
# are git repositories rather than packages, F5 and Chatterbox pin different
# torch-adjacent libraries — so one image per engine is the honest shape.
# Dockerfile.tts calls this with its TTS_ENGINE build argument; it also runs by
# hand into a venv on a GPU host:
#
#     VENV=.venv-tts ./scripts/tts_engine_deps.sh cosyvoice3
#
# Model weights are NOT downloaded here: they land in the HF cache on first
# start, which docker-compose.yml and deploy/rhel/services.sh mount from the
# host so a rebuild never re-downloads them.
set -euo pipefail

ENGINE="${1:-${TTS_ENGINE:-chatterbox}}"

# Where to install. Give VENV a virtualenv and the right installer is worked
# out for it: `uv pip --python` when uv is on the PATH, else the venv's own
# `python -m pip`. This repo builds its venvs with `uv venv`, which does NOT
# put a `pip` binary in them — pointing PIP at .venv/bin/pip fails with
# "No such file or directory", which is what this exists to prevent.
#
#     VENV=.venv-tts ./scripts/tts_engine_deps.sh kokoro    # recommended
#     PIP="python3.11 -m pip" ./scripts/tts_engine_deps.sh kokoro   # a plain host
#
# In the container there is no venv at all and the default is right.
if [ -n "${VENV:-}" ]; then
  [ -x "$VENV/bin/python" ] || {
    echo "no interpreter at $VENV/bin/python — create it first: uv venv $VENV --python 3.11" >&2
    exit 2
  }
  if [ -x "$VENV/bin/pip" ]; then
    PIP="${PIP:-$VENV/bin/pip}"
  elif command -v uv >/dev/null 2>&1; then
    # uv takes --python only *after* the subcommand, which does not fit the
    # `$PIP install …` shape the arms below use; VIRTUAL_ENV targets it just
    # as well, and uv accepts pip's --no-cache-dir.
    VIRTUAL_ENV="$(cd "$VENV" && pwd)"
    export VIRTUAL_ENV
    PIP="${PIP:-uv pip}"
  else
    PIP="${PIP:-$VENV/bin/python -m pip}"
  fi
fi
PIP="${PIP:-python3.11 -m pip}"
PYBIN="${PYBIN:-${VENV:+$VENV/bin/python}}"
PYBIN="${PYBIN:-python3.11}"

# Fail here, naming the fix, rather than three lines into a case arm. `list`
# is the one no-op every form above understands.
if ! $PIP list >/dev/null 2>&1; then
  echo "cannot run the installer: $PIP" >&2
  echo "  pass VENV=<dir> for a virtualenv, or PIP='<python> -m pip' for a host interpreter." >&2
  exit 2
fi

# Where repository-shaped engines are cloned. The sidecar reads the same
# variables at run time (COSYVOICE_HOME, INDEXTTS_HOME), so keep them in step.
PREFIX="${TTS_ENGINE_PREFIX:-/opt}"

common=(fastapi "uvicorn[standard]" numpy huggingface_hub)

echo "==> TTS engine: $ENGINE"
case "$ENGINE" in
  chatterbox|chatterbox-turbo|chatterbox-nano)
    $PIP install --no-cache-dir torch torchaudio chatterbox-tts "${common[@]}"
    ;;
  cosyvoice3)
    # Apache 2.0. Cloned recursively: Matcha-TTS rides along as a submodule
    # and the package imports it by path.
    $PIP install --no-cache-dir torch torchaudio "${common[@]}"
    if [ ! -d "$PREFIX/CosyVoice" ]; then
      git clone --recursive --depth 1 https://github.com/FunAudioLLM/CosyVoice.git "$PREFIX/CosyVoice"
    fi
    $PIP install --no-cache-dir -r "$PREFIX/CosyVoice/requirements.txt"
    echo "    set COSYVOICE_HOME=$PREFIX/CosyVoice for the sidecar"
    ;;
  f5)
    # MIT code; the shipped weights are CC-BY-NC-4.0. See docs/tts-models.md.
    $PIP install --no-cache-dir torch torchaudio f5-tts "${common[@]}"
    ;;
  indextts2)
    # bilibili Model Use License. The repo installs as a package; the
    # weights are fetched into checkpoints/ where the sidecar expects them.
    $PIP install --no-cache-dir torch torchaudio "${common[@]}"
    if [ ! -d "$PREFIX/index-tts" ]; then
      git clone --depth 1 https://github.com/index-tts/index-tts.git "$PREFIX/index-tts"
    fi
    $PIP install --no-cache-dir -e "$PREFIX/index-tts"
    $PIP install --no-cache-dir "huggingface_hub[cli]"
    hf download IndexTeam/IndexTTS-2 --local-dir "$PREFIX/index-tts/checkpoints"
    echo "    set INDEXTTS_HOME=$PREFIX/index-tts for the sidecar"
    ;;
  kokoro)
    # Apache 2.0, no cloning. misaki[zh] is what reads Mandarin; the espeak
    # loader is the English fallback G2P, which otherwise fails at first
    # synthesis rather than at install.
    $PIP install --no-cache-dir torch kokoro "misaki[en,zh]" espeakng-loader "${common[@]}"
    # misaki's English G2P loads spaCy's en_core_web_sm, which is NOT a pip
    # dependency of anything above. Without it the sidecar starts, reports
    # itself healthy, and then 500s on the first English line — the failure
    # this repo's pyproject already warns about on the Mac side. Installed
    # by URL rather than `spacy download`, which shells out to pip and so
    # does not work inside a uv-made venv.
    sp="$($PYBIN -c 'import spacy; print(spacy.__version__.rsplit(".", 1)[0])')"
    $PIP install --no-cache-dir \
      "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-${sp}.0/en_core_web_sm-${sp}.0-py3-none-any.whl"
    ;;
  fish)
    # Research licence. Installed from the repository, which pins its own
    # torch (2.8.0) — the reason this is one image on its own. The S2-Pro
    # checkpoint (~10 GB) lands where its api_server expects it.
    if [ ! -d "$PREFIX/fish-speech" ]; then
      git clone --depth 1 https://github.com/fishaudio/fish-speech.git "$PREFIX/fish-speech"
    fi
    $PIP install --no-cache-dir -e "$PREFIX/fish-speech[${FISH_EXTRA:-cu129}]" "${common[@]}"
    $PIP install --no-cache-dir "huggingface_hub[cli]"
    hf download "${FISH_REPO:-fishaudio/s2-pro}" --local-dir "$PREFIX/fish-speech/checkpoints/s2-pro"
    echo "    set FISH_HOME=$PREFIX/fish-speech for the sidecar"
    ;;
  fish-server)
    # The model runs in fish-speech's own api_server; this sidecar only
    # forwards to it, so all it needs is the wire format.
    $PIP install --no-cache-dir ormsgpack "${common[@]}"
    echo "    run fish-speech's api_server separately and set FISH_URL"
    ;;
  vibevoice)
    # MIT, English only, preset voices. Installed from the repository with
    # its streaming extra; the voice prompts (.pt) ship inside the clone.
    if [ ! -d "$PREFIX/VibeVoice" ]; then
      git clone --depth 1 https://github.com/microsoft/VibeVoice.git "$PREFIX/VibeVoice"
    fi
    $PIP install --no-cache-dir torch "${common[@]}"
    $PIP install --no-cache-dir -e "$PREFIX/VibeVoice[streamingtts]"
    # flash-attn is what the authors tested; the sidecar falls back to sdpa
    # if this fails to build on the host.
    $PIP install --no-cache-dir flash-attn --no-build-isolation || \
      echo "    flash-attn did not build; the sidecar will use sdpa"
    echo "    set VIBEVOICE_HOME=$PREFIX/VibeVoice for the sidecar"
    ;;
  *)
    echo "unknown TTS engine: $ENGINE" >&2
    echo "one of: chatterbox chatterbox-turbo chatterbox-nano cosyvoice3 f5 indextts2 kokoro fish fish-server vibevoice" >&2
    exit 2
    ;;
esac
