#include "pit.h"
#include "io.h"

void pit_init(uint32_t hz) {
    uint32_t divisor = PIT_BASE_HZ / hz;
    // Channel 0, lo/hi byte access, mode 2 (rate generator)
    outb(PIT_COMMAND, 0x34);
    outb(PIT_CHANNEL0, (uint8_t)(divisor & 0xFF));
    outb(PIT_CHANNEL0, (uint8_t)(divisor >> 8));
    // idt_init() intentionally masks every legacy PIC line. The timer is a
    // core clock source, so unlike device drivers it must unmask its own IRQ.
    // Without this, time.monotonic() remains 0 and every asyncio.sleep()
    // scheduled after boot waits forever.
    uint8_t mask = inb(0x21);
    outb(0x21, (uint8_t)(mask & ~0x01u));
}
