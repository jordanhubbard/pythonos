/*
 * hal.c — Hardware Abstraction Layer Python C extension module
 *
 * Exposes privileged hardware operations to the Python kernel:
 *   - Port I/O (inb/inw/inl/outb/outw/outl)
 *   - Control register access (CR2, CR3)
 *   - MMIO read/write
 *   - Interrupt dispatch bridge (C -> Python)
 *
 * Compiled as a built-in extension module (_hal) and initialized
 * before Py_Initialize() via PyImport_AppendInittab().
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <pthread.h>
#ifndef ARCH_ARM64
#include "../boot/io.h"
#endif
#include "../boot/smp.h"
#include "../linenoise/linenoise.h"

// Python C API callbacks have fixed signatures; many do not use self/args.
#if defined(__GNUC__)
#pragma GCC diagnostic ignored "-Wunused-parameter"
#endif

// ── Port I/O ────────────────────────────────────────────────────────────────

#ifndef ARCH_ARM64
static PyObject *py_inb(PyObject *self, PyObject *args) {
    unsigned int port;
    if (!PyArg_ParseTuple(args, "I", &port)) return NULL;
    return PyLong_FromUnsignedLong(inb((uint16_t)port));
}

static PyObject *py_inw(PyObject *self, PyObject *args) {
    unsigned int port;
    if (!PyArg_ParseTuple(args, "I", &port)) return NULL;
    return PyLong_FromUnsignedLong(inw((uint16_t)port));
}

static PyObject *py_inl(PyObject *self, PyObject *args) {
    unsigned int port;
    if (!PyArg_ParseTuple(args, "I", &port)) return NULL;
    return PyLong_FromUnsignedLong(inl((uint16_t)port));
}

static PyObject *py_outb(PyObject *self, PyObject *args) {
    unsigned int port, val;
    if (!PyArg_ParseTuple(args, "II", &port, &val)) return NULL;
    outb((uint16_t)port, (uint8_t)val);
    Py_RETURN_NONE;
}

static PyObject *py_outw(PyObject *self, PyObject *args) {
    unsigned int port, val;
    if (!PyArg_ParseTuple(args, "II", &port, &val)) return NULL;
    outw((uint16_t)port, (uint16_t)val);
    Py_RETURN_NONE;
}

static PyObject *py_outl(PyObject *self, PyObject *args) {
    unsigned int port, val;
    if (!PyArg_ParseTuple(args, "II", &port, &val)) return NULL;
    outl((uint16_t)port, (uint32_t)val);
    Py_RETURN_NONE;
}
#endif /* !ARCH_ARM64 */

// ── Control registers ────────────────────────────────────────────────────────

#ifndef ARCH_ARM64
static PyObject *py_read_cr2(PyObject *self, PyObject *args) {
    return PyLong_FromUnsignedLongLong(read_cr2());
}

static PyObject *py_read_cr3(PyObject *self, PyObject *args) {
    return PyLong_FromUnsignedLongLong(read_cr3());
}

static PyObject *py_write_cr3(PyObject *self, PyObject *args) {
    unsigned long long val;
    if (!PyArg_ParseTuple(args, "K", &val)) return NULL;
    write_cr3((uint64_t)val);
    Py_RETURN_NONE;
}
#endif /* !ARCH_ARM64 */

// ── arm64 MMIO-based port I/O and control register equivalents ───────────────

#ifdef ARCH_ARM64
static PyObject *py_inb_arm64(PyObject *self, PyObject *args) {
    unsigned long long addr;
    if (!PyArg_ParseTuple(args, "K", &addr)) return NULL;
    return PyLong_FromUnsignedLong(*(volatile uint8_t *)(uintptr_t)addr);
}
static PyObject *py_outb_arm64(PyObject *self, PyObject *args) {
    unsigned long long addr; unsigned int val;
    if (!PyArg_ParseTuple(args, "KI", &addr, &val)) return NULL;
    *(volatile uint8_t *)(uintptr_t)addr = (uint8_t)val;
    Py_RETURN_NONE;
}
/* read_cr2 → FAR_EL1 (fault address) */
static PyObject *py_read_cr2_arm64(PyObject *self, PyObject *args) {
    uint64_t far;
    __asm__ volatile("mrs %0, far_el1" : "=r"(far));
    return PyLong_FromUnsignedLongLong(far);
}
/* read_cr3 → TTBR0_EL1 */
static PyObject *py_read_cr3_arm64(PyObject *self, PyObject *args) {
    uint64_t ttbr;
    __asm__ volatile("mrs %0, ttbr0_el1" : "=r"(ttbr));
    return PyLong_FromUnsignedLongLong(ttbr);
}
/* write_cr3 → TTBR0_EL1 */
static PyObject *py_write_cr3_arm64(PyObject *self, PyObject *args) {
    unsigned long long val;
    if (!PyArg_ParseTuple(args, "K", &val)) return NULL;
    __asm__ volatile("msr ttbr0_el1, %0\nisb" :: "r"((uint64_t)val));
    Py_RETURN_NONE;
}
/* invlpg → TLBI VAE1IS */
static PyObject *py_invlpg_arm64(PyObject *self, PyObject *args) {
    unsigned long long vaddr;
    if (!PyArg_ParseTuple(args, "K", &vaddr)) return NULL;
    __asm__ volatile("tlbi vae1is, %0\ndsb sy\nisb" :: "r"(vaddr >> 12));
    Py_RETURN_NONE;
}
#endif /* ARCH_ARM64 */

// ── Arch-dispatch macros for method table ────────────────────────────────────
#ifdef ARCH_ARM64
#define HAL_INB        py_inb_arm64
#define HAL_OUTB       py_outb_arm64
#define HAL_INW        py_inb_arm64   /* no 16-bit on arm64, map to 8-bit */
#define HAL_OUTW       py_outb_arm64
#define HAL_INL        py_inb_arm64
#define HAL_OUTL       py_outb_arm64
#define HAL_READ_CR2   py_read_cr2_arm64
#define HAL_READ_CR3   py_read_cr3_arm64
#define HAL_WRITE_CR3  py_write_cr3_arm64
#define HAL_INVLPG     py_invlpg_arm64
#else
#define HAL_INB        py_inb
#define HAL_OUTB       py_outb
#define HAL_INW        py_inw
#define HAL_OUTW       py_outw
#define HAL_INL        py_inl
#define HAL_OUTL       py_outl
#define HAL_READ_CR2   py_read_cr2
#define HAL_READ_CR3   py_read_cr3
#define HAL_WRITE_CR3  py_write_cr3
#define HAL_INVLPG     py_invlpg
#endif

