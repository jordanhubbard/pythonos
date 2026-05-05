FROM python:3.14-slim-bookworm

# python:3.14-slim-bookworm ships Python 3.14 at /usr/local/bin/python3.14.
# We use Debian Bookworm's package set for the cross-compilation toolchain and
# image tools, all of which are identical to the Ubuntu 24.04 packages we used
# before. This avoids the deadsnakes PPA launchpadlib failure inside Docker
# (add-apt-repository tries to connect to the Launchpad web API, which is
# blocked in container builds). grub-pc-bin is amd64-only in Debian; arm64
# builds do not need it because they produce a QEMU -kernel ELF, not an x86 ISO.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc-x86-64-linux-gnu \
    binutils-x86-64-linux-gnu \
    gcc-aarch64-linux-gnu \
    binutils-aarch64-linux-gnu \
    libc6-dev-arm64-cross \
    linux-libc-dev-arm64-cross \
    nasm \
    grub-common \
    xorriso \
    mtools \
    e2fsprogs \
    curl \
    ca-certificates \
    && if apt-cache show grub-pc-bin >/dev/null 2>&1; then \
        apt-get install -y --no-install-recommends grub-pc-bin; \
    fi \
    && rm -rf /var/lib/apt/lists/*

# Cross-compiler shims — the Makefile uses x86_64-elf-* but Debian provides
# x86_64-linux-gnu-*. We alias them so the Makefile works unmodified.
RUN ln -s /usr/bin/x86_64-linux-gnu-gcc     /usr/local/bin/x86_64-elf-gcc     && \
    ln -s /usr/bin/x86_64-linux-gnu-ld      /usr/local/bin/x86_64-elf-ld      && \
    ln -s /usr/bin/x86_64-linux-gnu-objcopy /usr/local/bin/x86_64-elf-objcopy && \
    ln -s /usr/bin/x86_64-linux-gnu-ar      /usr/local/bin/x86_64-elf-ar      && \
    ln -s /usr/bin/x86_64-linux-gnu-ranlib  /usr/local/bin/x86_64-elf-ranlib

# arm64 cross-compiler shims — Makefile uses aarch64-elf-* names.
RUN ln -s /usr/bin/aarch64-linux-gnu-gcc     /usr/local/bin/aarch64-elf-gcc     && \
    ln -s /usr/bin/aarch64-linux-gnu-ld      /usr/local/bin/aarch64-elf-ld      && \
    ln -s /usr/bin/aarch64-linux-gnu-objcopy /usr/local/bin/aarch64-elf-objcopy && \
    ln -s /usr/bin/aarch64-linux-gnu-ar      /usr/local/bin/aarch64-elf-ar      && \
    ln -s /usr/bin/aarch64-linux-gnu-ranlib  /usr/local/bin/aarch64-elf-ranlib

WORKDIR /work
