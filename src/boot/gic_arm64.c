/*
 * gic_arm64.c — GIC (v2 and v3) init, arm64 generic timer, IRQ dispatch.
 *
 * QEMU virt machine layout (both versions share GICD_BASE):
 *   GICv2: Distributor 0x08000000, CPU interface 0x08010000
 *   GICv3: Distributor 0x08000000, Redistributor 0x080A0000 (per-CPU)
 *
 * GICv3 is mandatory for HVF on Apple Silicon — Apple's Hypervisor
 * Framework only exposes GICv3 to guests. The GICv2 path stays for
 * cross-emulation under TCG (QEMU virt default with cortex-a57) and
 * for any other tooling that prefers it. The kernel detects which
 * version is present via GICD_PIDR2 at boot and dispatches into the
 * matching init / ack / eoi paths.
 *
 * Timer: EL1 physical timer (CNTP), PPI #14 = GIC ID 30.
 * On every tick, interrupt_dispatch_python(0x20, ...) is called so the
 * Python-side IRQ.TIMER handler fires — same vector as the x86 PIT.
 */

#include <stdint.h>


/* ── MMIO bases ────────────────────────────────────────────────────────────── */

#define GICD_BASE  0x08000000UL
#define GICC_BASE  0x08010000UL          /* GICv2 only */
#define GICR_BASE  0x080A0000UL          /* GICv3 redistributor frame 0 */
#define GICR_STRIDE        0x20000UL     /* RD_base + SGI_base, per CPU */
#define GICR_SGI_OFFSET    0x10000UL     /* SGI base inside the per-CPU frame */


static inline uint32_t mmio_r32(uint64_t addr) {
    return *(volatile uint32_t *)addr;
}
static inline void mmio_w32(uint64_t addr, uint32_t v) {
    *(volatile uint32_t *)addr = v;
}
static inline uint64_t mmio_r64(uint64_t addr) {
    return *(volatile uint64_t *)addr;
}
static inline void mmio_w64(uint64_t addr, uint64_t v) {
    *(volatile uint64_t *)addr = v;
}


/* ── Detected version (1 = v2, 3 = v3, 0 = unknown) ──────────────────────── */

static int _gic_version = 0;


/* ── GICv2 helpers ─────────────────────────────────────────────────────────── */

static inline uint32_t gicd_r(uint32_t off) { return mmio_r32(GICD_BASE + off); }
static inline void     gicd_w(uint32_t off, uint32_t v) { mmio_w32(GICD_BASE + off, v); }
static inline void     gicd_w64(uint32_t off, uint64_t v) { mmio_w64(GICD_BASE + off, v); }
static inline uint32_t gicc_r(uint32_t off) { return mmio_r32(GICC_BASE + off); }
static inline void     gicc_w(uint32_t off, uint32_t v) { mmio_w32(GICC_BASE + off, v); }


/* ── Version detection ─────────────────────────────────────────────────────── */

/* GICD_PIDR2 lives at distributor offset 0xFE8 on GICv2 and 0xFFE8 on
 * GICv3. Bits [7:4] hold the architecture version. The GICv2 distributor
 * frame in QEMU virt is only 0x1000 bytes, so reading at 0xFFE8 first
 * would abort in v2 mode. We probe the v2 location first — if it returns
 * version 2, we're done; otherwise the hardware is GICv3 (the v3 frame
 * is 0x10000 and offset 0xFE8 is unallocated, reading as 0). */
static int gic_detect_version(void) {
    uint32_t pidr_v2 = gicd_r(0x0FE8);
    int v = (pidr_v2 >> 4) & 0xF;
    if (v == 2)
        return 2;
    /* Confirm GICv3 by reading the v3 PIDR2 location. */
    uint32_t pidr_v3 = gicd_r(0xFFE8);
    v = (pidr_v3 >> 4) & 0xF;
    if (v == 3 || v == 4)
        return v;
    return 2;   /* fail closed to v2 */
}


/* ── GICv2 init ────────────────────────────────────────────────────────────── */

static void gic_v2_distributor_init(void) {
    gicd_w(0x000, 0);
    __asm__ volatile("dsb sy");

    uint32_t typer = gicd_r(0x004);
    int n_lines = 32 * ((int)(typer & 0x1F) + 1);

    for (int i = 1; i < n_lines / 32; i++)
        gicd_w(0x180 + i * 4, 0xFFFFFFFF);   /* ICENABLER */
    for (int i = 0; i < n_lines / 4; i++)
        gicd_w(0x400 + i * 4, 0xA0A0A0A0);   /* IPRIORITYR */
    for (int i = 8; i < n_lines / 4; i++)
        gicd_w(0x800 + i * 4, 0x01010101);   /* ITARGETSR: CPU 0 */
    for (int i = 0; i < n_lines / 4; i++)
        gicd_w(0x080 + i * 4, 0x00000000);   /* IGROUPR: group 0 */

    gicd_w(0x000, 1);
}