// ── MMIO ─────────────────────────────────────────────────────────────────────
// On arm64 io.h is not included, so provide the MMIO helpers inline here.
#ifdef ARCH_ARM64
static inline uint8_t  mmio_read8 (uintptr_t addr) { return *(volatile uint8_t  *)addr; }
static inline uint32_t mmio_read32(uintptr_t addr) { return *(volatile uint32_t *)addr; }
static inline void     mmio_write32(uintptr_t addr, uint32_t v) { *(volatile uint32_t *)addr = v; }
#endif

static PyObject *py_mmio_read8(PyObject *self, PyObject *args) {
    unsigned long long addr;
    if (!PyArg_ParseTuple(args, "K", &addr)) return NULL;
    return PyLong_FromUnsignedLong(mmio_read8((uintptr_t)addr));
}

static PyObject *py_mmio_read32(PyObject *self, PyObject *args) {
    unsigned long long addr;
    if (!PyArg_ParseTuple(args, "K", &addr)) return NULL;
    return PyLong_FromUnsignedLong(mmio_read32((uintptr_t)addr));
}

static PyObject *py_mmio_write32(PyObject *self, PyObject *args) {
    unsigned long long addr;
    unsigned int val;
    if (!PyArg_ParseTuple(args, "KI", &addr, &val)) return NULL;
    mmio_write32((uintptr_t)addr, (uint32_t)val);
    Py_RETURN_NONE;
}

static PyObject *py_mmio_write8(PyObject *self, PyObject *args) {
    unsigned long long addr;
    unsigned int val;
    if (!PyArg_ParseTuple(args, "KI", &addr, &val)) return NULL;
    *(volatile uint8_t *)(uintptr_t)addr = (uint8_t)val;
    Py_RETURN_NONE;
}

// Bulk fill: write `count` 32-bit words starting at `addr` with `val`.
// Equivalent to a memset_pattern4 over MMIO. Replaces per-pixel loops
// in the framebuffer for fill / fill_rect.
static PyObject *py_mmio_fill32(PyObject *self, PyObject *args) {
    unsigned long long addr;
    Py_ssize_t count;
    unsigned int val;
    if (!PyArg_ParseTuple(args, "KnI", &addr, &count, &val)) return NULL;
    volatile uint32_t *p = (volatile uint32_t *)(uintptr_t)addr;
    for (Py_ssize_t i = 0; i < count; i++) p[i] = (uint32_t)val;
    Py_RETURN_NONE;
}

// Bulk write: copy a Python bytes-like buffer into MMIO at `addr` as
// 32-bit words. `src` length must be a multiple of 4. Replaces the
// per-pixel blit loop in Framebuffer.blit.
static PyObject *py_mmio_write_buf32(PyObject *self, PyObject *args) {
    unsigned long long addr;
    Py_buffer buf;
    if (!PyArg_ParseTuple(args, "Ky*", &addr, &buf)) return NULL;
    if (buf.len % 4 != 0) {
        PyBuffer_Release(&buf);
        PyErr_SetString(PyExc_ValueError, "buffer length must be multiple of 4");
        return NULL;
    }
    volatile uint32_t *dst = (volatile uint32_t *)(uintptr_t)addr;
    const uint32_t *src = (const uint32_t *)buf.buf;
    Py_ssize_t words = buf.len / 4;
    for (Py_ssize_t i = 0; i < words; i++) dst[i] = src[i];
    PyBuffer_Release(&buf);
    Py_RETURN_NONE;
}

// In-place fill of a writable buffer (e.g. bytearray) with a 32-bit
// pattern. Avoids the `pixel * (w*h)` allocation pattern used in
// software-surface fill paths — important when the compositor paints a
// full back buffer (3 MB on 1024x768) every frame.
static PyObject *py_buf_fill32(PyObject *self, PyObject *args) {
    Py_buffer buf;
    unsigned int val;
    if (!PyArg_ParseTuple(args, "w*I", &buf, &val)) return NULL;
    if (buf.len % 4 != 0) {
        PyBuffer_Release(&buf);
        PyErr_SetString(PyExc_ValueError, "buffer length must be multiple of 4");
        return NULL;
    }
    uint32_t *p = (uint32_t *)buf.buf;
    Py_ssize_t words = buf.len / 4;
    for (Py_ssize_t i = 0; i < words; i++) p[i] = (uint32_t)val;
    PyBuffer_Release(&buf);
    Py_RETURN_NONE;
}

// Bulk UART transmit. The bridge ships frames up to a few MiB at 30 Hz;
// per-byte Python loops over mmio_write32/outb are too slow. These C
// primitives poll the TX-ready flag once per byte but spend zero time
// in the Python interpreter between bytes.

#ifdef ARCH_ARM64
/* PL011: DR = base + 0x000, FR = base + 0x018, FR.TXFF = bit 5,
 *        FR.RXFE = bit 4. */
static PyObject *py_pl011_write_buf(PyObject *self, PyObject *args) {
    unsigned long long base;
    Py_buffer buf;
    if (!PyArg_ParseTuple(args, "Ky*", &base, &buf)) return NULL;
    volatile uint32_t *dr = (volatile uint32_t *)(uintptr_t)(base + 0x000);
    volatile uint32_t *fr = (volatile uint32_t *)(uintptr_t)(base + 0x018);
    const uint8_t *src = (const uint8_t *)buf.buf;
    for (Py_ssize_t i = 0; i < buf.len; i++) {
        while (*fr & (1u << 5)) { /* TXFF: spin until host drains */ }
        *dr = src[i];
    }
    PyBuffer_Release(&buf);
    Py_RETURN_NONE;
}

/* Tight blocking read of n bytes from a PL011 RX FIFO. Used by
 * synchronous bridge.call() — no asyncio yields, so the scheduler is
 * blocked for the duration of the read. Acceptable because bridge
 * responses are small (typically under 200 bytes). */
static PyObject *py_pl011_read_buf(PyObject *self, PyObject *args) {
    unsigned long long base;
    Py_ssize_t n;
    if (!PyArg_ParseTuple(args, "Kn", &base, &n)) return NULL;
    if (n < 0) {
        PyErr_SetString(PyExc_ValueError, "negative count");
        return NULL;
    }
    PyObject *result = PyBytes_FromStringAndSize(NULL, n);
    if (!result) return NULL;
    char *out = PyBytes_AsString(result);
    volatile uint32_t *dr = (volatile uint32_t *)(uintptr_t)(base + 0x000);
    volatile uint32_t *fr = (volatile uint32_t *)(uintptr_t)(base + 0x018);
    for (Py_ssize_t i = 0; i < n; i++) {
        while (*fr & (1u << 4)) { /* RXFE: spin until byte arrives */ }
        out[i] = (char)(*dr & 0xFF);
    }
    return result;
}
#else
/* 16550: data port = base, LSR = base+5, LSR.THRE = bit 5, LSR.DR = bit 0. */
static PyObject *py_uart16550_write_buf(PyObject *self, PyObject *args) {
    unsigned int base;
    Py_buffer buf;
    if (!PyArg_ParseTuple(args, "Iy*", &base, &buf)) return NULL;
    const uint8_t *src = (const uint8_t *)buf.buf;
    for (Py_ssize_t i = 0; i < buf.len; i++) {
        while ((inb((uint16_t)(base + 5)) & 0x20) == 0) { /* THRE wait */ }
        outb((uint16_t)base, src[i]);
    }
    PyBuffer_Release(&buf);
    Py_RETURN_NONE;
}

