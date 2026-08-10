---
name: "make-build-system"
description: "GNU Make build-system design. Use for Literate AI workflow tasks."
metadata:
  author: "Literate AI maintainers <literate-ai-maintainers@users.noreply.github.com>"
schema: "urn:literate-ai:schema:v1:specification-to-source-skill"
skill_id: "make-build-system"
version: "1.0.0"
title: "GNU Make build-system design"
stages:
  - "generate"
dependencies: []
limitations:
  - "Generated Makefiles must declare phony targets explicitly with .PHONY."
  - "Do not write derived artifacts inside the admitted source tree; direct all build output to a declared OUT directory or a target-local subdirectory."
  - "Compose recipe commands with the selected OS Flavor; do not introduce an undeclared POSIX-shell or PowerShell dependency."
  - "Do not treat this Flavor as authority over an explicit Component specification or selected Flavor requirement."
  - "Do not invoke make, fetch dependencies, or build the application during source generation."
trust: "repository-reviewed"
---
# GNU Make build-system design

Treat Make as a strong build-system preference, meaning a default recommendation in this prompt rather than a runtime mandate. Explicit Component specification text and selected Flavor requirements always take precedence. If they require or justify another build system, follow them and record an honest coherent build design; an ordered `-make` selector removes this Flavor and therefore removes this skill before generation.

Generate a portable, self-contained Makefile with the following structure and targets.

## Top-level variables

Declare all configuration at the top of the Makefile as `?=` assignable variables so operators can override them from the command line without editing the file:

```makefile
OUT     ?= _build
TARGET  ?= run
SRCS    := $(wildcard src/*.py)   # adjust glob to match the selected language
```

## Required phony targets

Every generated Makefile MUST contain a `.PHONY` declaration listing all phony targets, and MUST implement `all`, `test`, and `clean`:

```makefile
.PHONY: all test clean

all: $(OUT)/$(TARGET)

test: all
	# run the full generated test suite; exit non-zero on any failure

clean:
	# invoke a generated, selected-language cleanup helper for $(OUT)
```

`all` is listed first so it is the default target when `make` is invoked with no arguments. `clean` MUST remove only files under `$(OUT)` or other declared output directories; it MUST NOT delete or modify any source file.

## Build output isolation

All derived artifacts MUST be written under `$(OUT)`. Recipe lines that produce files MUST write to `$(OUT)/...` paths. Never write build output next to source files. This constraint makes `make clean` a reliable and complete reset.

## Explicit prerequisite declarations

Make has no sandbox. Every file-level dependency between targets MUST appear as a Make prerequisite. When a rule produces `$(OUT)/foo.o` from `src/foo.c` and `src/foo.h`, both source files are prerequisites:

```makefile
$(OUT)/foo.o: src/foo.c src/foo.h | $(OUT)
	$(CC) $(CFLAGS) -c $< -o $@
```

Undeclared prerequisites cause spurious incremental-build failures.

## Host portability

Compose commands with the selected OS Flavor. Do not introduce an undeclared POSIX-shell dependency on Windows or a PowerShell dependency on Linux and macOS. Prefer direct compiler, package-manager, and generated executable invocations. For filesystem operations such as recursive cleanup, generate a small helper in the selected language so the same target semantics work on Linux, macOS, and Windows.

## Integration with the selected language ecosystem

This skill covers the build-system layer only. It composes with the language skill already selected by the project Flavor set. Defer to the language skill for compiler flags, package manager invocation, test runner selection, and artifact format. Reference the language skill's declared variables and commands in the Makefile rather than duplicating them.

## No hardcoded host paths

Never embed absolute paths that are specific to the generation host. Use `$(OUT)` for output, rely on `PATH` for tool discovery, and document any non-standard tool requirement in the Component specification rather than hard-coding its path.
