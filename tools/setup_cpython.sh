#!/usr/bin/env bash
# setup_cpython.sh — Download, patch, and configure CPython for bare-metal PythonOS.
#
# Run inside the Docker build environment (Ubuntu 24.04) or on any Linux host
# with the cross-compiler toolchain installed.
#
# Usage:
#   ./tools/setup_cpython.sh           # downloads CPython 3.14, patches, configures
#   ./tools/setup_cpython.sh --build   # also compiles libpython3.14.a
#
# Output: deps/cpython/ — configured CPython source tree
#         deps/cpython/libpython3.14.a — static library (with --build)
#
# Two-phase build strategy:
#   Phase 1 — ./configure runs with the HOST compiler (gcc) so that all
#              feature probes can compile, link, and execute on the build machine.
#              ac_cv_* cache variables pre-answer the probes that would wrongly
#              detect POSIX features we don't have.
#   Phase 2 — make libpython$(VERSION)$(ABIFLAGS).a is driven with the CROSS compiler
#              (x86_64-elf-gcc = x86_64-linux-gnu-gcc in Docker) plus our
#              bare-metal CFLAGS. Our pyconfig.h (installed after configure)
#              overrides everything configure detected, so the resulting .o
#              files only depend on symbols we actually provide.

set -euo pipefail

CPYTHON_VERSION="3.14.4"
CPYTHON_URL="https://www.python.org/ftp/python/${CPYTHON_VERSION}/Python-${CPYTHON_VERSION}.tar.xz"
CPYTHON_SHA256="d923c51303e38e249136fc1bdf3568d56ecb03214efdef48516176d3d7faaef8"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ── Architecture selection ─────────────────────────────────────────────────────
ARCH="x86_64"
BUILD_REQUESTED=0
for arg in "$@"; do
    case "$arg" in
        --arch=arm64)  ARCH="arm64"  ;;
        --arch=x86_64) ARCH="x86_64" ;;
        --build)       BUILD_REQUESTED=1 ;;
    esac
done

if [[ "$ARCH" == "arm64" ]]; then
    DEPS_DIR="$REPO_ROOT/deps-arm64"
    CROSS_PREFIX="aarch64-elf"
    CC="${CROSS_PREFIX}-gcc"
    AR="${CROSS_PREFIX}-ar"
    RANLIB="${CROSS_PREFIX}-ranlib"
    # Bare-metal CFLAGS for AArch64.
    # -ffixed-x18: reserve x18 as a platform register (mirrors macOS arm64 ABI).
    # No -mno-red-zone: that flag is x86-only; AArch64 has no red zone.
    # NOTE: do NOT define -D_GNU_SOURCE or -D_POSIX_C_SOURCE. Those feature-test
    #   macros cause system headers to transitively include bits/pthreadtypes.h
    #   which defines real Linux pthread struct layouts that conflict with
    #   PythonOS pthread definitions.
    TARGET_CFLAGS="-std=c11 -O2 -ffreestanding -fno-stack-protector -fno-pie \
      -ffixed-x18 \
      -march=armv8-a \
      -I${REPO_ROOT}/src/libc/include \
      -I${REPO_ROOT}/deps-arm64/cpython \
      -DARCH_ARM64=1 \
      -DPy_BUILD_CORE=1 \
      -DNDEBUG=1 \
      -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0"
    # Configure with the HOST compiler (x86_64 gcc) just to generate the Makefile.
    # The actual compile in Phase 2 overrides CC=aarch64-elf-gcc, identical to how
    # the x86_64 build works. No --host flag — avoid cross-compile mode in configure
    # since we can't link arm64 test programs on the build host.
    CONFIGURE_EXTRA=""
