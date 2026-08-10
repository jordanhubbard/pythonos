# Active work

This file is the durable resumption queue for user-directed and discovered work. Before
implementation, follow `skills/agent/record-user-directed-work/SKILL.md`.
Keep detailed designs in focused roadmap documents and link them here.

## P0

### [x] ONBOARD-001 — First project outcome recorded as CONVERT-001

---

### [ ] CONVERT-001 — Derive literate-ai authority from existing pythonos source

- **Owner:** `components/pythonos-core/component.md` + project-local Flavors
- **Direction:** Adopt the existing pythonos codebase into the literate-ai lifecycle.
  The Python harness (`litai init --convert`) has scaffolded the catalog structure.
  This item covers the intelligence layer: deriving Component specs from observed
  behavior, and declaring the project-local Flavors for toolchains not in the
  standard catalog (QEMU, Docker cross-compilation, target architectures).
- **Conclusion:** One Component — `pythonos-core` — covers the full OS because kernel
  and apps can only be built, run, and tested as one bootable artifact (ISO). The
  acceptance oracle is the existing QEMU smoke test suite. Three project-local
  Flavors capture the non-standard toolchain axes: `docker-crossbuild` (build system),
  `qemu-x86_64` and `qemu-arm64` (target runtime). These are project-local Flavors;
  use COMPOSE-001 to share them when a second project needs them.
- **Depends on:** none
- **Implementation:**
  - [x] Harness: `litai init --convert` created catalog structure, merged `.gitignore`,
        preserved existing `CHANGELOG.md`, `AGENTS.md`, `CLAUDE.md`.
  - [x] Author `components/pythonos-core/component.md` — observable behavior from
        README, smoke tests, and shell/scheduler/vfs interfaces.
  - [ ] Author `flavors/docker-crossbuild/flavor.md` + openspec — Docker image as
        build system (replaces raw `make` for cross-compilation).
  - [ ] Author `flavors/qemu-x86_64/flavor.md` + openspec — QEMU x86_64 virt as
        the OS runtime target.
  - [ ] Author `flavors/qemu-arm64/flavor.md` + openspec — QEMU arm64 virt target.
  - [ ] Author `skills/specification-to-source/pythonos-kernel/SKILL.md` — guides
        generation of the kernel/HAL architecture from spec.
  - [ ] Update `literate.project.json` `default_flavor_selectors` to use the
        project-local Flavors: `["+docker-crossbuild", "+qemu-x86_64", "+macos"]`.
- **Evidence:**
  - [ ] `litai verify` passes (authority gate green, skill validates).
  - [ ] `litai lock components/pythonos-core --check` resolves against the declared
        Flavor slots.
  - [ ] `make test` (existing smoke suite) still exits 0 — no regression.

Check the parent only after every required subtask and evidence item passes. Add the
release-visible outcome to `CHANGELOG.md`; Git preserves prior queue states.
