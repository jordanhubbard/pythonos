---
name: "javascript-portable-json-application"
description: "JavaScript portable JSON application. Use for Literate AI workflow tasks."
metadata:
  author: "Literate AI maintainers <literate-ai-maintainers@users.noreply.github.com>"
schema: "urn:literate-ai:schema:v1:specification-to-source-skill"
skill_id: "javascript-portable-json-application"
version: "1.4.1"
title: "JavaScript portable JSON application"
stages:
  - "generate"
dependencies: []
limitations:
  - "Do not use npm packages, network access, standard input, a shell, eval, dynamic code loading, timers, locale-sensitive ordering, or operating-system-specific APIs."
  - "Do not emit logs or presentation text alongside the single JSON result on standard output."
  - "Do not embed acceptance-oracle values or special-case known invocation payloads."
trust: "repository-reviewed"
---
# JavaScript portable JSON application

Implement the selected JavaScript Flavor as direct, auditable JavaScript for Node.js 20 or newer using built-in modules only. Unless the Component declares a role-specific path, write the complete application to source/main.js and put the native generated-test implementation at source/tests/litai_test.js. Dispatch the Standard `--litai-test` and `--litai-smoke` modes before parsing exactly one UTF-8 JSON array containing the application arguments from process.argv[2]; keep the test implementation independent of source/tests/manifest.json. Validate every shape used by the specification, compute with deterministic integer and string semantics, and write exactly one JSON result to standard output. When a Component explicitly declares a multi-toolchain frontend role, honor its source path and CLI contract exactly; use child_process.spawnSync without a shell when that contract requires invoking a separately compiled backend, validate the backend exit status and JSON response, and keep application arguments distinct from infrastructure paths. Keep all ordering explicit before JSON.stringify and report exceptional failures only on standard error.

When a Component requires Unicode code-point ordering, do not use default `sort`, `<`,
`>`, or `localeCompare`: JavaScript's relational/default-sort behavior compares UTF-16
code units, while `localeCompare` is locale-sensitive. Implement an explicit comparator
that iterates complete code points, compares each `codePointAt(0)` integer, and places a
shorter exhausted sequence before its longer prefix extension. Use that same comparator
for output ordering and minimum/maximum tie-breaks. Add a generated native test whose
operands distinguish the domains—for example, U+E000 must sort before U+1F600 (`😀`) by
code point even though JavaScript's default UTF-16 comparison orders them oppositely.

Before completing generation, locate `node` on `PATH` and run `node source/main.js --litai-test` directly. Repair the implementation and generated-test protocol until that command exits successfully and reports every selected case exactly once. Treat this as a generation-time self-check only; do not weaken, replace, or bypass any later lifecycle phase.