static void gic_v2_cpu_iface_init(void) {
    gicc_w(0x004, 0xFF);   /* PMR */
    gicc_w(0x008, 0x07);   /* BPR */
    gicc_w(0x000, 1);      /* CTLR: enable */
}


/* ── GICv3 init ────────────────────────────────────────────────────────────── */

/* Each CPU has a redistributor frame at GICR_BASE + cpu * GICR_STRIDE.
 * For now we run on CPU 0 only at boot; APs call gic_cpu_iface_init()
 * which handles its own affinity. */

static uint64_t gicr_rd_base_for_self(void) {
    /* Read MPIDR_EL1 affinity to figure out which redistributor frame
     * belongs to this CPU. The QEMU virt machine packs frames in linear
     * order, indexed by aff0 + aff1 * 16 + aff2 * 256 + aff3 * 4096
     * (good enough for the small SMP topologies we run). */
    uint64_t mpidr;
    __asm__ volatile("mrs %0, mpidr_el1" : "=r"(mpidr));
    uint64_t aff0 = mpidr & 0xFF;
    uint64_t aff1 = (mpidr >> 8) & 0xFF;
    uint64_t aff2 = (mpidr >> 16) & 0xFF;
    uint64_t aff3 = (mpidr >> 32) & 0xFF;
    uint64_t idx = aff0 + (aff1 << 4) + (aff2 << 8) + (aff3 << 12);
    return GICR_BASE + idx * GICR_STRIDE;
}

static inline uint64_t gicr_sgi_base_for_self(void) {
    return gicr_rd_base_for_self() + GICR_SGI_OFFSET;
}

static void gic_v3_redistributor_init_self(void) {
    uint64_t rd  = gicr_rd_base_for_self();
    uint64_t sgi = gicr_sgi_base_for_self();

    /* Wake up this CPU's redistributor: clear ProcessorSleep, then wait
     * for ChildrenAsleep to clear. */
    uint32_t waker = mmio_r32(rd + 0x14);  /* GICR_WAKER */
    waker &= ~(1U << 1);                   /* Clear ProcessorSleep */
    mmio_w32(rd + 0x14, waker);
    while (mmio_r32(rd + 0x14) & (1U << 2)) { /* ChildrenAsleep */ }

    /* SGIs (0..15) and PPIs (16..31) live on the SGI frame. */
    mmio_w32(sgi + 0x080, 0xFFFFFFFF);     /* IGROUPR0: group 1 NS */
    mmio_w32(sgi + 0x180, 0xFFFFFFFF);     /* ICENABLER0: disable all */
    for (int i = 0; i < 8; i++)
        mmio_w32(sgi + 0x400 + i * 4, 0xA0A0A0A0); /* IPRIORITYR */
}

static void gic_v3_distributor_init(void) {
    /* GICv3 setup ordering matters under HVF: write GICD_CTLR with the
     * affinity-routing bits set BEFORE touching any per-IRQ register.
     * If we zero CTLR first, subsequent ICENABLER / IGROUPR writes can
     * trigger an unsynced Apple-hypervisor exception (QEMU 11 asserts
     * "isv" in hvf.c). Going straight to CTLR = ARE_S | ARE_NS lets
     * Apple's GIC virtualization route everything correctly. */
    gicd_w(0x000, (1U << 4) | (1U << 5));   /* ARE_S | ARE_NS */
    __asm__ volatile("dsb sy");

    uint32_t typer = gicd_r(0x004);
    int n_lines = 32 * ((int)(typer & 0x1F) + 1);

    /* Disable all SPIs (32..n_lines-1). */
    for (int i = 1; i < n_lines / 32; i++)
        gicd_w(0x180 + i * 4, 0xFFFFFFFF);   /* ICENABLER */

    /* Priorities for SPIs. */
    for (int i = 8; i < n_lines / 4; i++)
        gicd_w(0x400 + i * 4, 0xA0A0A0A0);   /* IPRIORITYR */

    /* Group 1 NS for all SPIs. */
    for (int i = 1; i < n_lines / 32; i++)
        gicd_w(0x080 + i * 4, 0xFFFFFFFF);   /* IGROUPR */

    /* Route every SPI to MPIDR (aff3:aff2:aff1:aff0) = 0 (CPU 0). */
    for (int i = 32; i < n_lines; i++)
        gicd_w64(0x6000 + i * 8, 0x0);       /* IROUTER<n> */

    /* Now enable group 1 (non-secure) on top of ARE. */
    gicd_w(0x000, (1U << 4) | (1U << 5) | (1U << 1));
    __asm__ volatile("dsb sy; isb");
}

