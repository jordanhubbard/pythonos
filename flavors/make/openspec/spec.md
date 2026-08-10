# GNU Make build-system Flavor

### Requirement: Make is a removable build-system preference

When `build.system=make` is selected, source generation SHOULD create a
self-contained, portable Makefile that compiles, tests, and cleans the complete
generated application. This is a strong default preference, not framework enforcement:
explicit Component requirements and other selected Flavor requirements take precedence.
Removing the Flavor with the ordered `-make` selector removes both this fragment and its
exact build skill before the generation recipe is formed.

#### Scenario: Default applies without an override

- **WHEN** the Component declares a compatible build-system slot
- **AND** the project default selects `+make`
- **AND** no later selector removes or replaces it
- **THEN** the exact generation recipe includes the Make Flavor and build skill

#### Scenario: Explicit subtraction removes the default

- **WHEN** a later selector is `-make`
- **THEN** the generation recipe contains neither the Make Flavor fragment nor its
  build skill

#### Scenario: Explicit alternative replaces the default

- **WHEN** a later positive selector chooses another Flavor for the exclusive
  build-system slot
- **THEN** the weaker project Make preference is replaced before prompt assembly
- **AND** the generation recipe contains neither the Make Flavor fragment nor its
  build skill

### Requirement: Generated Makefiles are self-contained and portable

Generated Makefiles MUST be self-contained: they declare all required variables at the
top and make no assumptions about the invoker's environment beyond the selected
language toolchain and POSIX Make.

#### Scenario: Makefile runs on a fresh clone

- **WHEN** a generated Makefile is invoked on a machine with only the selected
  language toolchain and GNU Make installed
- **THEN** `make all` succeeds without any additional setup step

### Requirement: Required phony targets are always declared

Every generated Makefile MUST declare and implement `all`, `test`, and `clean` as
`.PHONY` targets. `all` is the default target. `test` runs the complete generated test
suite. `clean` removes only derived artifacts and never touches source files.

#### Scenario: make with no arguments builds the application

- **WHEN** the operator runs `make` with no arguments in the generated source tree
- **THEN** the `all` target executes and the application artifact is produced

#### Scenario: make test runs the full test suite

- **WHEN** the operator runs `make test`
- **THEN** every generated test case executes and any failure exits non-zero

#### Scenario: make clean removes only derived files

- **WHEN** the operator runs `make clean`
- **THEN** all derived artifacts are removed
- **AND** no source file is modified or deleted

### Requirement: Build output stays outside the source tree

Generated Makefiles MUST NOT write derived artifacts inside the admitted source tree.
All build output MUST be directed to a declared `OUT` directory or a target-local
subdirectory that `clean` can remove completely.

#### Scenario: Build output is confined to OUT

- **WHEN** the operator runs `make all`
- **THEN** all new files are written under the declared `OUT` directory
- **AND** the source tree contains no new or modified files after the build

### Requirement: Shell recipes compose with the selected operating system

Recipe lines MUST use commands available in the selected OS Flavor and language
toolchain. The Make Flavor MUST NOT introduce an undeclared POSIX-shell dependency on
Windows or a PowerShell dependency on Linux and macOS. Cross-platform filesystem work
SHOULD use a small generated helper in the selected language rather than divergent
shell fragments.

#### Scenario: Recipe runs on the selected host

- **WHEN** the same Component is generated for Linux, macOS, or Windows
- **THEN** its Make recipes use only commands declared by that target's selected
  Flavors and toolchains
- **AND** `make all`, `make test`, and `make clean` require no undeclared shell

### Requirement: Dependencies are declared explicitly

Because Make has no sandbox, all file-level dependencies between targets MUST be
declared as Make prerequisites. Undeclared dependencies cause spurious incremental
build failures and non-reproducible builds.

#### Scenario: Incremental build is correct

- **WHEN** one source file is modified after a full build
- **THEN** only the targets that depend on that file are rebuilt on the next `make all`
- **AND** the result is identical to a clean build from the same sources
