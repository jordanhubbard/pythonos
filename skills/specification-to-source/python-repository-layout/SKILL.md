---
name: "python-repository-layout"
description: "Apply current Python src-layout, packaging, testing, and cache-location conventions to generated Python applications. Use when a selected Component or Flavor produces a Python package, library, service, or CLI."
metadata:
  author: "Literate AI maintainers <literate-ai-maintainers@users.noreply.github.com>"
schema: "urn:literate-ai:schema:v1:specification-to-source-skill"
skill_id: "python-repository-layout"
version: "1.0.1"
title: "Python repository layout"
stages:
  - "plan"
  - "generate"
dependencies:
  - schema: "urn:literate-ai:schema:v1:skill-reference"
    skill_id: "repository-layout"
    version: "1.0.1"
    identity:
      schema: "urn:literate-ai:schema:v1:content-identity"
      algorithm: "sha256"
      digest: "6be5f341c7f0917151ecacd447af269ddd1844d572ed25b1dd5f5afabd46bb6b"
limitations:
  - "Do not place importable product packages or project-specific build-backend modules directly at the repository root."
  - "Do not depend on the current working directory making undeclared root modules importable."
  - "Do not write __pycache__, bytecode, wheel staging, coverage data, or virtual environments into authored package directories."
trust: "repository-reviewed"
---
# Python repository layout

Use a `src/` layout for importable product code: packages live beneath
`src/<import_name>/`, tests live beneath `tests/`, and packaging plus tool configuration
lives in the root `pyproject.toml`. Declare console entrypoints through
`[project.scripts]`; do not keep executable product modules at the repository root or
rely on the working directory appearing first on `sys.path`. Test the installed or
editable distribution so missing package data and accidental root imports fail early.

A project-specific PEP 517 backend may be in-tree when required, but place it in a
dedicated authored tool directory such as `tools/build_backend/` and select it through
`build-system.backend-path`. Its distribution-root calculation must remain correct from
both a checkout and an sdist. Keep ordinary product modules under `src/`; an in-tree
backend is packaging tooling, not application source.

Configure wheel/build staging, bytecode, test caches, coverage output, and other derived
Python state beneath `OBJ_DIR`/`_build` where the corresponding tool supports a build
root. Create each framework- or agent-managed virtual environment beneath
`OBJ_DIR/python-envs/<validated-session-id>` and propagate that session identity through
nested build commands. Parallel sessions must never install into, replace, or clean the
same environment. A conventional ignored root `.venv/` may remain only as explicitly
user-owned state; do not create or mutate it automatically. Never create or import a
root-level Python module merely to make source-tree execution convenient; use an
editable install, an explicit `PYTHONPATH=src` development command, or the installed
console entrypoint.
