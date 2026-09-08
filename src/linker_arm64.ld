/* PythonOS arm64 linker script — QEMU virt, RAM at 0x40000000 */

ENTRY(_start)
OUTPUT_FORMAT(elf64-littleaarch64)

SECTIONS {
    . = 0x40100000;   /* 1 MiB above RAM base for safety */

    .text.boot : {
        KEEP(*(.text.boot))
    }

    .text ALIGN(4096) : {
        *(.text*)
    }

    .rodata ALIGN(4096) : {
        *(.rodata*)
    }

    .data ALIGN(4096) : {
        *(.data*)
    }

    .bss ALIGN(4096) : {
        bss_start = .;
        *(COMMON)
        *(.bss*)
        . = ALIGN(16);
        stack_bottom = .;
        . += 4194304;    /* 4 MiB stack */
        stack_top = .;
        bss_end = .;
    }

    /DISCARD/ : {
        *(.comment)
        *(.eh_frame)
        *(.note*)
    }
}
