---
name: "bazel-build-system"
description: "Bazel-preferred build-system design. Use for Literate AI workflow tasks."
metadata:
  author: "Literate AI maintainers <literate-ai-maintainers@users.noreply.github.com>"
schema: "urn:literate-ai:schema:v1:specification-to-source-skill"
skill_id: "bazel-build-system"
version: "1.6.4"
title: "Bazel-preferred build-system design"
stages:
  - "plan"
  - "generate"
dependencies: []
limitations:
  - "Load every language rule symbol from its declared module. For Bazel 9, load cc_binary, cc_library, and cc_test from their @rules_cc//cc:*.bzl files and load py_binary, py_library, and py_test from @rules_python//python:defs.bzl; none is a native symbol."
  - "Do not pass hdrs to rules_cc cc_binary or cc_test. Put shared headers and implementation sources in a cc_library and reference that target through deps, or list private headers in the consumer's srcs."
  - "Never use includes = [\".\"] or any include path that resolves to the Bazel workspace root. Put public headers in a package-local cc_library hdrs attribute, include them by their package-relative path, and make each consumer depend on that library."
  - "Always set main explicitly on every rules_python py_binary and py_test target. The path must name an executable Python source also present in srcs; never rely on target-name-based entrypoint inference."
  - "For aspect_rules_js js_binary and js_test, set entry_point to the executable JavaScript source. These rules do not accept srcs; place additional runtime files in data when needed."
  - "For every rules_rust rust_binary or rust_test, declare every Rust source file reachable through mod or #[path] from the crate root in srcs. A Rust module declaration does not make the file visible inside Bazel's sandbox."
  - "Do not treat this preference or its reviewed ruleset baselines as authority over an explicit Component specification or selected Flavor."
  - "Do not claim fine-grained Bazel dependency tracking when Bazel merely wraps a native build."
  - "Treat every literal bazel_dep version as a requested version subject to Bzlmod Minimal Version Selection, not proof of the final selected version."
  - "Require exactly one source-root MODULE.bazel module() declaration with literal non-empty name and exact Component version fields before any bazel_dep(); omission is an invalid generated candidate."
  - "Do not fabricate MODULE.bazel.lock or a transitive Bzlmod graph; the authorized Bazel builder resolves them in an external projection and replays with lockfile error mode before tests."
  - "Do not invoke Bazel, fetch rules, resolve dependencies, or populate a cache during source generation."
  - "Do not write Bazel output trees or caches into the specification repository or generated source tree."
  - "When the Literate AI Standard lifecycle is selected, //:litai_artifact must produce the exact portable output name for the selected language: Python run.pyz, JavaScript run.js, and C++ or Rust run (run.exe on Windows). Never invent another output name."
  - "Never declare a genrule output with the same package path as an existing executable rule's implicit output. For C++ or Rust, expose //:litai_artifact as an alias of //:run when both contracts require the same run or run.exe artifact; a genrule outs = [\"run\"] conflicts with cc_binary(name = \"run\") or rust_binary(name = \"run\") during Bazel analysis."
  - "A dependency-free Python //:run target must not copy a launcher that imports sibling source or test modules unless the genrule packages those modules into the runnable output; prove that --litai-test works from the Bazel-built artifact without source-tree or runfiles imports."
  - "A dependency-free Python //:run implemented as a native genrule must set executable = True and emit one executable output; a buildable but non-runnable genrule violates the declared target contract."
trust: "repository-reviewed"
---
# Bazel-preferred build-system design

Treat Bazel as a strong build-system preference, meaning a default recommendation in this prompt rather than a runtime mandate. Explicit Component specification text and selected Flavor requirements always take precedence. If they require or justify another build system, follow them and record an honest coherent build design; an ordered -bazel selector removes this Flavor and therefore removes this skill before generation. Otherwise generate a portable Bzlmod workspace with MODULE.bazel and BUILD.bazel, a runnable //:run target, and real test targets so `bazel test //...` and `bazel run //:run -- <application arguments>` exercise the complete application. The source-root MODULE.bazel must start with one keyword-only literal `module(name = "<valid_module_name>", version = "<exact_component_version>")` declaration; both fields are mandatory, use the Component version for `version`, and place only literal keyword-only `bazel_dep(name = ..., version = ...)` declarations after it. Model a fine-grained, hermetic, deterministic rule graph with declared inputs and outputs. Keep Bazel output roots and caches outside both the specification repository and generated source tree.

