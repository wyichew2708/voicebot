#!/usr/bin/env bash
# Pre-download MLX weights. Run this once, well before any demo — the first
# pull is tens of gigabytes and you do not want it happening on stage.
set -euo pipefail

MODELS=(
  "mlx-community/Qwen3.6-35B-A3B-4bit"          # ~20 GB
  "mlx-community/whisper-large-v3-turbo"        # ~1.6 GB  (ASR fallback)
  "mlx-community/Kokoro-82M-4bit"               # ~0.1 GB
)

command -v hf >/dev/null 2>&1 || {
  echo "huggingface-cli not found. Install with: uv pip install huggingface_hub[cli]" >&2
  exit 1
}

for m in "${MODELS[@]}"; do
  echo "==> $m"
  hf download "$m"
done

cat <<'NOTE'

Done.

Still open — the Mac ASR choice (see docs/deployments.md §3):
  whisper-large-v3-turbo is the fallback that definitely works, but it is weak
  on Singlish. The preferred path is Polyglot-Lion-1.7B (a Qwen3-ASR fine-tune
  for Singapore's four official languages), which needs converting to MLX first:

      mlx_lm.convert --hf-path <polyglot-lion-repo> --q-bits 4 \
                     --mlx-path models/polyglot-lion-1.7b-4bit

  Measure both on your own Singlish recordings before choosing. That comparison
  is week 1 of the plan.
NOTE
