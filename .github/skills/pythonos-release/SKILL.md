---
name: pythonos-release
description: Prepare and publish PythonOS releases, including factual high-energy marketing notes, changelog updates, dual-architecture validation, tags, and GitHub artifacts. Use for release preparation, release-note generation, or publishing a PythonOS version.
---

# PythonOS Release

Produce a release that is technically defensible and entertaining enough that
someone might voluntarily read about an operating-system build.

## Release notes

Before invoking `scripts/release.sh`, determine the next version and inspect the
commits, changelog, tests, and user-visible behavior since the latest `v*` tag.
Rewrite the repository-root `RELEASE-NOTES.md` for that version.

The notes must:

- open with `# PythonOS vX.Y.Z` so the release script can reject stale notes;
- cover the most important user-visible, architectural, performance, teaching,
  game, tooling, and platform changes actually present in the release;
- use maximum marketing energy and extremely high sarcasm by default, while
  remaining factually accurate and never inventing features or test results;
- translate implementation details into benefits, then use jokes to illuminate
  the engineering rather than obscure it;
- include concrete controls, artifacts, platforms, or measurements when they
  make a claim more useful;
- end with an executive summary and a link to the versioned GitHub release;
- remain the long-form companion to `CHANGELOG.md`, not a duplicate dump of
  commit subjects.

Keep `README.md` and `CHANGELOG.md` linked to `RELEASE-NOTES.md`. Update those
links only if paths change.

## Publishing

Follow the repository `AGENTS.md` and use `mac task` for tracking. Publishing is
an external mutation: only tag, push, or create a GitHub release when the user
has requested it.

Run the repository release workflow rather than recreating it manually:

```bash
./scripts/release.sh patch
```

The script requires clean `main`, authenticated `gh`, release notes matching
the prospective version, local host-architecture validation, and green CI for
both x86_64 and ARM64. It publishes both `pythonos.iso` and
`pythonos-arm64.elf`; do not waive either architecture gate.

If a live PythonOS VM holds `build/disk.img`, stop it before local validation
and relaunch it after publication when the user was actively testing the GUI.
Treat a stuck hosted runner as infrastructure trouble: retry CI rather than
bypassing it, and never create the final tag until both jobs pass.
