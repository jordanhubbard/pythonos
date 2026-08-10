# Models and generation

[Project guide](../README.md) → models and generation

Run `litai plan COMPONENT --flavor=+NAME` before generation. When two Component
slots share an axis, bind each role explicitly with `--flavor=+SLOT:NAME` instead.
Project defaults run first; explicit selectors run afterward, so an explicit
build-system Flavor replaces the scaffold's `+bazel` preference and `--flavor=-bazel`
removes it without replacement.
`CODING_CLI` may select `codex`, `claude`, or `cursor-agent`; otherwise Literate AI
chooses the first command on `PATH` in that order. A model declared by an exact
specification or Flavor is passed to the selected CLI using that CLI's supported
model-selection argument. Generation builds a CodeGraph sidecar after canonical source
collection and before guarded validation/build/test. Continue through the
[framework flow](framework-flow.md).
