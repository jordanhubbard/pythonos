# QEMU arm64 virtual machine target

### Requirement: OS boots from ELF kernel on QEMU virt machine

When `platform.target-runtime=qemu-arm64` is selected, the built artifact SHALL be an
ELF kernel image booted directly by QEMU `-machine virt` without a bootloader ISO.

#### Scenario: arm64 ELF boots and reaches REPL

- **WHEN** `make run-arm64` is invoked
- **THEN** `qemu-system-aarch64 -machine virt` loads the ELF directly
- **AND** the TCP REPL on port 5000 becomes reachable within 90 seconds
- **AND** all arm64-specific smoke tests pass (`make test-arm64` exits 0)

### Requirement: GICv3 interrupt controller

The arm64 target uses GICv3 for interrupt routing (not GICv2 or software fallback).

#### Scenario: Timer interrupt fires at expected frequency

- **WHEN** the kernel scheduler tick is configured for 100 Hz
- **THEN** the PIT (or equivalent arm64 timer) fires at ~100 Hz
- **AND** asyncio tasks advance on each tick
