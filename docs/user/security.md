# Security

[Project guide](../README.md) → security

Treat generated source as untrusted. Keep generation output outside the project,
require an empty output directory, inspect and validate the exact tree, and require
explicit build and host-execution authorization before compiling or running it.

The [framework flow](framework-flow.md) makes those gates visible.
