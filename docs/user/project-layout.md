# Project layout

[`literate.project.json`](../../literate.project.json) names every authoritative root,
including `documentation_roots`; nearby directories are ambient until declared. Keep
the provider-neutral onboarding skill at root `SKILL.md` and make provider files thin
pointers to it.

The manifest also declares the canonical source-intelligence provider. `litai
init` creates a real local `.codegraph/codegraph.db`, while project validation
checks it without mutation. Database bytes are ignored derived state, not source or Git
authority; generated-tree provenance and passing receipts bind stable index evidence.

Component specifications may place explanatory Mermaid diagrams beside their prose so
behavior and its illustration evolve together. Diagrams explain; prose requirements and
acceptance scenarios remain normative. See the
[traceability rule](../architecture/design-traceability.md) and
[framework flow](framework-flow.md).

A normal Component keeps portable metadata and its default behavior together in
`components/NAME/component.md`. Generated `component.lock.json` records exact target and
dependency resolution beside it but is not hand-maintained prose. Extra specification or
interface files are exceptional named boundaries, not boilerplate for every Component.
Harness vectors and private expected-value oracles live outside Component authority.

The initializer installs the content-pinned `flavors/bazel/` policy and its exact
`skills/specification-to-source/bazel-build-system/` input. The manifest selects it by
default only when a Component declares a compatible `build.system` slot. It is not
runtime enforcement or evidence that Bazel actually built an application.
