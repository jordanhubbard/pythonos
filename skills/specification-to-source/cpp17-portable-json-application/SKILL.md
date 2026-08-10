---
name: "cpp17-portable-json-application"
description: "C++17 portable JSON application. Use for Literate AI workflow tasks."
metadata:
  author: "Literate AI maintainers <literate-ai-maintainers@users.noreply.github.com>"
schema: "urn:literate-ai:schema:v1:specification-to-source-skill"
skill_id: "cpp17-portable-json-application"
version: "1.4.0"
title: "C++17 portable JSON application"
stages:
  - "generate"
dependencies: []
limitations:
  - "Do not depend on compiler extensions or operating-system-specific APIs."
  - "Do not read the request from standard input or emit logs alongside the JSON result."
trust: "repository-reviewed"
---
# C++17 portable JSON application

Implement the selected C++ Flavor as straightforward, auditable, portable C++17 without third-party libraries. Put the native generated-test implementation at source/tests/litai_test.cpp, expose it through an ordinary header, compile it into the runnable artifact, and dispatch the Standard `--litai-test` and `--litai-smoke` modes before ordinary argument parsing without reading source/tests/manifest.json. Read the complete JSON arguments array from argv[1], validate the shapes needed by the specification, perform the required computation, and write exactly one JSON result to standard output with deterministic key and value semantics. Trace the acceptance invocation and result before finishing. Keep every translation unit internally complete: declare or define every type and function before its first use, include every required standard header, and ensure the resulting source compiles as C++17. Use either one translation unit or ordinary headers plus separately compiled translation units. Never #include a .c, .cc, .cpp, or .cxx implementation file; put declarations and inline definitions in guarded headers and define every externally linked function exactly once. Preserve object lifetimes: make a parser own its input by value, or first copy argv[1] into a named std::string whose lifetime spans the complete parse and pass that storage without retaining it. Portable generated parsers must not use std::string reference or std::string_view data members, and must never construct a non-owning parser directly from argv[1], because the implicit temporary string immediately dangles. For file reads, reject open failure and bad(); do not require eof() after std::istreambuf_iterator consumption because stream-buffer iteration need not set the stream eofbit. Report exceptional failures only on standard error.

Before completing generation, locate the first available C++ compiler from `c++`, `g++`, and `clang++` on `PATH`. Compile every generated `.cpp` translation unit together as C++17 with `source` on the include path, write the temporary executable outside the generated source tree, and run that executable with `--litai-test`. Repair missing files, declarations, definitions, headers, build metadata, test protocol, and behavior until compilation and the complete generated-test run both succeed. Remove the temporary executable afterward. Treat this as a generation-time self-check only; do not weaken, replace, or bypass any later authorized build, test, execution, or acceptance phase.
