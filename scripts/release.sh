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

commit_range() {
    local previous="$1"
    if git rev-parse "v$previous" >/dev/null 2>&1; then
        printf 'v%s..HEAD\n' "$previous"
    else
        printf 'HEAD\n'
    fi
}

# Categorize commit subjects in $range into Keep-a-Changelog buckets.
# PythonOS commits don't reliably use conventional-commit prefixes, so
# we look at the leading verb. Output is the four buckets to stdout
# separated by `~~SECTION~~` markers — read by render_changelog_entry.
categorize_commits() {
    local range="$1"
    # Many PythonOS commits start with a `<scope>:` prefix
    # (e.g. `Dockerfile: fix x86 cross-build`, `kernel.shell: add tab
    # completion`). Strip an optional leading `<scope>:` before
    # categorizing on the leading verb so we don't dump everything
    # scoped into Other.
    git log "$range" --pretty=format:'%s' --no-merges | awk '
        BEGIN { added = ""; changed = ""; fixed = ""; removed = ""; other = "" }
        {
            full = $0
            verb = full
            # Strip a single "<scope>:" prefix (no spaces in scope) so
            # "release.sh: fix X" is categorized as Fixed, not Other.
            sub(/^[^[:space:]:]+:[[:space:]]*/, "", verb)
        }
        full ~ /^bd:/    { next }
        full ~ /^chore:/ { next }
        verb ~ /^[Ff]ix/ || verb ~ /^fix:/                          { fixed   = fixed   "- " full "\n"; next }
        verb ~ /^[Aa]dd/ || verb ~ /^feat:/ || verb ~ /^[Ii]mplement/ { added   = added   "- " full "\n"; next }
        verb ~ /^[Rr]efactor/ || verb ~ /^[Uu]pdate/ ||
        verb ~ /^[Cc]hange/ || verb ~ /^[Mm]igrate/ ||
        verb ~ /^[Mm]ove/ || verb ~ /^[Ww]ire/                       { changed = changed "- " full "\n"; next }
        verb ~ /^[Rr]emove/ || verb ~ /^[Dd]rop/ ||
        verb ~ /^[Dd]elete/                                          { removed = removed "- " full "\n"; next }
        { other = other "- " full "\n" }
        END {
            printf "%s~~SECTION~~%s~~SECTION~~%s~~SECTION~~%s~~SECTION~~%s",
                   added, changed, fixed, removed, other
        }
    '
}

# Build a Keep-a-Changelog entry for $version from commits in $range.
# Mirrors nanolang's update_changelog convention. Sections are only
# emitted when non-empty.
render_changelog_entry() {
    local version="$1"
    local range="$2"
    local date
    date="$(date +%Y-%m-%d)"

    local raw added changed fixed removed other
    raw="$(categorize_commits "$range")"
    added="$(  printf '%s\n' "$raw" | awk -F'~~SECTION~~' '{print $1}')"
    changed="$(printf '%s\n' "$raw" | awk -F'~~SECTION~~' '{print $2}')"
    fixed="$(  printf '%s\n' "$raw" | awk -F'~~SECTION~~' '{print $3}')"
    removed="$(printf '%s\n' "$raw" | awk -F'~~SECTION~~' '{print $4}')"
    other="$(  printf '%s\n' "$raw" | awk -F'~~SECTION~~' '{print $5}')"

    # Use if/fi (not `[ ] && cmd`) so a trailing empty section
    # doesn't leave the function returning 1, which would trip
    # set -e at the call site under bash.
    printf '## [%s] - %s\n\n' "$version" "$date"
    if [ -n "$added"   ]; then printf '### Added\n%s\n'   "$added";   fi
    if [ -n "$changed" ]; then printf '### Changed\n%s\n' "$changed"; fi
    if [ -n "$fixed"   ]; then printf '### Fixed\n%s\n'   "$fixed";   fi
    if [ -n "$removed" ]; then printf '### Removed\n%s\n' "$removed"; fi
    if [ -n "$other"   ]; then printf '### Other\n%s\n'   "$other";   fi
}

