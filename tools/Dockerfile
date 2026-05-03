FROM --platform=linux/amd64 python:3.14-slim-bookworm

# python:3.14-slim-bookworm ships Python 3.14 at /usr/local/bin/python3.14.
# We use Debian Bookworm's package set for the cross-compilation toolchain and
# ISO tools, all of which are identical to the Ubuntu 24.04 packages we used
# before. This avoids the deadsnakes PPA launchpadlib failure inside Docker
# (add-apt-repository tries to connect to the Launchpad web API, which is
# blocked in container builds).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc-x86-64-linux-gnu \
    binutils-x86-64-linux-gnu \
    gcc-aarch64-linux-gnu \
    binutils-aarch64-linux-gnu \
    libc6-dev-arm64-cross \
    linux-libc-dev-arm64-cross \
    nasm \
    grub-pc-bin \
    grub-common \
    xorriso \
    mtools \
    e2fsprogs \
    curl \
    ca-certificates \
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
