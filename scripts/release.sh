#!/usr/bin/env bash
# Automated release script for PythonOS.
# Usage:
#   ./scripts/release.sh [major|minor|patch|X.Y.Z|vX.Y.Z]
#
# Release order matches the nanolang workflow:
#   1. require a clean main branch and GitHub CLI auth
#   2. run the local validation gate
#   3. push main and wait for CI to go green for HEAD
#   4. create/push an annotated tag
#   5. create the GitHub release from generated notes

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

info() {
    printf '[release] %s\n' "$*"
}

fail() {
    printf '[release] ERROR: %s\n' "$*" >&2
    exit 1
}

check_prerequisites() {
    command -v gh >/dev/null 2>&1 || fail "GitHub CLI (gh) is required"
    gh auth status >/dev/null 2>&1 || fail "GitHub CLI is not authenticated"
    [ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || fail "release must run on main"
    [ -z "$(git status --porcelain)" ] || fail "working tree is not clean"
}

current_version() {
    git tag -l 'v[0-9]*' | sort -V | tail -1 | sed 's/^v//'
}

next_version() {
    local current="$1"
    local bump="$2"

    if [[ "$bump" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        printf '%s\n' "${bump#v}"
        return
    fi

    [[ "$bump" =~ ^(major|minor|patch)$ ]] || \
        fail "version argument must be major, minor, patch, or X.Y.Z"

    local major minor patch
    IFS='.' read -r major minor patch <<< "$current"
    case "$bump" in
        major) major=$((major + 1)); minor=0; patch=0 ;;
        minor) minor=$((minor + 1)); patch=0 ;;
        patch) patch=$((patch + 1)) ;;
    esac
    printf '%s.%s.%s\n' "$major" "$minor" "$patch"
}

release_notes() {
    local previous="$1"
    local version="$2"
    local notes_file="$3"
    local range=""
    local commit_count=0

    if git rev-parse "v$previous" >/dev/null 2>&1; then
        range="v$previous..HEAD"
        commit_count=$(git rev-list --count "$range")
    else
        range="HEAD"
        commit_count=$(git rev-list --count HEAD)
    fi

    {
        printf '## PythonOS v%s\n\n' "$version"
        printf '### Validation\n'
        printf -- '- Local validation: `scripts/validate-release.sh`\n'
        printf -- '- CI: green for `%s`\n\n' "$(git rev-parse --short HEAD)"
        printf '### Statistics\n'
        printf -- '- Commits since v%s: %s\n\n' "$previous" "$commit_count"
        printf '### Changes\n\n'
        git log "$range" --pretty=format:'- %s' --no-merges
        printf '\n'
    } > "$notes_file"
}

wait_for_ci() {
    local head_sha="$1"
    local run_id=""

    info "waiting for CI run for $head_sha"
    for _ in $(seq 1 60); do
        run_id=$(gh run list \
            --branch main \
            --limit 20 \
            --json databaseId,headSha,status,conclusion \
            --jq ".[] | select(.headSha == \"$head_sha\") | .databaseId" \
            | head -1)
        if [ -n "$run_id" ]; then
            gh run watch "$run_id" --exit-status
            return
        fi
        sleep 10
    done
    fail "no CI run appeared for $head_sha"
}

main() {
    local bump="${1:-patch}"
    local previous
    previous="$(current_version)"
    if [ -z "$previous" ]; then
        previous="0.0.0"
    fi
    local version
    version="$(next_version "$previous" "$bump")"
    local tag="v$version"

    git rev-parse "$tag" >/dev/null 2>&1 && fail "tag $tag already exists"

    check_prerequisites

    info "releasing $tag (previous v$previous)"
    ./scripts/validate-release.sh

    info "syncing and pushing main"
    git pull --rebase origin main
    git push origin main

    local head_sha
    head_sha="$(git rev-parse HEAD)"
    wait_for_ci "$head_sha"

    local notes_file
    notes_file="$(mktemp)"
    trap 'rm -f "$notes_file"' EXIT
    release_notes "$previous" "$version" "$notes_file"

    info "creating annotated tag $tag"
    git tag -a "$tag" -m "Release $tag"
    git push origin "$tag"

    info "creating GitHub release $tag"
    gh release create "$tag" --title "$tag" --notes-file "$notes_file"

    info "release complete: $tag"
}

main "$@"
