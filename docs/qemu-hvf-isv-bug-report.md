# Draft bug report — QEMU upstream

Save this for filing at <https://gitlab.com/qemu-project/qemu/-/issues/new>.
Once `glab auth login --hostname gitlab.com` is configured, the report can
also be posted via:

```bash
glab issue create --hostname gitlab.com --repo qemu-project/qemu \
    --title "$(head -1 docs/qemu-hvf-isv-bug-report.md.title)" \
    --description-file docs/qemu-hvf-isv-bug-report.md
```

---

## Title

`hvf.c:2181 assert(isv) fires on GIC distributor MMIO under -accel hvf on Apple Silicon`

## Host environment

- **Operating system:** macOS 15.x (Apple Silicon)
- **Architecture:** aarch64
- **QEMU flavor:** `qemu-system-aarch64`
- **QEMU version:** 11.0.0 (also confirmed against `target/arm/hvf/hvf.c` on `master` as of 2026-05; the `assert(isv)` is at the same site)
- **QEMU command line:**

  ```
  qemu-system-aarch64 -accel hvf -cpu host \
                      -machine virt,gic-version=3 \
                      -m 2G -smp 2 -nographic \
                      -kernel <bare-metal-elf>
  ```

## Emulated/Virtualized environment

- **Operating system:** bare-metal (PythonOS — github.com/jordanhubbard/pythonos)
- **Architecture:** aarch64

## Description of problem

A bare-metal guest under `-accel hvf -cpu host -machine virt,gic-version=3` on Apple Silicon hits the assertion at `target/arm/hvf/hvf.c:2181` (`assert(isv);`) on the very first MMIO access to either:

- the GICv3 distributor at `0x08000000` (e.g. `LDR Wn, [GICD]` for `GICD_CTLR` / `GICD_TYPER`)
- the GICv3 redistributor at `0x080A0000` (e.g. `LDR Wn, [GICR_BASE+0x14]` for `GICR_WAKER`)

The guest is doing a plain 32-bit `LDR`/`STR` to a device-memory page (page-table descriptor with `AttrIdx=0` mapped as Device-nGnRnE). Apple's Hypervisor Framework delivers the resulting stage-2 data abort **without setting the ISV (Instruction Syndrome Valid) bit**, and QEMU's `hvf_handle_exception()` immediately asserts.

The TODO comment a few lines below the assert already acknowledges that ISV-not-set paths exist (the comment specifically calls out SIMD/SVE):

```c
/*
 * TODO: ISV will be 0 for SIMD or SVE accesses.
 * Inject the exception into the guest.
 */
assert(!s1ptw);
...
assert(isv);    /* line 2181 */
```

This bug extends that TODO to **plain integer LDR/STR to MMIO** under HVF on Apple Silicon — the hypervisor framework simply does not provide ISV for those, regardless of the instruction class.

A previous report ([#2312](https://gitlab.com/qemu-project/qemu/-/issues/2312)) covered the same assertion site triggered by `qemu-xhci`. That issue was closed in Sep 2024, but the underlying assertion is still in `master` and the `qemu-xhci` reproducer was specific to the controller's MMIO pattern. The GIC case is independent.

## Reproducer

Boot script (`launch.sh`):

```bash
#!/usr/bin/env bash
qemu-system-aarch64 \
  -accel hvf -cpu host \
  -machine virt,gic-version=3 \
  -m 2G -smp 2 -nographic \
  -kernel build-arm64/pythonos-arm64.elf
```

The kernel image is a small bare-metal ELF that:

1. Drops to EL1, sets up identity-mapped page tables (4 KiB granule, `MAIR_EL1[0] = 0x00` → Device-nGnRnE for the 0–1 GiB block).
2. Sets `VBAR_EL1`.
3. Calls `gic_init()` which:
   1. Reads `GICD_PIDR2` at `GICD_BASE + 0xFE8` to detect the version (this read **succeeds** under HVF, suggesting it is special-cased or otherwise paged in).
   2. Reads `GICD_PIDR2` at `GICD_BASE + 0xFFE8` to confirm v3 (also **succeeds**).
   3. Wakes the per-CPU redistributor by reading `GICR_BASE + 0x14` (`GICR_WAKER`) — **this is where the assertion fires**.

A minimal C reproducer:

```c
volatile uint32_t *gicr_waker = (volatile uint32_t *)0x080A0014ULL;
uint32_t v = *gicr_waker;     /* triggers QEMU assertion */
```

Reproducer kernel + serial log are reproducible from the PythonOS tree at <https://github.com/jordanhubbard/pythonos>:

```bash
git clone https://github.com/jordanhubbard/pythonos
cd pythonos
make TARGET_ARCH=arm64
ARM64_HVF=1 make run-arm64    # serial log will dead-end after VBAR set
```

## Expected behavior

Per the existing TODO comment in `hvf.c`, the stage-2 abort should be injected into the guest (or otherwise handled) rather than aborting the QEMU process via `assert()`.

If preserving the assertion is preferred for debugging, an alternative would be to detect the Apple-Silicon-without-ISV case and emit a clear error message rather than an opaque `Assertion failed`.

## Actual behavior

```
[PythonOS/arm64] boot: serial OK
[PythonOS/arm64] boot: MMU enabled
[PythonOS/arm64] boot: TLS initialized
[PythonOS/arm64] boot: VBAR set
QEMU 11.0.0 monitor - type 'help' for more information
(qemu) Assertion failed: (isv), function hvf_handle_exception, file hvf.c, line 2181.
Abort trap: 6
```

## Workaround in our project

We default `ARM64_HVF=0` and run under TCG with `-cpu cortex-a57` (GICv2) or `-cpu cortex-a76 -machine virt,gic-version=3` (GICv3 driver test path). The kernel-side GICv3 driver is fully functional under TCG; HVF would be transparently picked up if this assertion were addressed.

## Additional notes

The same kernel ELF boots cleanly under TCG with both:

- `-machine virt -cpu cortex-a57` (GICv2)
- `-machine virt,gic-version=3 -cpu cortex-a76` (GICv3)

so the kernel's GIC code is exercising standard ARM64 patterns, not anything obviously HVF-hostile. If the QEMU team would like reproducer artifacts (the bare-metal ELF, page tables, serial trace), I'm happy to attach.