static void gic_v3_cpu_iface_init(void) {
    /* Enable system-register access. */
    uint64_t sre;
    __asm__ volatile("mrs %0, S3_0_C12_C12_5" : "=r"(sre));   /* ICC_SRE_EL1 */
    sre |= 1ULL;
    __asm__ volatile("msr S3_0_C12_C12_5, %0\nisb" :: "r"(sre));

    /* Priority mask: accept everything. */
    __asm__ volatile("msr S3_0_C4_C6_0, %0" :: "r"((uint64_t)0xFFULL));   /* ICC_PMR_EL1 */
    /* Binary point register for group 1. */
    __asm__ volatile("msr S3_0_C12_C12_3, %0" :: "r"((uint64_t)0ULL));    /* ICC_BPR1_EL1 */
    /* Enable group 1 interrupts. */
    __asm__ volatile("msr S3_0_C12_C12_7, %0\nisb" :: "r"((uint64_t)1ULL));/* ICC_IGRPEN1_EL1 */
}


/* ── Public API ────────────────────────────────────────────────────────────── */

/* Per-CPU CPU interface init for application processors. The BSP wires
 * its own per-CPU bits inside gic_init(); APs land here from
 * ap_entry_arm64_c (src/boot/smp_arm64.c). */
void gic_cpu_iface_init(void) {
    if (_gic_version == 3 || _gic_version == 4) {
        gic_v3_redistributor_init_self();
        gic_v3_cpu_iface_init();
    } else {
        gic_v2_cpu_iface_init();
    }
}

void gic_init(void) {
    _gic_version = gic_detect_version();
    if (_gic_version == 3 || _gic_version == 4) {
        /* Wake the BSP's redistributor BEFORE distributor MMIO. */
        gic_v3_redistributor_init_self();
        gic_v3_distributor_init();
        gic_v3_cpu_iface_init();
    } else {
        _gic_version = 2;
        gic_v2_distributor_init();
        gic_v2_cpu_iface_init();
    }
}

void gic_enable_irq(int irq) {
    if (irq < 32 && (_gic_version == 3 || _gic_version == 4)) {
        /* SGI / PPI live on the per-CPU redistributor's SGI frame. */
        uint64_t sgi = gicr_sgi_base_for_self();
        mmio_w32(sgi + 0x100 + (irq / 32) * 4, 1u << (irq % 32));
    } else {
        gicd_w(0x100 + (irq / 32) * 4, 1u << (irq % 32));   /* GICD_ISENABLER */
    }
}


/* ── Ack / EOI ─────────────────────────────────────────────────────────────── */

static uint32_t gic_v3_ack(void) {
    uint64_t iar;
    __asm__ volatile("mrs %0, S3_0_C12_C12_0" : "=r"(iar));   /* ICC_IAR1_EL1 */
    return (uint32_t)(iar & 0xFFFFFF);
}

static void gic_v3_eoi(uint32_t id) {
    __asm__ volatile("msr S3_0_C12_C12_1, %0" :: "r"((uint64_t)id));  /* ICC_EOIR1_EL1 */
    __asm__ volatile("isb");
}

static uint32_t gic_ack(void) {
    if (_gic_version == 3 || _gic_version == 4)
        return gic_v3_ack();
    return gicc_r(0x00C);   /* GICC_IAR */
}

static void gic_eoi(uint32_t id) {
    if (_gic_version == 3 || _gic_version == 4)
        gic_v3_eoi(id);
    else
        gicc_w(0x010, id);   /* GICC_EOIR */
}


/* ── Physical timer (EL1 CNTP, PPI #14 = GIC ID 30) ──────────────────────── */

#define TIMER_IRQ   30
#define TIMER_HZ   100

static uint64_t _timer_interval;

void timer_arm64_init(void) {
    uint64_t freq;
    __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(freq));
    _timer_interval = freq / TIMER_HZ;

    __asm__ volatile("msr cntp_tval_el0, %0" :: "r"(_timer_interval));
    __asm__ volatile("msr cntp_ctl_el0,  %0" :: "r"((uint64_t)1));
    __asm__ volatile("isb");

    gic_enable_irq(TIMER_IRQ);
}

static void timer_arm64_reload(void) {
    __asm__ volatile("msr cntp_tval_el0, %0" :: "r"(_timer_interval));
}


/* ── IRQ dispatch bridge ───────────────────────────────────────────────────── */

extern void interrupt_dispatch_python(uint64_t vector, uint64_t error_code,
                                       uint64_t rip, uint64_t cs,
                                       uint64_t rflags, uint64_t rsp);

/*
 * Called from el1h_irq_handler in boot_arm64.S.
 * Acknowledge the GIC, dispatch to Python, reload the timer, EOI.
 */
void arm64_irq_handler(void) {
    uint32_t id = gic_ack();

    if (id >= 1020) {          /* spurious; 1020–1023 are special */
        if (id != 1023) gic_eoi(id);
        return;
    }

    if (id == TIMER_IRQ) {
        timer_arm64_reload();
        interrupt_dispatch_python(0x20, 0, 0, 0, 0, 0);
    } else {
        interrupt_dispatch_python(0x40 + id, 0, 0, 0, 0, 0);
    }

    gic_eoi(id);
}


/* ── Diagnostic ────────────────────────────────────────────────────────────── */

int gic_version_detected(void) {
    return _gic_version;
}
