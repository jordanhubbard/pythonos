---
schema: "literate-ai/flavor-markdown@1"
namespace: "pythonos"
name: "qemu-arm64"
version: "1.0.0"
display_name: "QEMU arm64 virtual machine target"
primary_axis: "deployment"
target: "qemu-arm64"
secondary_constraints: []
applicable_capabilities: []
provides:
  - name: "runtime.qemu"
    version: "1.0.0"
    contract: null
  - name: "deployment.qemu.arm64"
    version: "1.0.0"
    contract: null
requires: []
specification_roots:
  - "openspec/spec.md"
authoring_inputs: []
contributions: []
conflicts:
  - "qemu-x86_64"
co_requisites: []
order_before: []
order_after: []
---
# QEMU arm64 virtual machine target

Select this Flavor when the target runtime is a QEMU `virt` machine emulating arm64.
PythonOS boots from an ELF kernel image (`pythonos-arm64.elf`) rather than an ISO on
this target. The same TCP REPL interface (guest port 5000) is available.

This Flavor conflicts with `qemu-x86_64`; exactly one target runtime may be selected.

Project-local Flavor. Use COMPOSE-001 to share with other projects targeting QEMU arm64.