static PyObject *py_uart16550_read_buf(PyObject *self, PyObject *args) {
    unsigned int base;
    Py_ssize_t n;
    if (!PyArg_ParseTuple(args, "In", &base, &n)) return NULL;
    if (n < 0) {
        PyErr_SetString(PyExc_ValueError, "negative count");
        return NULL;
    }
    PyObject *result = PyBytes_FromStringAndSize(NULL, n);
    if (!result) return NULL;
    char *out = PyBytes_AsString(result);
    for (Py_ssize_t i = 0; i < n; i++) {
        while ((inb((uint16_t)(base + 5)) & 0x01) == 0) { /* DR wait */ }
        out[i] = (char)(inb((uint16_t)base) & 0xFF);
    }
    return result;
}
#endif

// Per-row in-place fill of a writable buffer: write `count` 32-bit
// words at byte offset `off`. Used by Surface.fill_rect to avoid
// allocating a fresh `pixel * span` bytes object per row.
static PyObject *py_buf_fill32_at(PyObject *self, PyObject *args) {
    Py_buffer buf;
    Py_ssize_t off, count;
    unsigned int val;
    if (!PyArg_ParseTuple(args, "w*nnI", &buf, &off, &count, &val)) return NULL;
    if (off < 0 || count < 0 || (off + count * 4) > buf.len) {
        PyBuffer_Release(&buf);
        PyErr_SetString(PyExc_ValueError, "off/count out of range");
        return NULL;
    }
    uint32_t *p = (uint32_t *)((char *)buf.buf + off);
    for (Py_ssize_t i = 0; i < count; i++) p[i] = (uint32_t)val;
    PyBuffer_Release(&buf);
    Py_RETURN_NONE;
}

// ── High-resolution performance counter ───────────────────────────────────

static uint64_t perf_counter_read(void) {
#ifdef ARCH_ARM64
    uint64_t value;
    __asm__ volatile("mrs %0, cntvct_el0" : "=r"(value));
    return value;
#else
    uint32_t lo, hi;
    __asm__ volatile("lfence; rdtsc" : "=a"(lo), "=d"(hi) :: "memory");
    return ((uint64_t)hi << 32) | lo;
#endif
}

static PyObject *py_perf_counter(PyObject *self, PyObject *args) {
    (void)self; (void)args;
    return PyLong_FromUnsignedLongLong(perf_counter_read());
}

static PyObject *py_perf_frequency(PyObject *self, PyObject *args) {
    (void)self; (void)args;
#ifdef ARCH_ARM64
    uint64_t value;
    __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(value));
    return PyLong_FromUnsignedLongLong(value);
#else
    // TSC frequency is platform-specific. Consumers retain raw cycles rather
    // than reporting a misleading conversion to time.
    return PyLong_FromLong(0);
#endif
}

// ── PIT tick counter (incremented on every timer interrupt before Python dispatch)
extern void pit_tick(void);   // defined in src/libc/time.c (or main_arm64.c on arm64)

// ── TLB ──────────────────────────────────────────────────────────────────────

#ifndef ARCH_ARM64
static PyObject *py_invlpg(PyObject *self, PyObject *args) {
    unsigned long long vaddr;
    if (!PyArg_ParseTuple(args, "K", &vaddr)) return NULL;
    __asm__ volatile ("invlpg (%0)" :: "r"((uintptr_t)vaddr) : "memory");
    Py_RETURN_NONE;
}
#endif /* !ARCH_ARM64 */

// ── Interrupt dispatch bridge ─────────────────────────────────────────────────

// Python-side router and pending interrupt queue. The raw interrupt path cannot
// safely call into Python on SMP/no-GIL builds, so C records events and the
// Python event loop drains them from normal execution context.
static PyObject *interrupt_router   = NULL;
static PyObject *event_loop         = NULL;   // asyncio loop object
static PyObject *call_soon_ts       = NULL;   // loop.call_soon_threadsafe
static void _dbg(const char *s);

#define PENDING_IRQ_CAP 256U
#define PENDING_IRQ_DRAIN_MAX 64U

typedef struct {
    uint64_t vector;
    uint64_t error_code;
    uint64_t rip;
    uint64_t cs;
    uint64_t rflags;
    uint64_t rsp;
} pending_irq_t;

static pending_irq_t pending_irqs[PENDING_IRQ_CAP];
static volatile uint32_t pending_irq_head;
static volatile uint32_t pending_irq_tail;
static volatile uint32_t pending_irq_dropped;

#ifndef ARCH_ARM64
static uint64_t irq_save(void) {
    uint64_t flags;
    __asm__ volatile("pushfq; popq %0; cli" : "=r"(flags) :: "memory");
    return flags;
}

static void irq_restore(uint64_t flags) {
    if (flags & (1ULL << 9)) {
        __asm__ volatile("sti" ::: "memory");
    }
}
#else
static uint64_t irq_save(void) {
    uint64_t flags;
    __asm__ volatile("mrs %0, daif\nmsr daifset, #2" : "=r"(flags) :: "memory");
    return flags;
}

static void irq_restore(uint64_t flags) {
    __asm__ volatile("msr daif, %0" :: "r"(flags) : "memory");
}
#endif

static void queue_interrupt(uint64_t vector, uint64_t error_code,
                            uint64_t rip, uint64_t cs,
                            uint64_t rflags, uint64_t rsp) {
    uint32_t head = pending_irq_head;
    uint32_t next = (head + 1U) % PENDING_IRQ_CAP;
    if (next == pending_irq_tail) {
        pending_irq_dropped++;
        return;
    }

    pending_irqs[head] = (pending_irq_t){
        vector, error_code, rip, cs, rflags, rsp
    };
    __sync_synchronize();
    pending_irq_head = next;
}

static PyObject *py_set_interrupt_router(PyObject *self, PyObject *args) {
    PyObject *router;
    if (!PyArg_ParseTuple(args, "O", &router)) return NULL;
    if (!PyCallable_Check(router)) {
        PyErr_SetString(PyExc_TypeError, "router must be callable");
        return NULL;
    }
    Py_XINCREF(router);
    Py_XDECREF(interrupt_router);
    interrupt_router = router;
    Py_RETURN_NONE;
}

