---
schema: "literate-ai/flavor-markdown@1"
namespace: "pythonos"
name: "qemu-x86_64"
version: "1.0.0"
display_name: "QEMU x86_64 virtual machine target"
primary_axis: "deployment"
target: "qemu-x86_64"
secondary_constraints: []
applicable_capabilities: []
provides:
  - name: "runtime.qemu"
    version: "1.0.0"
    contract: null
  - name: "deployment.qemu.x86_64"
    version: "1.0.0"
    contract: null
requires: []
specification_roots:
  - "openspec/spec.md"
authoring_inputs: []
contributions: []
conflicts:
  - "qemu-arm64"
co_requisites: []
order_before: []
order_after: []
---
# QEMU x86_64 virtual machine target

Select this Flavor when the target runtime is a QEMU `q35` machine emulating x86_64.
PythonOS boots inside this VM from a bootable ISO image. The QEMU instance exposes a
TCP REPL on guest port 5000 (host port 5555) for programmatic interaction and testing.

This Flavor conflicts with `qemu-arm64`; exactly one target runtime may be selected.

Project-local Flavor. Use COMPOSE-001 to share with other projects targeting QEMU x86_64.