else
    DEPS_DIR="$REPO_ROOT/deps"
    CROSS_PREFIX="x86_64-elf"
    CC="${CROSS_PREFIX}-gcc"
    AR="${CROSS_PREFIX}-ar"
    RANLIB="${CROSS_PREFIX}-ranlib"
    # Bare-metal CFLAGS for the actual library build (Phase 2).
    # NOT passed to configure — they would break configure's linker probes.
    #
    # NOTE: do NOT pass -mno-sse/-mno-sse2 here. Those flags are only for the
    #   kernel boot/ISR code (see BOOT_CFLAGS in Makefile). CPython must be
    #   able to generate SSE2 code since it runs with FPU state fully saved.
    # NOTE: do NOT define -D_GNU_SOURCE or -D_POSIX_C_SOURCE. Those feature-test
    #   macros cause system headers to transitively include bits/pthreadtypes.h
    #   which defines real Linux pthread struct layouts that conflict with
    #   PythonOS pthread definitions.
    TARGET_CFLAGS="-std=c11 -O2 -ffreestanding -fno-stack-protector -fno-pie \
      -mno-red-zone \
      -I${REPO_ROOT}/src/libc/include \
      -I${REPO_ROOT}/deps/cpython \
      -DPy_BUILD_CORE=1 \
      -DNDEBUG=1 \
      -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0"
    CONFIGURE_EXTRA=""
fi

# CPython's configure and build rewrite a large Makefile many times.  Keep
# those operations off the repository bind mount: Docker Desktop on macOS can
# expose partially updated files to a subsequent command in the same container.
# Work on the container-native filesystem and publish the completed source tree
# only after every requested setup/build step succeeds.
PERSISTENT_CPYTHON_SRC="$DEPS_DIR/cpython-src"
LOCAL_BUILD_ROOT="$(mktemp -d /tmp/pythonos-cpython.XXXXXX)"
cleanup_local_build() {
    rm -rf "$LOCAL_BUILD_ROOT"
}
trap cleanup_local_build EXIT

LOCAL_DEPS_DIR="$LOCAL_BUILD_ROOT/$(basename "$DEPS_DIR")"
CPYTHON_SRC="$LOCAL_DEPS_DIR/cpython-src"
mkdir -p "$LOCAL_DEPS_DIR" "$LOCAL_BUILD_ROOT/src/hal"

# Modules/Setup.local reaches _hal through ../../../src/hal/hal.c.  Copy its
# writable output directory locally while linking its read-only dependencies.
cp -f "$REPO_ROOT/src/hal/hal.c" "$LOCAL_BUILD_ROOT/src/hal/hal.c"
ln -s "$REPO_ROOT/src/boot" "$LOCAL_BUILD_ROOT/src/boot"
ln -s "$REPO_ROOT/src/linenoise" "$LOCAL_BUILD_ROOT/src/linenoise"

FREE_THREADING="${PYTHONOS_FREE_THREADING:-0}"
if [[ "$FREE_THREADING" == "1" ]]; then
    CONFIGURE_EXTRA="$CONFIGURE_EXTRA --disable-gil --with-mimalloc"
    echo "==> EXPERIMENTAL: configuring CPython free-threading (--disable-gil)"
fi

echo "==> PythonOS CPython bare-metal setup v${CPYTHON_VERSION} [arch=${ARCH}]"

# ── 1. Download ──────────────────────────────────────────────────────────────
mkdir -p "$DEPS_DIR"
TARBALL="$DEPS_DIR/Python-${CPYTHON_VERSION}.tar.xz"
if [ ! -f "$TARBALL" ]; then
    echo "==> Downloading CPython ${CPYTHON_VERSION}..."
    curl -L "$CPYTHON_URL" -o "$TARBALL"
fi

echo "==> Verifying checksum..."
echo "${CPYTHON_SHA256}  $TARBALL" | sha256sum -c -

# ── 2. Extract ───────────────────────────────────────────────────────────────
if [ ! -d "$CPYTHON_SRC" ]; then
    echo "==> Extracting..."
    tar -xf "$TARBALL" -C "$LOCAL_DEPS_DIR"
    mv -f "$LOCAL_DEPS_DIR/Python-${CPYTHON_VERSION}" "$CPYTHON_SRC"
fi

cd "$CPYTHON_SRC"

# ── 3. Apply patches ─────────────────────────────────────────────────────────
echo "==> Applying bare-metal patches..."