Also expose `//:litai_artifact` as the Standard lifecycle target. Make it produce exactly one self-contained portable file: `run.pyz` for Python, `run.js` for JavaScript, and `run` for C++ or Rust (`run.exe` on Windows). These are the current Standard language-profile `bazel_output_path` values and are exact output contracts, not suggestions. The artifact must preserve the portable application's ordinary JSON invocation plus `--litai-test` and `--litai-smoke` modes without relying on a Bazel workspace or runfiles after export. A single-file Python application may be copied unchanged to `run.pyz` by a cross-platform genrule (`cmd` plus `cmd_bat`); a multi-file application must create a real self-contained zipapp. Keep `//:run` as the human-facing target and make both targets consume the same application implementation. Never invent a different artifact name or silently return only a launcher whose runfiles remain elsewhere.

When `//:run` already produces the exact required `run` or `run.exe` artifact, make
`//:litai_artifact` an `alias(actual = ":run")`. Do not add a genrule whose
`outs = ["run"]`: Bazel output paths are package-global, so that declaration conflicts
with the implicit output of `cc_binary(name = "run")` or
`rust_binary(name = "run")` before compilation begins.

Do not introduce a Bazel module dependency merely to use language-specific Bazel rules when the locked Standard language command already owns compilation or test execution. In particular, a dependency-free Python Component under the Standard lifecycle MUST use portable `genrule` targets for `run` and `run.pyz`; its BUILD file MUST contain no other rule invocations. It MUST NOT add `rules_python`, `rules_shell`, `py_binary`, `py_library`, `py_test`, or `sh_test`. Bazel 9 does not provide native `sh_test`. The `//:run` genrule MUST set `executable = True` and emit exactly one executable output so `bazel run //:run -- ...` works; `//:litai_artifact` separately emits the exact `run.pyz` lifecycle artifact. The generated test suite remains owned and executed by the Standard test phase, so do not create a redundant Bazel test target in this case. A copied single-file launcher is valid only when it contains the application and generated-test implementation itself. If `--litai-test` imports `source/tests/litai_test.py` or any other sibling module, package the complete module tree into `run.pyz` and make both `//:run` and `//:litai_artifact` execute that zipapp; merely listing files as genrule inputs does not make Python imports available at artifact runtime. Add Bzlmod dependencies only when the Component's actual source or build semantics require them, and describe every such dependency in the source CycloneDX BOM.

Bazel 9 no longer supplies native C++ or Python rules. Use cc_binary/cc_library/cc_test from rules_cc and py_binary/py_library/py_test from rules_python. Use rust_binary/rust_test from rules_rust for Rust, and js_binary plus real test targets from aspect_rules_js for JavaScript. When no Component or selected Flavor pins a compatible ruleset version, use these reviewed Bazel Central Registry baselines: rules_cc 0.2.22, rules_python 2.2.0, rules_rust 0.73.0, and aspect_rules_js 3.3.1. Declare only the rulesets the selected language actually needs. These are build-policy defaults, not authority over a user pin. Never use an undefined native cc_binary, py_binary, rust_binary, or js_binary symbol.

For Rust, treat the crate root's complete module graph as declared inputs. If
`main.rs` contains `#[path = "tests/litai_test.rs"] mod litai_test;`, every
`rust_binary` and `rust_test` that compiles `main.rs` must include both `main.rs` and
`tests/litai_test.rs` in `srcs`. Apply the same rule recursively to ordinary `mod`
declarations and other explicit paths. Bazel does not infer these Rust source files;
omitting one creates an incomplete sandbox even when direct `rustc` can see the file.

Do not confuse two integrations: wrapping a native build under Bazel provides one coarse cached action, while native fine-grained Bazel targets provide materially better dependency tracking. Prefer fine-grained targets when they faithfully preserve ecosystem semantics, but use an honest wrapper or native build when translation would be brittle or misleading.

Elixir/Erlang: Elixir ships with Mix, which handles compilation, dependency resolution, code generation, testing, and OTP application packaging. Bazel can invoke Mix, but reproducing Mix’s dependency and application semantics as fine-grained native Bazel targets is difficult and relatively uncommon.

Zig: Zig’s build.zig files are executable Zig programs that construct an arbitrary build graph. Bazel can compile Zig source directly or run zig build, but translating an arbitrary build.zig program into Bazel targets may be impractical.

Dynamic metaprogramming-heavy ecosystems: Some Lisp, Smalltalk, or image-based environments may compile or generate code based on runtime state, loaded modules, or a persistent system image. Bazel can wrap the process, but fine-grained dependency tracking may be poor.

Projects with opaque code generation: This is usually a project problem rather than a language problem. A native build may discover files dynamically, access the network, modify source directories, inspect undeclared environment variables, or generate outputs whose names are unknown beforehand. Bazel strongly discourages these behaviors.
