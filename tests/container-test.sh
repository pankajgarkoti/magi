#!/usr/bin/env bash
# Fast loop: run install.sh on a fresh Fedora 44 container as a normal user with sudo.
#   tests/container-test.sh            (needs podman; downloads registry.fedoraproject.org/fedora:44)
# Checks: clean first install (with pre-existing files that must be backed up), an idempotent
# second run, --dry-run, magi-update --no-packages, uninstall + restore, and a reinstall.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
IMG=${IMG:-registry.fedoraproject.org/fedora:44}
LOG=${LOG:-$REPO/tests/container-test.log}
podman run --rm --replace --name magi-ctest \
  --security-opt label=disable -v "$REPO":/src:ro -v magi-dnf-cache:/var/cache/libdnf5 \
  "$IMG" bash /src/tests/container-inner.sh </dev/null 2>&1 | tee "$LOG"
