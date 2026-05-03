#!/usr/bin/env bash
# Build a fresh ext2 disk image for PythonOS persistent storage.
#
# Image layout: a single ext2 filesystem at the device root, containing
# /home and /apps top-level directories. The kernel mounts these into the
# VFS at /home and /apps; / itself stays tmpfs (see ef6.4 boot wiring).
#
# Uses `mkfs.ext2 -d <staging>` so the image is populated without loop
# mounts or root privileges. Runs inside the build container — see
# tools/Dockerfile for the e2fsprogs dependency.
#
# Usage: build_disk.sh <output_path> <size_mb>
set -euo pipefail

IMG="${1:?usage: build_disk.sh <output_path> <size_mb>}"
SIZE_MB="${2:?usage: build_disk.sh <output_path> <size_mb>}"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$STAGE/home" "$STAGE/apps"
mkdir -p "$(dirname "$IMG")"

truncate -s "${SIZE_MB}M" "$IMG"
mkfs.ext2 -F -t ext2 -b 4096 -L pythonos -d "$STAGE" "$IMG" >/dev/null

echo "Built $IMG: ${SIZE_MB} MiB ext2 with /home and /apps"