# Patch 1: Remove #include <sys/select.h> from timemodule.c — we don't have it
if ! grep -q "PythonOS_patched" Modules/timemodule.c; then
    sed -i 's/#ifdef HAVE_SELECT/#if 0 \/\* PythonOS_patched \*\//g' Modules/timemodule.c
    echo "  patched: Modules/timemodule.c"
fi

# Patch 2: signalmodule.c — disable sigaltstack (needs sys/types.h)
if ! grep -q "PythonOS_patched" Modules/signalmodule.c; then
    sed -i 's/#ifdef HAVE_SIGALTSTACK/#if 0 \/\* PythonOS_patched \*\//g' Modules/signalmodule.c
    echo "  patched: Modules/signalmodule.c"
fi

# Patch 3: Python/fileutils.c — disable the fd cloexec loop (no fork)
if ! grep -q "PythonOS_patched" Python/fileutils.c; then
    sed -i 's/res = fcntl(fd, F_SETFD, new_flags);/res = 0; \/\/ PythonOS_patched: skipped fcntl/g' Python/fileutils.c
    echo "  patched: Python/fileutils.c"
fi

# Patch 4: pycore_pyhash.h uses dev_t/ino_t without including sys/types.h
if ! grep -q "PythonOS_patched" Include/internal/pycore_pyhash.h; then
    sed -i 's/#ifndef Py_BUILD_CORE/#include <sys\/types.h> \/\* PythonOS_patched *\/ \n#ifndef Py_BUILD_CORE/' \
        Include/internal/pycore_pyhash.h
    echo "  patched: Include/internal/pycore_pyhash.h"
fi

# ── 4. Configure with HOST compiler (Phase 1) ─────────────────────────────────
# We deliberately do NOT pass CC/CFLAGS/LDFLAGS here. configure must be able
# to compile, link, and run test programs on the build host. The bare-metal
# flags (-ffreestanding, -nostdlib) would prevent any linker probes from
# succeeding, confusing configure into thinking the compiler is broken.
#
# We override the probes that matter via ac_cv_* cache variables, and we
# install our own pyconfig.h AFTER configure (which regenerates it from
# pyconfig.h.in during the configure run).

echo "==> Phase 1: Configuring CPython with host compiler..."
# shellcheck disable=SC2086
./configure \
    --without-pydebug \
    --disable-shared \
    --without-ensurepip \
    --without-readline \
    --disable-ipv6 \
    --without-dtrace \
    --without-c-locale-coercion \
    --with-computed-gotos \
    $CONFIGURE_EXTRA \
    ac_cv_file__dev_ptmx=no \
    ac_cv_file__dev_null=no \
    ac_cv_header_netinet_in_h=no \
    ac_cv_header_sys_socket_h=no \
    ac_cv_header_sys_select_h=no \
    ac_cv_header_fcntl_h=no \
    ac_cv_header_unistd_h=no \
    ac_cv_func_fork=no \
    ac_cv_func_execv=no \
    ac_cv_func_getpid=yes \
    ac_cv_func_mmap_fixed_mapped=yes \
    2>&1 | tee "$DEPS_DIR/configure.log"

echo "==> Configuration complete. Log: $DEPS_DIR/configure.log"

# ── 5. Install our pyconfig.h and Modules/Setup.local ────────────────────────
# These MUST come after configure, which regenerates pyconfig.h from
# pyconfig.h.in. Our version overrides the host-detected values with the
# bare-metal subset our libc actually provides.
echo "==> Installing bare-metal pyconfig.h..."
if [[ "$ARCH" == "arm64" ]]; then
    cp "$REPO_ROOT/deps/pyconfig_arm64.h" "$CPYTHON_SRC/pyconfig.h"
else
    cp "$REPO_ROOT/deps/pyconfig.h" "$CPYTHON_SRC/pyconfig.h"