static PyObject *py_set_event_loop(PyObject *self, PyObject *args) {
    PyObject *loop;
    if (!PyArg_ParseTuple(args, "O", &loop)) return NULL;
    Py_XINCREF(loop);
    Py_XDECREF(event_loop);
    event_loop = loop;
    // Cache loop.call_soon_threadsafe for interrupt-context use
    Py_XDECREF(call_soon_ts);
    call_soon_ts = PyObject_GetAttrString(loop, "call_soon_threadsafe");
    Py_RETURN_NONE;
}

static PyObject *py_drain_interrupts(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;

    pending_irq_t local[PENDING_IRQ_DRAIN_MAX];
    uint32_t count = 0;

    uint64_t flags = irq_save();
    while (pending_irq_tail != pending_irq_head && count < PENDING_IRQ_DRAIN_MAX) {
        local[count++] = pending_irqs[pending_irq_tail];
        pending_irq_tail = (pending_irq_tail + 1U) % PENDING_IRQ_CAP;
    }
    irq_restore(flags);

    PyObject *items = PyList_New(count);
    if (!items) {
        return NULL;
    }
    for (uint32_t i = 0; i < count; i++) {
        PyObject *item = Py_BuildValue(
            "(KKKKKK)",
            (unsigned long long)local[i].vector,
            (unsigned long long)local[i].error_code,
            (unsigned long long)local[i].rip,
            (unsigned long long)local[i].cs,
            (unsigned long long)local[i].rflags,
            (unsigned long long)local[i].rsp
        );
        if (!item) {
            Py_DECREF(items);
            return NULL;
        }
        PyList_SET_ITEM(items, i, item);
    }
    return items;
}

// Called from idt.c on every hardware/software interrupt.
// Records the interrupt for later dispatch by the Python event loop.
void interrupt_dispatch_python(uint64_t vector, uint64_t error_code,
                               uint64_t rip, uint64_t cs,
                               uint64_t rflags, uint64_t rsp) {
    // Advance C-side tick counter on every timer interrupt (vector 0x20)
    if (vector == 0x20) pit_tick();

    if (!interrupt_router) return;
    queue_interrupt(vector, error_code, rip, cs, rflags, rsp);
}

// ── Buffer address (for ctypes.addressof equivalent) ─────────────────────────
// Returns the address of the underlying C buffer for bytearray (or subclass).
// On bare metal with identity mapping, this virtual address IS the physical addr.
static PyObject *py_buf_addr(PyObject *self, PyObject *args) {
    PyObject *obj;
    if (!PyArg_ParseTuple(args, "O", &obj)) return NULL;
    if (!PyByteArray_Check(obj)) {
        PyErr_SetString(PyExc_TypeError, "buf_addr requires bytearray");
        return NULL;
    }
    uintptr_t addr = (uintptr_t)PyByteArray_AS_STRING(obj);
    return PyLong_FromUnsignedLongLong((unsigned long long)addr);
}

// ── DMA allocation ────────────────────────────────────────────────────────────
// Allocate zero-filled C-heap memory that Python's GC will never touch.
// Returns the physical address (= virtual on identity-mapped bare metal).
// Memory is never freed — caller is responsible for lifetime.
extern void *calloc(size_t n, size_t size);
static PyObject *py_dma_alloc(PyObject *self, PyObject *args) {
    unsigned long long size;
    if (!PyArg_ParseTuple(args, "K", &size)) return NULL;
    /* VirtIO queues must be page-aligned (pfn = ptr >> 12 must satisfy ptr == pfn*4096).
     * Allocate an extra page so we can round up to the next 4096-byte boundary.
     * The wasted prefix bytes are never returned; no free() so no leak concern. */
    char *raw = (char *)calloc(1, (size_t)size + 4096);
    if (!raw) {
        PyErr_NoMemory();
        return NULL;
    }
    uintptr_t aligned = ((uintptr_t)raw + 4095) & ~(uintptr_t)4095;
    return PyLong_FromUnsignedLongLong((unsigned long long)aligned);
}

static PyObject *py_serial_write(PyObject *self, PyObject *args) {
    const char *text;
    if (!PyArg_ParseTuple(args, "s", &text)) return NULL;
    _dbg(text);
    Py_RETURN_NONE;
}

// ── C-level pthread smoke test ────────────────────────────────────────────────

static void *pthread_selftest_worker(void *arg) {
    volatile uint64_t *marker = (volatile uint64_t *)arg;
    *marker = 123456789ULL;
    return (void *)(uintptr_t)0x1234ULL;
}

static PyObject *py_pthread_selftest(PyObject *self, PyObject *args) {
    static volatile uint64_t marker;
    marker = 0;

    pthread_t tid;
    int rc = pthread_create(&tid, NULL, pthread_selftest_worker, (void *)&marker);
    if (rc != 0) {
        return Py_BuildValue("(iKK)", rc, 0ULL, 0ULL);
    }

    void *retval = NULL;
    rc = pthread_join(tid, &retval);
    return Py_BuildValue(
        "(iKK)",
        rc,
        (unsigned long long)marker,
        (unsigned long long)(uintptr_t)retval
    );
}

