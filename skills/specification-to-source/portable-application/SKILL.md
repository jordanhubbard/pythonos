---
name: "portable-application"
description: "Portable complete application generation. Use for Literate AI workflow tasks."
metadata:
  author: "Literate AI maintainers <literate-ai-maintainers@users.noreply.github.com>"
schema: "urn:literate-ai:schema:v1:specification-to-source-skill"
skill_id: "portable-application"
version: "1.5.0"
title: "Portable complete application generation"
stages:
  - "plan"
  - "generate"
dependencies: []
limitations:
  - "Do not read an original implementation or source cache during clean regenerative qualification."
  - "Do not omit a runnable application, generated tests, or build metadata."
  - "Do not introduce platform-specific behavior unless a selected Flavor requires it."
  - "Do not flatten private transitive Component context or turn reusable guidance into product behavior."
trust: "repository-reviewed"
---
# Portable complete application generation

Generate a complete application from the exact accepted specifications and selected Flavors. When a literate specification-context document is present, preserve its node, ancestor, and reference provenance, read each original document once, and surface contradictions rather than silently erasing a normative statement through nearest-wins precedence. Keep this Component's reasoning context bounded to local authority and direct dependencies' public interfaces; never import private or transitive implementation material. Preserve every normative observable behavior, emit all implementation source and current tests, and provide a runnable entrypoint named run. The entrypoint must accept one strict JSON value as its first command-line argument and emit exactly one strict JSON value on standard output.

The runnable artifact SHALL take exactly one command-line argument: a complete UTF-8 JSON **array** of the application's arguments, and SHALL spread that array's elements as the application's positional arguments. A Component whose specification describes "one JSON object as its first argument" therefore receives `[{...}]` on the command line and passes the single object through, rather than receiving the bare object. Emit exactly one JSON result plus a trailing newline on standard output, and send diagnostics only to standard error. This is the same contract the independent acceptance verifier uses to invoke the built artifact, so an implementation that accepts the bare object instead of the array will pass its own generated tests and still fail acceptance.

Before product argument parsing, support `--litai-test` by running every current generated native case. Write `source/tests/manifest.json` as compact canonical JSON with object keys sorted lexicographically and no insignificant whitespace. On complete success this mode must emit exactly one compact JSON object of the form `{"schema":"literate-ai/generated-test-results@1","cases":[{"case_id":"CASE-ID","outcome":"passed"}]}`. Include one unique case entry for every and only case in the manifest, preserve its case IDs exactly, use the literal outcome `passed`, and emit no other standard output. A failed or unexecuted case must make the mode exit nonzero and must never be reported as passed. Do not substitute newline-delimited records, a `passed` Boolean, test-framework prose, or the manifest itself for this protocol.

Support `--litai-smoke` by exercising one current non-acceptance generated example through real application logic and emitting that example's ordinary JSON result on standard output. This mode must produce observable standard output: the Standard lifecycle executes the built artifact in this mode after source custody is gone and rejects a run that emits nothing, so a silent smoke mode fails the lifecycle even when the application is correct. Neither mode is product behavior and neither may read a generated manifest at runtime. Keep diagnostics on standard error. When the Bazel Flavor is selected, expose the application as //:run, the selected command profile's self-contained export as //:litai_artifact, and tests through bazel test //.... Apply reusable source-disposal, test, portability, and build guidance from this skill and selected Flavors without inventing duplicate product requirements. Agent-authored glue may connect selected interfaces but new observable behavior requires a proposed specification change. Do not consult or reproduce an original source checkout: only specifications, Flavors, and exact skills are implementation inputs.