# Insert a fresh entry under "## [Unreleased]" in CHANGELOG.md and
# leave a blank Unreleased section above it. Idempotent for re-runs;
# safe to skip if CHANGELOG.md doesn't exist (returns success).
update_changelog() {
    local version="$1"
    local range="$2"
    local file="CHANGELOG.md"
    [ -f "$file" ] || { info "no $file — skipping changelog update"; return 0; }

    # macOS awk (BWK) rejects newlines in -v values, so stage the
    # entry through a tempfile and getline it inside awk.
    local entry_file out
    entry_file="$(mktemp)"
    out="$(mktemp)"
    render_changelog_entry "$version" "$range" > "$entry_file"
    awk -v entry_file="$entry_file" '
        /^## \[Unreleased\]/ && !done {
            print $0
            print ""
            while ((getline line < entry_file) > 0) print line
            close(entry_file)
            done = 1
            next
        }
        { print }
    ' "$file" > "$out"
    mv "$out" "$file"
    rm -f "$entry_file"
    info "updated CHANGELOG.md with [$version] entry"
}

release_notes() {
    local previous="$1"
    local version="$2"
    local notes_file="$3"
    local range
    range="$(commit_range "$previous")"
    local commit_count
    commit_count="$(git rev-list --count "$range")"

    {
        printf '## PythonOS v%s\n\n' "$version"
        printf '### Validation\n'
        printf -- '- Local validation: `scripts/validate-release.sh`\n'
        printf -- '- CI: green for `%s`\n\n' "$(git rev-parse --short HEAD)"
        printf '### Statistics\n'
        printf -- '- Commits since v%s: %s\n\n' "$previous" "$commit_count"
        # Use the categorized changelog body for the GitHub release notes
        # too — keeps the release page and CHANGELOG.md in lockstep.
        render_changelog_entry "$version" "$range" \
            | sed '1,/^$/d'   # drop the leading "## [version] - date" line
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

    info "syncing main before changelog edit"
    git pull --rebase origin main

    # Update CHANGELOG.md with the categorized commit entry, commit the
    # change as part of the release, then push everything together so the
    # CI run that gates the tag covers the changelog edit too. Skipped
    # cleanly if CHANGELOG.md isn't present.
    local range
    range="$(commit_range "$previous")"
    update_changelog "$version" "$range"
    if ! git diff --quiet -- CHANGELOG.md 2>/dev/null; then
        info "committing CHANGELOG.md update"
        git add CHANGELOG.md
        git commit -m "docs: update CHANGELOG for v$version"
    fi

    info "pushing main"
    git push origin main

    local head_sha
    head_sha="$(git rev-parse HEAD)"
    wait_for_ci "$head_sha"

    # Use a global so the EXIT trap (which runs after main returns) can see
    # the path under `set -u`. local-scoped vars go out of scope before EXIT.
    NOTES_FILE="$(mktemp)"
    trap 'rm -f "${NOTES_FILE:-}"' EXIT
    local notes_file="$NOTES_FILE"
    release_notes "$previous" "$version" "$notes_file"

    info "creating annotated tag $tag"
    git tag -a "$tag" -m "Release $tag"
    git push origin "$tag"

    info "creating GitHub release $tag"
    # Attach the bootable artifacts when present. Both are large binaries;
    # we don't gate the release on their existence, so a CI-only release
    # (no local make run) still works. validate-release.sh and CI together
    # cover correctness; this is just convenience for downloaders.
    local -a assets=()
    [ -f pythonos.iso ]        && assets+=("pythonos.iso")
    [ -f pythonos-arm64.elf ]  && assets+=("pythonos-arm64.elf")
    if [ "${#assets[@]}" -gt 0 ]; then
        info "attaching ${#assets[@]} artifact(s): ${assets[*]}"
        gh release create "$tag" --title "$tag" --notes-file "$notes_file" \
            "${assets[@]}"
    else
        gh release create "$tag" --title "$tag" --notes-file "$notes_file"
    fi

    info "release complete: $tag"
}

main "$@"