// Exercises the pthread attr / condattr surface that PythonOS exposes for
// source-compatibility, plus a non-NULL-attr pthread_create + join. CPython
// itself does not call into the attr path on PythonOS (see
// docs/pthread-attr-coverage.md), so this helper exists to keep that
// surface verified independently.
static PyObject *py_pthread_attr_selftest(PyObject *self, PyObject *args) {
    int cases = 0;
    const char *fail = NULL;

    do {
        // attr_init / destroy round-trip with valid pointers.
        pthread_attr_t attrs;
        if (pthread_attr_init(&attrs) != 0) { fail = "attr_init"; break; }
        cases++;
        if (pthread_attr_destroy(&attrs) != 0) { fail = "attr_destroy"; break; }
        cases++;

        // attr_init rejects NULL.
        if (pthread_attr_init(NULL) != EINVAL) { fail = "attr_init(NULL) accepted"; break; }
        cases++;

        // detachstate accepts known values.
        if (pthread_attr_init(&attrs) != 0) { fail = "attr_init/2"; break; }
        if (pthread_attr_setdetachstate(&attrs, PTHREAD_CREATE_JOINABLE) != 0) {
            fail = "setdetachstate(JOINABLE)"; break;
        }
        cases++;
        if (pthread_attr_setdetachstate(&attrs, PTHREAD_CREATE_DETACHED) != 0) {
            fail = "setdetachstate(DETACHED)"; break;
        }
        cases++;
        if (pthread_attr_setdetachstate(&attrs, 99) != EINVAL) {
            fail = "setdetachstate(99) accepted"; break;
        }
        cases++;

        // stacksize: under 32 KiB rejected, valid round-trips through getter.
        if (pthread_attr_setstacksize(&attrs, 1024) != EINVAL) {
            fail = "setstacksize(1024) accepted"; break;
        }
        cases++;
        if (pthread_attr_setstacksize(&attrs, 131072) != 0) {
            fail = "setstacksize(131072)"; break;
        }
        cases++;
        size_t got = 0;
        if (pthread_attr_getstacksize(&attrs, &got) != 0 || got != 131072) {
            fail = "getstacksize round-trip"; break;
        }
        cases++;
        if (pthread_attr_getstacksize(NULL, &got) != EINVAL) {
            fail = "getstacksize(NULL,&) accepted"; break;
        }
        cases++;
        if (pthread_attr_getstacksize(&attrs, NULL) != EINVAL) {
            fail = "getstacksize(&,NULL) accepted"; break;
        }
        cases++;

        // Non-NULL-attr pthread_create + join, exercising the path CPython
        // *would* use if THREAD_STACK_SIZE were defined.
        if (pthread_attr_setdetachstate(&attrs, PTHREAD_CREATE_JOINABLE) != 0) {
            fail = "setdetachstate(JOINABLE)/2"; break;
        }
        static volatile uint64_t attr_marker;
        attr_marker = 0;
        pthread_t tid;
        if (pthread_create(&tid, &attrs, pthread_selftest_worker,
                           (void *)&attr_marker) != 0) {
            fail = "create with attr"; break;
        }
        cases++;
        void *retval = NULL;
        if (pthread_join(tid, &retval) != 0) {
            fail = "join with attr"; break;
        }
        cases++;
        if (attr_marker != 123456789ULL || (uintptr_t)retval != 0x1234ULL) {
            fail = "attr worker marker"; break;
        }
        cases++;
        if (pthread_attr_destroy(&attrs) != 0) { fail = "attr_destroy/2"; break; }
        cases++;

        // condattr surface is no-op stubs in our build (CONDATTR_MONOTONIC
        // is not defined). Verify the calls succeed and accept the
        // documented clock values.
        pthread_condattr_t ca;
        if (pthread_condattr_init(&ca) != 0) { fail = "condattr_init"; break; }
        cases++;
        // Accepts CLOCK_REALTIME and CLOCK_MONOTONIC silently (stub).
        if (pthread_condattr_setclock(&ca, CLOCK_REALTIME) != 0) {
            fail = "condattr_setclock(REALTIME)"; break;
        }
        cases++;
        if (pthread_condattr_setclock(&ca, CLOCK_MONOTONIC) != 0) {
            fail = "condattr_setclock(MONOTONIC)"; break;
        }
        cases++;
        if (pthread_condattr_destroy(&ca) != 0) { fail = "condattr_destroy"; break; }
        cases++;

        // Mutex attr surface (used by CPython for PyThread_type_lock mutex
        // init via NULL attr; settype is called in some paths).
        pthread_mutexattr_t ma;
        if (pthread_mutexattr_init(&ma) != 0) { fail = "mutexattr_init"; break; }
        cases++;
        if (pthread_mutexattr_settype(&ma, PTHREAD_MUTEX_NORMAL) != 0) {
            fail = "mutexattr_settype"; break;
        }
        cases++;
        if (pthread_mutexattr_destroy(&ma) != 0) { fail = "mutexattr_destroy"; break; }
        cases++;
    } while (0);

    if (fail) {
        return Py_BuildValue("(is)", cases, fail);
    }
    return Py_BuildValue("(iO)", cases, Py_None);
}

// ── linenoise wrappers ───────────────────────────────────────────────────────
//
// linenoise (vendored at src/linenoise/) is a small BSD-licensed line
// editor; on a real tty its blocking linenoise(prompt) does prompt
// redraw, history navigation, etc. PythonOS exposes the non-blocking
// utility surface here so Python code can manage history and clear the
// screen; the blocking call is a thin wrapper that returns the
// completed line (or None on EOF/Ctrl-C). Reading the input bytes
// requires a kernel-installed stdin callback (see
// libc_set_stdin_byte_reader in src/libc/syscalls.c) — without one the
// blocking call returns immediately via the linenoiseNoTTY fallback.

static PyObject *py_linenoise(PyObject *self, PyObject *args) {
    const char *prompt = "";
    if (!PyArg_ParseTuple(args, "|s", &prompt)) return NULL;
    char *line = linenoise(prompt);
    if (!line) Py_RETURN_NONE;
    PyObject *res = PyUnicode_FromString(line);
    linenoiseFree(line);
    return res;
}

static PyObject *py_linenoise_history_add(PyObject *self, PyObject *args) {
    const char *line;
    if (!PyArg_ParseTuple(args, "s", &line)) return NULL;
    return PyLong_FromLong(linenoiseHistoryAdd(line));
}

static PyObject *py_linenoise_history_set_max_len(PyObject *self,
                                                   PyObject *args) {
    int n;
    if (!PyArg_ParseTuple(args, "i", &n)) return NULL;
    return PyLong_FromLong(linenoiseHistorySetMaxLen(n));
}

static PyObject *py_linenoise_clear_screen(PyObject *self, PyObject *args) {
    (void)args;
    linenoiseClearScreen();
    Py_RETURN_NONE;
}

static PyObject *py_linenoise_set_multi_line(PyObject *self, PyObject *args) {
    int ml;
    if (!PyArg_ParseTuple(args, "i", &ml)) return NULL;
    linenoiseSetMultiLine(ml);
    Py_RETURN_NONE;
}

// ── Non-blocking linenoise edit (cooperatively driven from asyncio) ────────
//
// Multi-session support: the kernel can have several Shell instances
// running concurrently (kshell on serial + N TCP REPL sessions + an
// example script driving its own line edit). Each gets its own
// linenoiseState slot. The libc read(0)/write(1) hooks remain global
// (linenoise calls them by fd, not by state pointer) but switch to
// the *currently feeding* session for the duration of each
// linenoiseEditFeed call. Sessions that aren't currently feeding
// don't disturb each other's pending-byte buffer.

extern void libc_set_stdin_byte_reader(int (*fn)(void));
extern void libc_set_stdout_write_hook(int (*fn)(int, const char *, size_t));

#define LN_MAX_SESSIONS 8
#define LN_BUF_SIZE 2048

typedef struct {
    int in_use;
    struct linenoiseState state;
    char buf[LN_BUF_SIZE];
    int pending_byte;            /* -1 when no byte queued */
    PyObject *write_callback;    /* owned reference */
    PyObject *completion_callback; /* owned reference */
} ln_session_t;

static ln_session_t ln_sessions[LN_MAX_SESSIONS];
static int ln_active_session = -1;  /* index of session feeding right now */
static int ln_global_hooks_installed = 0;

