# Writing readable specifications

[Project guide](../README.md) → readable specifications

Use specifications for observable product behavior, Flavors for OS/language/build
choices, and exact skills for reusable conversion guidance. Start every normal Component
with one `component.md`: strict frontmatter carries portable Component metadata and the
Markdown body is its default `literate-markdown` behavioral specification.

```markdown
---
namespace: example
version: 1.0.0
display_name: Scene Viewer
profiles: []
sample: false
provides: []
requires: []
authoring_inputs: []
workflow_definition:
  uri: ../../workflows/specification-to-source.md
routing_policy:
  uri: ../../routing/specification-to-source.json
flavor_slots: []
entrypoints: []
acceptance_contracts: []
source_dependencies: []
---
# Scene Viewer

Load one reviewed scene and expose clear failures for missing or invalid assets.
```

Do not create `component.json`, `openspec/app.json`, `openspec/spec.md`, or an
`acceptance/` directory as peer authoring files for a simple Component. Repository test
vectors and private oracles belong outside the Component tree. Add another specification
or interface document only for a named domain, module, protocol, or independently
consumed public Component boundary.

An additional `literate-markdown` node begins with narrow frontmatter:

```markdown
---
name: Scene loading
summary: Load and validate one factory scene
kind: component
references:
  - product.core.scene-contract
---
```

Only `name`, `summary`, and `kind` are required. Paths derive IDs and parents;
`references` imports local constraints. Do not add dates, hand-bumped revisions,
toolchains, or generic test/build guidance. Content identities and Git own history;
Flavors and skills own reusable technique. When explicit additional roots are selected,
the first is still `component.md`; nested node folders use their own `spec.md` only when
they represent a genuine named boundary.

Run `litai plan COMPONENT ...` to validate the exact corpus and inspect its
deterministic context graph before generation. See the
[mission-specification map](../architecture/mission-specification-composition.md) for
the distinction between local node nesting and independently generatable Components.
