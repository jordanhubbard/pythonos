# Docker cross-compilation build system

### Requirement: All compilation happens inside Docker

Source generation SHALL produce build instructions that invoke the project's Docker
cross-compilation image rather than relying on host-installed compilers or Python
source trees.

#### Scenario: Clean first-time build

- **WHEN** `make docker-build` has been run once
- **THEN** `make` produces a bootable ISO from source without requiring any C compiler,
  Python source tree, or architecture-specific toolchain on the host
- **AND** the Docker image provides the complete cross-compilation closure

#### Scenario: Incremental build uses cached libpython

- **WHEN** the libpython build cache (`deps/` or `deps-arm64/`) exists from a prior run
- **THEN** `make` rebuilds only changed kernel source files and reproduced the ISO
  without recompiling CPython
- **AND** the total incremental build time is significantly less than a clean build

### Requirement: Build output isolation

Generated Makefiles and build scripts MUST direct all artifacts to the declared
`build/` or `build-arm64/` directories. The Docker container MUST NOT write to host
paths outside the project working directory mount.

#### Scenario: `make clean` leaves libpython cache intact

- **WHEN** `make clean` is run
- **THEN** `build/` and `build-arm64/` are removed
- **AND** `deps/` and `deps-arm64/` (libpython cache) remain intact
- **AND** `make` can subsequently produce the ISO without re-downloading CPython source