fi
if [[ "$FREE_THREADING" == "1" ]]; then
    sed -i \
        -e 's|#undef  Py_GIL_DISABLED|#define Py_GIL_DISABLED 1|' \
        -e '/#define WITH_PYMALLOC/d' \
        "$CPYTHON_SRC/pyconfig.h"
    printf '\n#define WITH_MIMALLOC 1\n' >> "$CPYTHON_SRC/pyconfig.h"
fi

echo "==> Installing Modules/Setup.local..."
cp "$REPO_ROOT/deps/Modules.Setup.local" "$CPYTHON_SRC/Modules/Setup.local"

# For arm64 builds, update the hal path in Setup.local to reference the arm64 deps dir
if [[ "$ARCH" == "arm64" ]]; then
    sed -i 's|../../deps/cpython|../../deps-arm64/cpython|g' "$CPYTHON_SRC/Modules/Setup.local" 2>/dev/null || true
fi

# ── 5b. Post-configure Makefile patches ──────────────────────────────────────
# Patch the configure-generated Makefile to work with our bare-metal build:
#   - _warnings: code is in Python/_warnings.c (already in Python/*.o) — remove
#   - _string: code is in Objects/unicodeobject.c — remove
#   - _csv.c (renamed from _csvmodule.c in CPython 3.13, unchanged in 3.14)
#   - sha256module.c / sha512module.c → sha2module.c (needs HACL) — skip
#   - hal path: ../../src/hal → ../../../src/hal (one more level up from Modules/)
echo "==> Patching configure-generated Makefile..."
# Redirect _freeze_module and _bootstrap_python to python3.14 — both binaries
# link libpython with -fno-pie which fails PIE-only linkers.
# python3.14 MUST be used here: frozen bytecode must match the interpreter version
# (RESUME opcode = 128 in 3.14; older versions have different numbering).
sed -i \
    -e 's|^PYTHON_FOR_FREEZE=.*$|PYTHON_FOR_FREEZE=python3.14|' \
    -e 's|^FREEZE_MODULE_BOOTSTRAP=.*$|FREEZE_MODULE_BOOTSTRAP=python3.14 ./Programs/_freeze_module.py|' \
    -e 's|^FREEZE_MODULE_BOOTSTRAP_DEPS=.*$|FREEZE_MODULE_BOOTSTRAP_DEPS=Programs/_freeze_module.py|' \
    -e 's|^FREEZE_MODULE_DEPS=.*$|FREEZE_MODULE_DEPS=$(srcdir)/Programs/_freeze_module.py|' \
    "$CPYTHON_SRC/Makefile"
# Remove stale/excluded module objects
sed -i \
    -e 's/ Modules\/_warnings\.o / /g' \
    -e 's/ Modules\/_string\.o / /g' \
    -e 's/ Modules\/sha256module\.o  Modules\/sha512module\.o / /g' \
    -e 's/ Modules\/_csvmodule\.o / Modules\/_csv.o /g' \
    -e 's|Modules/\.\./\.\./src/hal/|Modules/../../../src/hal/|g' \
    -e 's/ Modules\/posixmodule\.o / /g' \
    -e 's| Modules/_decimal/_decimal\.o | |g' \
    -e 's/ Modules\/sha1module\.o / /g' \
    -e 's/ Modules\/md5module\.o / /g' \
    -e 's/ Modules\/pwdmodule\.o / /g' \
    -e 's| Modules/_blake2/blake2module\.o Modules/_blake2/blake2b_impl\.o Modules/_blake2/blake2s_impl\.o | |g' \
    -e 's| Modules/blake2module\.o | |g' \
    "$CPYTHON_SRC/Makefile"
# Remove binascii's zlib dependency: clear USE_ZLIB_CRC32 flag and -lz linker flag
# sha1/md5 use HACL library which we don't provide; remove their CFLAGS entirely
sed -i \
    -e 's|^MODULE_BINASCII_CFLAGS=.*$|MODULE_BINASCII_CFLAGS=|' \
    -e 's|^MODULE_BINASCII_LDFLAGS=.*$|MODULE_BINASCII_LDFLAGS=|' \
    "$CPYTHON_SRC/Makefile"
