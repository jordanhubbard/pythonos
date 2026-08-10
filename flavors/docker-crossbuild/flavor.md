---
schema: "literate-ai/flavor-markdown@1"
namespace: "pythonos"
name: "docker-crossbuild"
version: "1.0.0"
display_name: "Docker cross-compilation build system"
primary_axis: "build.system"
target: "docker-crossbuild"
secondary_constraints: []
applicable_capabilities: []
provides:
  - name: "build.policy.docker-crossbuild"
    version: "1.0.0"
    contract: null
requires: []
specification_roots:
  - "openspec/spec.md"
authoring_inputs: []
contributions: []
conflicts: []
co_requisites: []
order_before: []
order_after: []
---
# Docker cross-compilation build system

Select this Flavor when the `build.system` axis requires a hermetic Docker-based
cross-compilation environment. PythonOS uses this to compile a custom CPython 3.14
and a C HAL (boot/hal/libc) for x86_64 and arm64 targets from any host OS.

This is a project-local Flavor defined in `pythonos` and not shipped with the
literate-ai standard catalog. Use COMPOSE-001 (`litai catalog copy`) to import it
into another project that builds with Docker cross-compilation.
