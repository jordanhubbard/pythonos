---
name: "python-portable-application"
description: "Python portable application. Use for Literate AI workflow tasks."
metadata:
  author: "Literate AI maintainers <literate-ai-maintainers@users.noreply.github.com>"
schema: "urn:literate-ai:schema:v1:specification-to-source-skill"
skill_id: "python-portable-application"
version: "1.4.0"
title: "Python portable application"
stages:
  - "generate"
dependencies: []
limitations:
  - "Do not require standard input, a shell, or environment-specific package installation."
  - "Do not rely on repository-local modules unless the execution contract explicitly permits them."
trust: "repository-reviewed"
---
# Python portable application

Implement the selected Python Flavor with the standard library only. Expose a callable main at source/main.py, accept the specified positional arguments directly, return JSON-compatible values, and keep module imports portable across Linux, macOS, and Windows. Its executable wrapper must dispatch the Standard `--litai-test` and `--litai-smoke` modes, otherwise parse one complete UTF-8 JSON arguments array from argv[1], call main(*arguments), and emit only the returned JSON value plus a trailing newline on standard output. Put the native generated-test implementation at source/tests/litai_test.py and keep it independent of source/tests/manifest.json. Before completing generation, locate `python3` and then `python` on PATH, use the first compatible interpreter found to run `source/main.py --litai-test`, and repair every implementation or generated-test failure until that command exits zero and reports every manifest case as passed. This is a generation-time self-check, not lifecycle authority; do not replace, weaken, or claim the later independent build and test phases.
