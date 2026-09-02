#!/usr/bin/env bash
# One-time host preparation for the RHEL GPU box.
#
# Ordered so each step fails loudly before the next depends on it. The GPU
# check in particular runs BEFORE any model work — SELinux blocking container
# device access surfaces as an opaque CUDA init error otherwise, and people
# lose an afternoon to it.
set -euo pipefail

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
die() { printf '\033[31mFAILED: %s\033[0m\n' "$1" >&2; exit 1; }

say "1/5  Host facts"
. /etc/os-release
echo "  ${PRETTY_NAME}"
[ "${VERSION_ID%%.*}" = "9" ] || echo "  WARNING: written for RHEL 9; the toolkit path differs on 8"
command -v podman >/dev/null || die "podman not installed (dnf install podman)"
podman --version

say "2/5  NVIDIA driver"
command -v nvidia-smi >/dev/null || die "nvidia-smi missing — install the driver first"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader

say "3/5  Container toolkit + CDI"
if ! rpm -q nvidia-container-toolkit >/dev/null 2>&1; then
  echo "  installing nvidia-container-toolkit"
  sudo dnf install -y nvidia-container-toolkit
fi
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
grep -c "nvidia.com/gpu" /etc/cdi/nvidia.yaml >/dev/null || die "CDI spec looks empty"
echo "  /etc/cdi/nvidia.yaml written"

say "4/5  GPU visible inside a container  (the step that catches SELinux)"
if ! podman run --rm --device nvidia.com/gpu=all \
      docker.io/nvidia/cuda:12.4.0-base-ubi9 nvidia-smi -L; then
  cat >&2 <<'HINT'

  GPU not visible inside the container. Usual causes, in order:
    * SELinux blocking container device access:
        sudo setsebool -P container_use_devices 1
    * CDI spec stale after a driver update — re-run step 3
    * rootless podman without the right subuid/subgid ranges
HINT
  die "container GPU access"
fi

say "5/5  Firewall"
sudo firewall-cmd --add-port=8788/tcp --permanent >/dev/null 2>&1 || true
sudo firewall-cmd --reload >/dev/null 2>&1 || true
echo "  console port 8788 open; model services stay bound to localhost"

printf '\n\033[32mHost ready.\033[0m  Next: make rhel-up\n'
