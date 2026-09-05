#!/usr/bin/env bash
# Install what one TTS engine needs, and nothing another one needs.
#
# The candidate models do not share a dependency set — CosyVoice and IndexTTS
# are git repositories rather than packages, F5 and Chatterbox pin different
# torch-adjacent libraries — so one image per engine is the honest shape.
# Dockerfile.tts calls this with its TTS_ENGINE build argument; it also runs by
# hand into a venv on a GPU host:
#
#     PIP=".venv-tts/bin/pip" ./scripts/tts_engine_deps.sh cosyvoice3
#
# Model weights are NOT downloaded here: they land in the HF cache on first
# start, which docker-compose.yml and deploy/rhel/services.sh mount from the
# host so a rebuild never re-downloads them.
set -euo pipefail

ENGINE="${1:-${TTS_ENGINE:-chatterbox}}"
PIP="${PIP:-python3.11 -m pip}"
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
    ;;
  fish)
    # Research licence. The model runs in fish-speech's own api_server; this
    # sidecar only forwards to it, so all it needs is the wire format.
    $PIP install --no-cache-dir ormsgpack "${common[@]}"
    echo "    run fish-speech's api_server separately and set FISH_URL"
    ;;
  *)
    echo "unknown TTS engine: $ENGINE" >&2
    echo "one of: chatterbox chatterbox-turbo chatterbox-nano cosyvoice3 f5 indextts2 kokoro fish" >&2
    exit 2
    ;;
esac
