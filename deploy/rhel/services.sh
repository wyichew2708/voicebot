#!/usr/bin/env bash
# Start the GPU services the console talks to. Each is a separate container so
# one can be restarted without disturbing the others — and so the voice ASR is
# never sharing a process with whatever else uses the LLM.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HF_CACHE="${HF_HOME:-$HOME/.cache/huggingface}"
GPU="--device nvidia.com/gpu=all"
# :Z relabels for SELinux; without it the container cannot read the cache.
MOUNT="-v ${HF_CACHE}:/root/.cache/huggingface:Z"

start() {  # name image port args...
  local name=$1 image=$2 port=$3; shift 3
  if podman container exists "$name"; then
    echo "  $name already exists — podman rm -f $name to recreate"; return
  fi
  echo "  starting $name on :$port"
  podman run -d --name "$name" $GPU $MOUNT \
    -p "127.0.0.1:${port}:${port}" --restart=unless-stopped "$image" "$@"
}

# ASR — MERaLiON behind vLLM. Its own package wraps vLLM with the right
# prompt, chunking and decode config; do not hand-roll this.
start voicebot-asr docker.io/vllm/vllm-openai:latest 8801 \
  --model MERaLiON/MERaLiON-3-3B-ASR --port 8801 \
  --max-model-len 8192 --gpu-memory-utilization 0.22 --trust-remote-code

# TTS — improvised lines only; scripted turns come from voices/cache.
start voicebot-tts localhost/voicebot-tts:latest 8802

cat <<'NOTE'

  The LLM is NOT started here. Qwen3.6 is already running on this box, and
  voice should point at a dedicated replica or a priority-scheduled queue —
  a batch job landing mid-call is dead air to the customer. Set its address in
  config/rhel.yaml under backend.llm.base_url.

  Check readiness:  curl -s localhost:8788/api/health
NOTE