static int ln_async_byte_reader(void) {
    if (ln_active_session < 0) return -1;
    int b = ln_sessions[ln_active_session].pending_byte;
    ln_sessions[ln_active_session].pending_byte = -1;
    return b;
}

static int ln_async_write_hook(int fd, const char *buf, size_t n) {
    (void)fd;
    if (ln_active_session < 0 || n == 0) return 0;
    PyObject *cb = ln_sessions[ln_active_session].write_callback;
    if (!cb) return 0;
    PyObject *bytes = PyBytes_FromStringAndSize(buf, (Py_ssize_t)n);
    if (!bytes) { PyErr_Clear(); return 0; }
    PyObject *result = PyObject_CallOneArg(cb, bytes);
    Py_DECREF(bytes);
    if (!result) { PyErr_Clear(); return 0; }
    Py_DECREF(result);
    return 1;
}

static void ln_async_completion_callback(const char *buf,
                                         linenoiseCompletions *lc) {
    if (ln_active_session < 0) return;
    PyObject *cb = ln_sessions[ln_active_session].completion_callback;
    if (!cb) return;

    PyObject *arg = PyUnicode_FromString(buf ? buf : "");
    if (!arg) { PyErr_Clear(); return; }
    PyObject *result = PyObject_CallOneArg(cb, arg);
    Py_DECREF(arg);
    if (!result) { PyErr_Clear(); return; }
    if (result == Py_None) {
        Py_DECREF(result);
        return;
    }

    PyObject *iter = PyObject_GetIter(result);
    Py_DECREF(result);
    if (!iter) { PyErr_Clear(); return; }

    PyObject *item;
    while ((item = PyIter_Next(iter)) != NULL) {
        PyObject *text = PyObject_Str(item);
        Py_DECREF(item);
        if (!text) {
            PyErr_Clear();
            continue;
        }
        const char *s = PyUnicode_AsUTF8(text);
        if (s) {
            linenoiseAddCompletion(lc, s);
        } else {
            PyErr_Clear();
        }
        Py_DECREF(text);
    }
    Py_DECREF(iter);
    if (PyErr_Occurred()) PyErr_Clear();
}

static int ln_count_active(void) {
    int n = 0;
    for (int i = 0; i < LN_MAX_SESSIONS; i++) {
        if (ln_sessions[i].in_use) n++;
    }
    return n;
}

static void ln_install_global_hooks(void) {
    if (ln_global_hooks_installed) return;
    libc_set_stdin_byte_reader(ln_async_byte_reader);
    libc_set_stdout_write_hook(ln_async_write_hook);
    linenoiseSetCompletionCallback(ln_async_completion_callback);
    ln_global_hooks_installed = 1;
}

static void ln_uninstall_global_hooks(void) {
    if (!ln_global_hooks_installed) return;
    libc_set_stdin_byte_reader(NULL);
    libc_set_stdout_write_hook(NULL);
    linenoiseSetCompletionCallback(NULL);
    ln_global_hooks_installed = 0;
}

static int ln_alloc_session(void) {
    for (int i = 0; i < LN_MAX_SESSIONS; i++) {
        if (!ln_sessions[i].in_use) return i;
    }
    return -1;
}

static PyObject *py_linenoise_edit_start(PyObject *self, PyObject *args) {
    const char *prompt = "";
    PyObject *write_cb = NULL;
    PyObject *completion_cb = NULL;
    if (!PyArg_ParseTuple(args, "|sOO", &prompt, &write_cb,
                          &completion_cb)) return NULL;

    int slot = ln_alloc_session();
    if (slot < 0) {
        PyErr_SetString(PyExc_RuntimeError,
                        "no free linenoise session slots");
        return NULL;
    }
    ln_session_t *sess = &ln_sessions[slot];
    sess->in_use = 1;
    sess->pending_byte = -1;
    sess->write_callback = NULL;
    sess->completion_callback = NULL;
    if (write_cb && write_cb != Py_None) {
        if (!PyCallable_Check(write_cb)) {
            sess->in_use = 0;
            PyErr_SetString(PyExc_TypeError,
                            "write callback must be callable or None");
            return NULL;
        }
        Py_INCREF(write_cb);
        sess->write_callback = write_cb;
    }
    if (completion_cb && completion_cb != Py_None) {
        if (!PyCallable_Check(completion_cb)) {
            Py_XDECREF(sess->write_callback);
            sess->write_callback = NULL;
            sess->in_use = 0;
            PyErr_SetString(PyExc_TypeError,
                            "completion callback must be callable or None");
            return NULL;
        }
        Py_INCREF(completion_cb);
        sess->completion_callback = completion_cb;
    }

    /* Install global hooks if first session, and route them to this
     * one for the duration of linenoiseEditStart's prompt write. */
    ln_install_global_hooks();
    int prev_active = ln_active_session;
    ln_active_session = slot;

    int rc = linenoiseEditStart(&sess->state, 0, 1, sess->buf,
                                LN_BUF_SIZE, prompt);
    /* Restore previous active session pointer; subsequent feeds will
     * set it explicitly via py_linenoise_edit_feed_byte. */
    ln_active_session = prev_active;

    if (rc != 0) {
        Py_XDECREF(sess->write_callback);
        Py_XDECREF(sess->completion_callback);
        sess->write_callback = NULL;
        sess->completion_callback = NULL;
        sess->in_use = 0;
        if (ln_count_active() == 0) {
            ln_uninstall_global_hooks();
        }
        PyErr_SetString(PyExc_RuntimeError, "linenoiseEditStart failed");
        return NULL;
    }
    return PyLong_FromLong((long)slot);
}

static PyObject *py_linenoise_edit_feed_byte(PyObject *self, PyObject *args) {
    int slot, byte;
    if (!PyArg_ParseTuple(args, "ii", &slot, &byte)) return NULL;
    if (slot < 0 || slot >= LN_MAX_SESSIONS || !ln_sessions[slot].in_use) {
        PyErr_SetString(PyExc_RuntimeError,
                        "linenoise session not started");
        return NULL;
    }
    if (byte < 0 || byte > 255) {
        PyErr_SetString(PyExc_ValueError, "byte out of range");
        return NULL;
    }
    ln_session_t *sess = &ln_sessions[slot];
    sess->pending_byte = byte;

    int prev_active = ln_active_session;
    ln_active_session = slot;
    char *result = linenoiseEditFeed(&sess->state);
    ln_active_session = prev_active;

    if (result == linenoiseEditMore) {
        Py_RETURN_NONE;
    }
    if (result == NULL) {
        PyErr_SetNone(PyExc_EOFError);
        return NULL;
    }
    PyObject *out = PyUnicode_FromString(result);
    linenoiseFree(result);
    return out;
}

