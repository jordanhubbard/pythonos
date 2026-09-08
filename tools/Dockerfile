FROM python:3.14-slim-bookworm

# python:3.14-slim-bookworm ships Python 3.14 at /usr/local/bin/python3.14.
# We use Debian Bookworm's package set for the cross-compilation toolchain
# and image tools, all of which are identical to the Ubuntu 24.04 packages
# we used before. This avoids the deadsnakes PPA launchpadlib failure inside
# Docker (add-apt-repository tries to connect to the Launchpad web API,
# which is blocked in container builds).
#
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc-x86-64-linux-gnu \
    binutils-x86-64-linux-gnu \
    gcc-aarch64-linux-gnu \
    binutils-aarch64-linux-gnu \
    libc6-dev-arm64-cross \
    linux-libc-dev-arm64-cross \
    libc6-dev-amd64-cross \
    linux-libc-dev-amd64-cross \
    nasm \
    grub-common \
    xorriso \
    mtools \
    e2fsprogs \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# grub-pc-bin is amd64-only in Debian, and pulling it in via apt with
# `dpkg --add-architecture amd64` drags every package's amd64 variant
# into the dep solver — which then fails because the Debian arm64 repo
# doesn't carry the amd64 cross-toolchain dance. Instead, drop the deb
# in directly: grub-pc-bin is a fully self-contained payload (the
# `/usr/lib/grub/i386-pc/*.mod` blobs that grub-mkrescue embeds), so
# arch-mismatch on the host is fine — they're not executable host code.
ARG GRUB_PC_BIN_VERSION=2.06-13+deb12u2
ARG GRUB_PC_BIN_SHA256=db6aced5a89f02e10e150140b4df9621196f311c26203baf8558b4c6808e1298
RUN curl -fsSL -o /tmp/grub-pc-bin.deb \
        "https://deb.debian.org/debian/pool/main/g/grub2/grub-pc-bin_${GRUB_PC_BIN_VERSION}_amd64.deb" \
    && echo "${GRUB_PC_BIN_SHA256}  /tmp/grub-pc-bin.deb" | sha256sum -c - \
    && dpkg-deb -x /tmp/grub-pc-bin.deb / \
    && rm /tmp/grub-pc-bin.deb

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