# Fix the _csv.o build rule (source file renamed from _csvmodule.c to _csv.c)
sed -i \
    -e 's|Modules/_csvmodule\.o: \$(srcdir)/Modules/_csvmodule\.c|Modules/_csv.o: $(srcdir)/Modules/_csv.c|g' \
    -e 's|-c \$(srcdir)/Modules/_csvmodule\.c -o Modules/_csvmodule\.o|-c $(srcdir)/Modules/_csv.c -o Modules/_csv.o|g' \
    "$CPYTHON_SRC/Makefile"
# Neuter PIE-incompatible binary targets: _freeze_module and _bootstrap_python.
# Both link libpython with -fno-pie which fails Ubuntu 24.04's PIE-only linker.
# Replace their link recipes with no-ops; frozen module headers are pre-generated
# via python3.14 Programs/_freeze_module.py in step 5c below.
#
# _freeze_module's link recipe uniquely contains 'getpath_noop.o'.
# _bootstrap_python's link recipe uniquely contains '_bootstrap_python.o'.
sed -i \
    -e 's|^\t\$(LINKCC).*getpath_noop.*$|\t@echo "PythonOS: skipping _freeze_module link"|' \
    -e 's|^\t\$(LINKCC).*_bootstrap_python\.o.*$|\t@echo "PythonOS: skipping _bootstrap_python link"|' \
    -e 's|^\t\t.*LIBRARY_OBJS_OMIT_FROZEN.*$|\t@true|' \
    "$CPYTHON_SRC/Makefile"
# Also neuter the _bootstrap_python dependency line so make doesn't re-enter the rule
sed -i \
    -e 's|^_bootstrap_python: .*$|_bootstrap_python: Programs/_freeze_module.py|' \
    -e 's|^Programs/_freeze_module: Programs/_freeze_module\.o.*$|Programs/_freeze_module: Programs/_freeze_module.py|' \
    "$CPYTHON_SRC/Makefile"
echo "==> Neutered _freeze_module and _bootstrap_python build rules"
# Neuter the Makefile self-regeneration rule.
# When 'make libpython3.14.a' runs, make sees Setup.local (installed after
# configure) is newer than Makefile and re-runs 'make -f Makefile.pre', which
# regenerates Makefile from scratch and overwrites all of our patches above.
# Replace the regeneration recipe with a no-op to prevent this.
sed -i \
    -e 's|^\t\$(MAKESETUP) -c .*$|\t@echo "PythonOS: skipping Makefile regen (patches preserved)"|' \
    -e '/^\t\t\t\t\(-s Modules\|Modules\/Setup\)/d' \
    -e 's|^\t@mv config\.c Modules.*$|\t@true|' \
    -e '/^\t@echo "The Makefile was updated/d' \
    "$CPYTHON_SRC/Makefile"
echo "==> Neutered Makefile self-regeneration rule"
# Regenerate Modules/config.c from our custom Setup.local so that make
# does not try to regenerate it (and lose our Makefile patches).
# We discard the Makefile fragment output and only keep config.c.
echo "==> Regenerating Modules/config.c from our Setup.local..."
# In CPython 3.14+, makesetup moved from Misc/ to Modules/.
# It writes config.c to the current directory; the Makefile then does 'mv config.c Modules'.
# Run from CPYTHON_SRC so relative paths (-s Modules, Setup.local etc.) resolve correctly.
(cd "$CPYTHON_SRC" && \
 Modules/makesetup \
    -c Modules/config.c.in \
    -s Modules \
    Modules/Setup.local \
    Modules/Setup.stdlib \
    Modules/Setup.bootstrap \
    Modules/Setup \
    > /dev/null 2>&1 && mv config.c Modules/ ) || echo "WARN: makesetup failed — config.c may be incomplete"

# Touch Makefile AND config.c AFTER patching so make does not re-generate
# them when it sees Setup.local is newer (autoconf self-regen rule).
touch "$CPYTHON_SRC/Makefile"
touch "$CPYTHON_SRC/Modules/config.c"