static PyObject *py_linenoise_edit_stop(PyObject *self, PyObject *args) {
    int slot;
    if (!PyArg_ParseTuple(args, "i", &slot)) return NULL;
    if (slot < 0 || slot >= LN_MAX_SESSIONS) {
        PyErr_SetString(PyExc_ValueError, "invalid linenoise slot");
        return NULL;
    }
    ln_session_t *sess = &ln_sessions[slot];
    if (sess->in_use) {
        int prev_active = ln_active_session;
        ln_active_session = slot;
        linenoiseEditStop(&sess->state);
        ln_active_session = prev_active;
        sess->in_use = 0;
    }
    Py_XDECREF(sess->write_callback);
    Py_XDECREF(sess->completion_callback);
    sess->write_callback = NULL;
    sess->completion_callback = NULL;
    sess->pending_byte = -1;
    if (ln_count_active() == 0) {
        ln_uninstall_global_hooks();
    }
    Py_RETURN_NONE;
}

// ── pythonos-3yx: lightweight C-callable dispatch via _hal.smp_run_selftest
//
// Submits a no-op runner to each online AP and joins. Useful for
// diagnostics — Python can verify AP responsiveness at runtime without
// going through _thread.start_new_thread.

static uint64_t hal_smp_runner(void *cpu, void *arg) {
    (void)cpu; (void)arg;
    return 0xC0FFEEUL;
}

static PyObject *py_smp_run_selftest(PyObject *self, PyObject *args) {
    (void)args;
    uint32_t executed = 0;
    uint32_t online = smp_online_count();
    if (online > 1) {
        for (uint32_t i = 1; i < online; i++) {
            uint64_t handle = 0;
            if (!smp_submit_worker(hal_smp_runner, NULL, &handle)) {
                continue;
            }
            uint64_t result = 0;
            if (smp_join_worker(handle, &result) && result == 0xC0FFEEUL) {
                executed++;
            }
        }
    }
    return Py_BuildValue("(II)",
                         (unsigned int)executed,
                         (unsigned int)smp_cpu_count());
}

// ── Module definition ─────────────────────────────────────────────────────────

