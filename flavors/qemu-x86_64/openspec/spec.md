# QEMU x86_64 virtual machine target

### Requirement: OS boots and exposes TCP REPL

When the `platform.target-runtime=qemu-x86_64` Flavor is selected, the built artifact
SHALL boot inside a QEMU `q35` machine and expose a TCP REPL on guest port 5000.

#### Scenario: First TCP connection succeeds within boot timeout

- **WHEN** the bootable ISO is launched with `qemu-system-x86_64 -cdrom pythonos.iso`
- **THEN** a TCP connection to guest port 5000 (forwarded to host) succeeds within
  90 seconds of QEMU starting
- **AND** the connection receives a Python `>>>` banner

#### Scenario: Hardware acceleration when guest matches host

- **WHEN** the build host is x86_64 with HVF (macOS) or KVM (Linux) available
- **THEN** QEMU uses the accelerator (`-accel hvf` or `-accel kvm`)
- **AND** boot time is substantially faster than software emulation (TCG)

### Requirement: SMP CPUs come online

All CPUs declared via `SMP_CPUS` MUST start and be schedulable by the kernel.

#### Scenario: All APs start

- **WHEN** `SMP_CPUS=N` is set and QEMU starts with `-smp N`
- **THEN** the serial log contains `SMP online N/N` before the REPL prompt
