#!/usr/bin/env bash
# Package already-validated boot media locally; never tag, push, or publish.
# Use `make package` to run the validation gate first.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
arch="${1:?usage: package-release.sh arm64|x86_64}"
case "$arch" in
    arm64) image=build-arm64/pythonos-arm64.elf ;;
    x86_64) image=build/pythonos.iso ;;
    *) echo "unsupported architecture: $arch" >&2; exit 2 ;;
esac
test -s "$image"
revision="$(git describe --tags --always --dirty)"
platform="$(uname -s | tr '[:upper:]' '[:lower:]')"
bundle="pythonos-${revision}-${platform}-${arch}"
mkdir -p dist
stage="$(mktemp -d "${TMPDIR:-/tmp}/pythonos-package.XXXXXX")"
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/$bundle"
cp -f "$image" README.md LICENSE RELEASE-NOTES.md "$stage/$bundle/"
printf 'revision=%s\nhost=%s\ntarget=%s\n' "$revision" "$(uname -sm)" "$arch" \
    > "$stage/$bundle/BUILD-INFO.txt"
tar -czf "dist/$bundle.tar.gz" -C "$stage" "$bundle"
(
    cd dist
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$bundle.tar.gz"
    else
        shasum -a 256 "$bundle.tar.gz"
    fi
) > "dist/$bundle.tar.gz.sha256"
printf 'release media: dist/%s.tar.gz\n' "$bundle"
