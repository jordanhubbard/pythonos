/* PythonOS kernel linker script — x86-64 higher-half kernel at 1 MiB */

ENTRY(_start)
OUTPUT_FORMAT(elf64-x86-64)

SECTIONS {
    . = 0x100000;   /* load at 1 MiB — above BIOS reserved region */

    .multiboot2 : {
        KEEP(*(.multiboot2))
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

    /*
     * x86-64 static TLS uses Variant II: the thread pointer lives at %fs:0
     * and compiler TLS variables are addressed at negative offsets from it.
     * Keep .tdata before .tbss so the linker computes TPOFF relocations using
     * the full TLS image size rather than placing .tbss at/after %fs:0.
     */
    .tdata ALIGN(16) : {
        __tls_start = .;
        __tdata_start = .;
        *(.tdata .tdata.* .gnu.linkonce.td.*)
        __tdata_end = .;
    }

    .tbss ALIGN(16) : {
        __tbss_start = .;
        *(.tbss .tbss.* .gnu.linkonce.tb.*)
        *(.tcommon)
        . = ALIGN(16);
        __tbss_end = .;
        __tls_end = .;
    }

    .bss ALIGN(4096) : {
        *(COMMON)
        *(.bss*)
    }

    /* Discard debug info — keeps the image small */
    /DISCARD/ : {
        *(.comment)
        *(.eh_frame)
        *(.note*)
    }
}