# ── 5c. Pre-generate frozen module headers ────────────────────────────────────
# Programs/_freeze_module is a HOST tool that must compile and run on the build
# host. Since we cross-compile with -ffreestanding/-fno-pie, the binary can't
# be built normally. Instead, use the pure-Python implementation with python3.14.
#
# CRITICAL: python3.14 must be used. The frozen bytecode must use the same
# opcode numbering as the interpreter (RESUME = 128 in 3.14). Using a different
# Python version produces bytecode with wrong opcode numbers at instruction 0,
# causing an immediate fault before any Python code can run.
FREEZE_PY=$(command -v python3.14 2>/dev/null || command -v python3 2>/dev/null)
FREEZE_PY_VER=$("$FREEZE_PY" -c "import sys; print(sys.version_info[:2])" 2>/dev/null)
if [[ "$FREEZE_PY_VER" != "(3, 14)" ]]; then
    echo "ERROR: python3.14 is required to generate frozen modules but found $FREEZE_PY ($FREEZE_PY_VER)"
    echo "       Install python3.14 and retry."
    exit 1
fi
echo "==> Pre-generating frozen module headers with $FREEZE_PY ($FREEZE_PY_VER)..."
mkdir -p Python/frozen_modules
# Always regenerate all frozen module headers — tarball timestamps cannot be
# trusted and using the wrong Python version silently corrupts the bytecode.
#
# Read the rules from the pristine source template, not the generated Makefile.
# The latter is rewritten several times above.  On Docker Desktop for macOS,
# an immediately following read through the bind mount can transiently make
# grep classify even a text file as binary.  Makefile.pre.in contains the same
# freeze recipes and is never rewritten; `-a` also prevents grep's binary-file
# short circuit from turning a successful first install into an empty pipeline.
grep -a -A1 'frozen_modules/.*\.h:' Makefile.pre.in \
    | grep -a $'^\t' \
    | grep -a 'FREEZE_MODULE' \
    | sed 's/.*BOOTSTRAP) //' \
    | sed 's/.*FREEZE_MODULE) //' \
    | sed "s|\$(srcdir)|.|g" \
    | while read -r modname srcpy outfile; do
        if [ -n "$modname" ] && [ -n "$outfile" ]; then
            echo "  freezing: $modname"
            "$FREEZE_PY" "$CPYTHON_SRC/Programs/_freeze_module.py" \
                "$modname" "$CPYTHON_SRC/$srcpy" "$CPYTHON_SRC/$outfile" \
                2>/dev/null || true
        fi
      done
