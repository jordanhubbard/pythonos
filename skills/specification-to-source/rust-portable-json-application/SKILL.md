---
name: "rust-portable-json-application"
description: "Rust portable JSON application. Use for Literate AI workflow tasks."
metadata:
  author: "Literate AI maintainers <literate-ai-maintainers@users.noreply.github.com>"
schema: "urn:literate-ai:schema:v1:specification-to-source-skill"
skill_id: "rust-portable-json-application"
version: "1.3.1"
title: "Rust portable JSON application"
stages:
  - "generate"
dependencies: []
limitations:
  - "Do not use third-party crates, Cargo, build scripts, proc macros, unsafe Rust, or operating-system-specific APIs."
  - "Do not read the request from standard input or emit logs alongside the JSON result."
  - "Do not hard-code acceptance examples or assume that the verifier's post-build probe is known during generation."
  - "Do not place the generated runtime test module behind #[cfg(test)], and do not omit source/tests/litai_test.rs from any Bazel target that compiles the crate root which includes it."
trust: "repository-reviewed"
---
# Rust portable JSON application

Implement the selected Rust Flavor as straightforward, auditable Rust 2021 source rooted at source/main.rs and compilable by one direct rustc invocation. Put the native generated-test implementation at source/tests/litai_test.rs, include it unconditionally from the application through an explicit path module, and dispatch the Standard `--litai-test` and `--litai-smoke` modes before ordinary argument parsing without reading source/tests/manifest.json. Do not use `#[cfg(test)]` for this module: the exported runtime artifact must contain it. When Bazel is selected, list both `main.rs` and `tests/litai_test.rs` in every `rust_binary` or `rust_test` that compiles `main.rs`; recursively declare any other module sources as well. Use conventional multiline rustfmt-style structure rather than minified one-line functions, and re-read the finished source for balanced delimiters, complete match arms, and valid character/string escapes. Use only the Rust standard library: do not create Cargo manifests, fetch crates, or depend on serde. Read one complete UTF-8 JSON arguments array from argv[1], validate every shape and domain invariant used by the specification, perform the general computation rather than matching examples, and write exactly one deterministic JSON value plus a trailing newline to standard output. Implement the small JSON reader and string escaper required by the selected contract inside the generated source; correctly handle JSON whitespace, escapes, Unicode escape pairs, signed integer bounds, nested arrays and objects, and rejection of trailing input. Use owned String as the single error type across parser, environment, application, and main-result chains; convert borrowed literals and system errors explicitly before combining Result values with and_then or the question-mark operator. Keep output field ordering stable where the specification makes exact comparison observable. Send concise failures only to standard error and exit nonzero.

For extrema with deterministic string tie-breakers, do not return nested borrowed keys from `min_by_key` or `max_by_key` closures. Compare entries explicitly with `min_by` or `max_by`, or return an owned cloned key, so closure lifetimes are independent and the same source compiles on every supported Rust toolchain.