static PyMethodDef hal_methods[] = {
    {"perf_counter", py_perf_counter, METH_NOARGS,
     "Return a monotonic hardware performance-counter value."},
    {"perf_frequency", py_perf_frequency, METH_NOARGS,
     "Return counter ticks/second when architecturally known, else 0."},
    {"inb",                  HAL_INB,                 METH_VARARGS, "Read byte from I/O port"},
    {"inw",                  HAL_INW,                 METH_VARARGS, "Read word from I/O port"},
    {"inl",                  HAL_INL,                 METH_VARARGS, "Read dword from I/O port"},
    {"outb",                 HAL_OUTB,                METH_VARARGS, "Write byte to I/O port"},
    {"outw",                 HAL_OUTW,                METH_VARARGS, "Write word to I/O port"},
    {"outl",                 HAL_OUTL,                METH_VARARGS, "Write dword to I/O port"},
    {"read_cr2",             HAL_READ_CR2,            METH_VARARGS, "Read CR2 / FAR_EL1 (fault address)"},
    {"read_cr3",             HAL_READ_CR3,            METH_VARARGS, "Read CR3 / TTBR0_EL1 (page table base)"},
    {"write_cr3",            HAL_WRITE_CR3,           METH_VARARGS, "Write CR3 / TTBR0_EL1"},
    {"mmio_read8",           py_mmio_read8,           METH_VARARGS, "MMIO read byte"},
    {"mmio_read32",          py_mmio_read32,          METH_VARARGS, "MMIO read dword"},
    {"mmio_write32",         py_mmio_write32,         METH_VARARGS, "MMIO write dword"},
    {"mmio_write8",          py_mmio_write8,          METH_VARARGS, "MMIO write byte"},
    {"mmio_fill32",          py_mmio_fill32,          METH_VARARGS, "MMIO fill: write `count` 32-bit words with the same value"},
    {"mmio_write_buf32",     py_mmio_write_buf32,     METH_VARARGS, "MMIO bulk write: copy a 4-byte-multiple bytes-like buffer as 32-bit words"},
    {"buf_fill32",           py_buf_fill32,           METH_VARARGS, "Fill a writable buffer with a 32-bit pattern (in-place, no alloc)"},
    {"buf_fill32_at",        py_buf_fill32_at,        METH_VARARGS, "Fill `count` 32-bit words at byte offset `off` of a writable buffer"},
#ifdef ARCH_ARM64
    {"pl011_write_buf",      py_pl011_write_buf,      METH_VARARGS, "Bulk transmit a bytes-like through a PL011 UART (TXFF-polled)"},
    {"pl011_read_buf",       py_pl011_read_buf,       METH_VARARGS, "Bulk receive `n` bytes from a PL011 UART (RXFE-polled, blocking)"},
#else
    {"uart16550_write_buf",  py_uart16550_write_buf,  METH_VARARGS, "Bulk transmit a bytes-like through a 16550 UART (THRE-polled)"},
    {"uart16550_read_buf",   py_uart16550_read_buf,   METH_VARARGS, "Bulk receive `n` bytes from a 16550 UART (DR-polled, blocking)"},
#endif
    {"invlpg",               HAL_INVLPG,              METH_VARARGS, "Invalidate TLB entry"},
    {"set_interrupt_router", py_set_interrupt_router, METH_VARARGS, "Register Python interrupt dispatcher"},
    {"set_event_loop",       py_set_event_loop,       METH_VARARGS, "Register asyncio event loop for threadsafe dispatch"},
    {"drain_interrupts",     py_drain_interrupts,     METH_NOARGS,  "Drain pending hardware interrupts"},
    {"buf_addr",             py_buf_addr,             METH_VARARGS, "Return physical address of a buffer object's data"},
    {"dma_alloc",            py_dma_alloc,            METH_VARARGS, "Allocate zero-filled C-heap DMA buffer, return physical address"},
    {"serial_write",         py_serial_write,         METH_VARARGS, "Write raw text to the early serial console"},
    {"pthread_selftest",     py_pthread_selftest,     METH_NOARGS,  "Run a C-level pthread_create/join smoke test"},
    {"pthread_attr_selftest",py_pthread_attr_selftest,METH_NOARGS,  "Exercise pthread_attr_* / pthread_condattr_* / pthread_mutexattr_* surface"},
    {"linenoise",            py_linenoise,            METH_VARARGS, "Read a line with linenoise editing (returns None on EOF)"},
    {"linenoise_history_add",py_linenoise_history_add,METH_VARARGS, "Append a line to linenoise history"},
    {"linenoise_history_set_max_len", py_linenoise_history_set_max_len, METH_VARARGS, "Set linenoise history capacity"},
    {"linenoise_clear_screen", py_linenoise_clear_screen, METH_NOARGS, "Clear the terminal screen via VT100 escapes"},
    {"linenoise_set_multi_line", py_linenoise_set_multi_line, METH_VARARGS, "Enable/disable linenoise multi-line edit mode"},
    {"linenoise_edit_start", py_linenoise_edit_start, METH_VARARGS, "Begin a non-blocking linenoise edit (prompt, write_callback, completion_callback)"},
    {"linenoise_edit_feed_byte", py_linenoise_edit_feed_byte, METH_VARARGS, "Feed one input byte; returns the completed line (Enter), '' (EOF), or None for more"},
    {"linenoise_edit_stop", py_linenoise_edit_stop, METH_VARARGS, "End the non-blocking linenoise edit (slot)"},
    {"smp_run_selftest", py_smp_run_selftest, METH_NOARGS, "Run a worker self-test on each online AP (returns (executed, total_cpus))"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef hal_module = {
    PyModuleDef_HEAD_INIT,
    .m_name = "_hal",
    .m_doc = NULL,
    .m_size = -1,
    .m_methods = hal_methods,
};

PyMODINIT_FUNC PyInit__hal(void) {
    PyObject *m = PyModule_Create(&hal_module);
    if (!m) return NULL;
#ifdef Py_GIL_DISABLED
    PyUnstable_Module_SetGIL(m, Py_MOD_GIL_NOT_USED);
#endif
#ifdef ARCH_ARM64
    PyModule_AddStringConstant(m, "ARCH", "arm64");
#else
    PyModule_AddStringConstant(m, "ARCH", "x86_64");
#endif
    PyModule_AddIntConstant(m, "SMP_CPUS", (long)smp_cpu_count());
    PyModule_AddIntConstant(m, "SMP_ONLINE", (long)smp_online_count());
    PyModule_AddIntConstant(m, "SMP_WORKERS", (long)smp_worker_selftest_count());
    PyModule_AddIntConstant(m, "BSP_APIC_ID", (long)smp_bsp_apic_id());
#ifdef Py_GIL_DISABLED
    PyModule_AddIntConstant(m, "PY_GIL_DISABLED", 1);
#else
    PyModule_AddIntConstant(m, "PY_GIL_DISABLED", 0);
#endif
    return m;
}

// ── Python kernel entry point ─────────────────────────────────────────────────

typedef struct { uint64_t base; uint64_t length; } mmap_entry_t;

typedef struct {
    uint64_t phys_addr;
    uint32_t pitch;
    uint32_t width;
    uint32_t height;
    uint8_t  bpp;
    uint8_t  type;
    uint8_t  valid;
} framebuffer_info_t;

// Simple serial write for C-level debug (before Python stdout is up)
static void _dbg(const char *s) {
#ifdef ARCH_ARM64
    for (; *s; s++) {
        volatile uint32_t *fr = (volatile uint32_t *)(0x09000018UL);
        volatile uint32_t *dr = (volatile uint32_t *)(0x09000000UL);
        while (*fr & (1U << 5)) {}
        if (*s == '\n') { while (*fr & (1U << 5)) {} *dr = '\r'; }
        *dr = (uint32_t)(unsigned char)*s;
    }
#else
    for (; *s; s++) {
        while ((inb(0x3F8 + 5) & 0x20) == 0) {}
        if (*s == '\n') { while ((inb(0x3F8 + 5) & 0x20) == 0) {} outb(0x3F8, '\r'); }
        outb(0x3F8, (uint8_t)*s);
    }
#endif
}

/* Merge kernel frozen modules with the CPython
 * standard frozen modules (bootstrap, codecs, io, …) before Py_Initialize
 * so that both sets are available during interpreter startup. */
extern void install_frozen_kernel(void);

void python_kernel_start(mmap_entry_t *mmap, int mmap_count,
                         framebuffer_info_t *fb) {
    _dbg("[hal] AppendInittab\n");
    PyImport_AppendInittab("_hal", &PyInit__hal);

    _dbg("[hal] installing frozen modules\n");
    install_frozen_kernel();

    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    config.install_signal_handlers = 0;
#ifdef Py_GIL_DISABLED
    config.tlbc_enabled = 0;
#endif
    _dbg("[hal] Py_Initialize starting\n");
    Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    _dbg("[hal] Py_Initialize done\n");

    // Memory map: list of (base, length) tuples
    PyObject *py_mmap = PyList_New(mmap_count);
    for (int i = 0; i < mmap_count; i++) {
        PyList_SET_ITEM(py_mmap, i,
            Py_BuildValue("(KK)", mmap[i].base, mmap[i].length));
    }

    // Framebuffer info dict (or None if not available)
    PyObject *py_fb;
    if (fb && fb->valid) {
        py_fb = Py_BuildValue(
            "{sKsIsIsIsIsI}",
            "phys_addr", (unsigned long long)fb->phys_addr,
            "pitch",     (unsigned int)fb->pitch,
            "width",     (unsigned int)fb->width,
            "height",    (unsigned int)fb->height,
            "bpp",       (unsigned int)fb->bpp,
            "type",      (unsigned int)fb->type
        );
    } else {
        py_fb = Py_None;
        Py_INCREF(Py_None);
    }

    _dbg("[hal] importing kernel\n");
    PyObject *kernel = PyImport_ImportModule("kernel");
#ifdef ARCH_ARM64
    if (!kernel) { _dbg("[hal] kernel import FAILED\n"); PyErr_Print(); for(;;) __asm__ volatile("wfe"); }
#else
    if (!kernel) { _dbg("[hal] kernel import FAILED\n"); PyErr_Print(); for(;;) __asm__("hlt"); }
#endif
    _dbg("[hal] kernel imported\n");

    PyObject *boot_fn = PyObject_GetAttrString(kernel, "boot");
#ifdef ARCH_ARM64
    if (!boot_fn)  { _dbg("[hal] boot attr FAILED\n"); PyErr_Print(); for(;;) __asm__ volatile("wfe"); }
#else
    if (!boot_fn)  { _dbg("[hal] boot attr FAILED\n"); PyErr_Print(); for(;;) __asm__("hlt"); }
#endif

    _dbg("[hal] calling boot()\n");
    PyObject *result = PyObject_CallFunction(boot_fn, "OO", py_mmap, py_fb);
#ifdef ARCH_ARM64
    if (!result)   { _dbg("[hal] boot() FAILED\n"); PyErr_Print(); for(;;) __asm__ volatile("wfe"); }
#else
    if (!result)   { _dbg("[hal] boot() FAILED\n"); PyErr_Print(); for(;;) __asm__("hlt"); }
#endif

    Py_DECREF(result);
    Py_DECREF(boot_fn);
    Py_DECREF(kernel);
    Py_DECREF(py_mmap);
    Py_DECREF(py_fb);
}

/* posixmodule.c, pwdmodule.c, and PyOS_FSPath are now compiled and linked
 * from CPython's source tree — no stubs needed here. */