# Touch all frozen headers to prevent make from trying to regenerate them
touch Python/frozen_modules/*.h 2>/dev/null || true

# ── 6. Optionally build (Phase 2) ─────────────────────────────────────────────
if [[ "$BUILD_REQUESTED" == "1" ]]; then
    PY_VERSION=$(sed -n 's/^VERSION=[[:space:]]*//p' "$CPYTHON_SRC/Makefile" | head -1 | tr -d '[:space:]')
    PY_ABIFLAGS=$(sed -n 's/^ABIFLAGS=[[:space:]]*//p' "$CPYTHON_SRC/Makefile" | head -1 | tr -d '[:space:]')
    LIBPYTHON_ARCHIVE="libpython${PY_VERSION}${PY_ABIFLAGS}.a"

    echo "==> Phase 2: Building ${LIBPYTHON_ARCHIVE} with cross-compiler..."
    echo "    ARCH=$ARCH"
    echo "    CC=$CC"
    echo "    CFLAGS=$TARGET_CFLAGS"

    # Clean previous objects so CFLAGS changes (e.g. -U_FORTIFY_SOURCE) take
    # effect — make does not track compiler flag changes in its dependency graph.
    echo "==> Cleaning previous build artifacts..."
    find "$CPYTHON_SRC" -name '*.o' -delete 2>/dev/null || true
    rm -f "$CPYTHON_SRC"/libpython*.a

    # Pass FREEZE_MODULE overrides on the command line — these survive any
    # Makefile self-regeneration. The Docker/macOS volume mount resolves
    # timestamps to 1-second granularity inside the container; touch Makefile
    # and Setup.local may land in the same second, causing make's self-regen
    # rule to fire and regenerate Makefile from Makefile.pre. Even if it does,
    # command-line variable assignments always override Makefile definitions:
    #   FREEZE_MODULE_BOOTSTRAP_DEPS — change from binary to .py script
    #   FREEZE_MODULE_DEPS           — remove _bootstrap_python, use .py only
    #   FREEZE_MODULE_BOOTSTRAP      — use python3.14 to run .py directly
    #   PYTHON_FOR_FREEZE            — use python3.14 for non-bootstrap freeze
    # -W Makefile -W Modules/config.c: additionally tell make both are up-to-date
    # so the self-regen recipe is skipped if possible.
    make -j"$(nproc)" "$LIBPYTHON_ARCHIVE" \
        -W Makefile \
        -W Modules/config.c \
        'FREEZE_MODULE_BOOTSTRAP=python3.14 ./Programs/_freeze_module.py' \
        'FREEZE_MODULE_BOOTSTRAP_DEPS=Programs/_freeze_module.py' \
        'FREEZE_MODULE_DEPS=$(srcdir)/Programs/_freeze_module.py' \
        'PYTHON_FOR_FREEZE=python3.14' \
        CC="$CC" \
        AR="$AR" \
        RANLIB="$RANLIB" \
        CFLAGS="$TARGET_CFLAGS" \
        LDFLAGS="" \
        2>&1 | tee "$DEPS_DIR/build.log"

    echo "==> CPython archive built successfully."
else
    echo ""
    echo "Next: ./tools/setup_cpython.sh [--arch=arm64] --build"
    echo "  or: cd $PERSISTENT_CPYTHON_SRC && make -j\$(nproc) libpython3.14.a CC=$CC CFLAGS=..."
fi

echo "==> Publishing configured CPython source tree..."
PUBLISH_CPYTHON_SRC="$DEPS_DIR/cpython-src.tmp"
rm -rf "$PUBLISH_CPYTHON_SRC"
cp -rf "$CPYTHON_SRC" "$PUBLISH_CPYTHON_SRC"
rm -rf "$PERSISTENT_CPYTHON_SRC"
mv -f "$PUBLISH_CPYTHON_SRC" "$PERSISTENT_CPYTHON_SRC"

if [[ "$BUILD_REQUESTED" == "1" ]]; then
    # Publish the target archive last.  Its presence is Make's cache marker, so
    # an interrupted source-tree copy must never leave a false successful build.
    mkdir -p "$DEPS_DIR/cpython/Include"
    # Copy directly from container-native storage.  Reading back from the
    # just-published bind-mounted tree can still observe stale zero-filled data
    # on Docker Desktop, even after the write command has returned.
    cp -rf "$CPYTHON_SRC/Include/." "$DEPS_DIR/cpython/Include/"
    if [[ "$ARCH" == "arm64" ]]; then
        cp -f "$REPO_ROOT/deps/pyconfig_arm64.h" "$DEPS_DIR/cpython/pyconfig.h"
    else
        cp -f "$REPO_ROOT/deps/pyconfig.h" "$DEPS_DIR/cpython/pyconfig.h"
    fi
    if [[ "$FREE_THREADING" == "1" ]]; then
        sed -i \
            -e 's|#undef  Py_GIL_DISABLED|#define Py_GIL_DISABLED 1|' \
            -e '/#define WITH_PYMALLOC/d' \
            "$DEPS_DIR/cpython/pyconfig.h"
        printf '\n#define WITH_MIMALLOC 1\n' >> "$DEPS_DIR/cpython/pyconfig.h"
    fi
    cp -f "$CPYTHON_SRC/$LIBPYTHON_ARCHIVE" \
        "$DEPS_DIR/cpython/libpython3.14.a"
    echo "==> Done. Library: $DEPS_DIR/cpython/libpython3.14.a"
fi

echo "==> CPython bare-metal setup complete."
