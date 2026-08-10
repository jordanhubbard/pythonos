---
schema: "urn:literate-ai:schema:v1:specification-to-source-skill"
skill_id: "pythonos-kernel"
version: "0.1.0"
title: "PythonOS kernel and HAL generation"
stages:
  - "plan"
  - "generate"
dependencies: []
limitations:
  - "STUB — this skill body is not yet complete. The plan below names what it must cover."
  - "Do not attempt generation from this skill until the body is authored and reviewed."
  - "The C HAL (src/boot, src/hal, src/libc) and the CPython dependency are not generated — they are pre-existing authority. This skill covers only the Python kernel layer (kernel/) and the apps layer (apps/)."
trust: "repository-reviewed"
---

# PythonOS kernel and HAL generation — PLAN STUB

This skill body is not yet authored. The following describes what it MUST cover when written.

## What this skill will guide

When fully authored, this skill will guide a coding agent to generate the Python kernel
layer (`kernel/`) and the GUI application layer (`apps/`) from a `component.md` spec.
It will NOT generate the C HAL (`src/boot`, `src/hal`, `src/libc`) or the CPython
dependency — those are pre-existing authority and must be preserved exactly.

## Required coverage (authoring TODO)

- **Boot protocol**: `kernel.boot(mmap, fb_info)` is the entry point called by the
  C HAL. The generated kernel must export this exact symbol with this signature.
- **Asyncio event loop**: the kernel scheduler owns the asyncio event loop. The agent
  must generate a cooperative scheduler that advances on timer ticks (PIT at 100 Hz
  for x86_64, arm64 timer equivalent for arm64).
- **VFS interface**: `kernel.fs.vfs.vfs` must be a module-level singleton. The agent
  must generate read, write, readdir, mount, and open operations compatible with the
  existing smoke test: `vfs is not None` must evaluate True.
- **Shell commands**: `/bin` scripts must be precompiled in `kernel.commands.SCRIPTS`.
  The agent must generate `sysinfo`, `netstat`, and `ps` implementations that satisfy
  the smoke test assertions.
- **TCP REPL server**: the agent must generate a server listening on port 5000 that
  accepts Python expressions and returns `repr()` of the result.
- **App registry**: `apps.registry` must provide a `register(name, main_coro)` API
  that the compositor uses to list and launch apps.

## What the agent must NOT generate

- The C HAL source files in `src/`
- The CPython build system or `deps/` directory
- Any modification to `GNUMakefile` or `Makefile`
- Flavor-specific configuration (that belongs in Flavor files)

## Next steps to complete this skill

1. Survey the existing `kernel/` and `apps/` implementation in depth.
2. Extract all invariants not yet captured in `components/pythonos-core/component.md`.
3. Write the full skill body covering the sections above.
4. Record the skill update as a separate roadmap item after this conversion is complete.
5. Pass `litai project validate` and the NVIDIA SkillEvaluator gate before marking done.
