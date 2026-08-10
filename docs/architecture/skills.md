# Skills

[Project guide](../README.md) → skill boundaries

The root `SKILL.md` teaches an agent how to use the project. It is not a generation
input. Specification-to-source skills live under `skills/specification-to-source/` and
enter generation only through exact `ContentReference` pins on Components or Flavors.
Each skill dependency also pins the required skill version and content identity.

The installed `bazel-build-system` skill is reached through the pinned `bazel` Flavor.
It recommends Bazel in the generation prompt but yields to explicit Component and
selected-Flavor requirements. Selecting an alternative build-system Flavor or using
`-bazel` removes that skill from the resolved generation recipe.

Inverse source-to-specification skills are a separate catalog. Return to the
[framework flow](../user/framework-flow.md) before generating source.
