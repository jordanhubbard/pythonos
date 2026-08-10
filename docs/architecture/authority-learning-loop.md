# Authority learning loop

[Project guide](../README.md) → authority learning loop

A project learns only when typed run evidence produces a reviewed Git-tracked change to
future generation authority. Retry feedback repairs one candidate; it is not durable
learning by itself.

```mermaid
flowchart LR
    Run[Derivation evidence] --> Decide{Reusable lesson?}
    Decide -- no --> History[Compact run history]
    Decide -- yes --> Classify{Narrowest owner}
    Classify --> Spec[Component behavior or interface]
    Classify --> Flavor[Target variance]
    Classify --> Skill[Reusable conversion technique]
    Classify --> Flow[Workflow or routing]
    Spec & Flavor & Skill & Flow --> Review[Review, refresh locks, rebuild, accept]
    Review --> Git[Git-tracked authority]
```

Never copy a generated workaround into a behavioral specification merely because that
is where the failure appeared. Private acceptance values, secrets, host paths, and prior
candidate source cannot enter a learning proposal. Skill changes must also pass the
project's pinned SkillEvaluator gate. Until `litai learn` is implemented, make this
classification explicitly during review and bind the accepted lesson through the normal
authority diff, lock refresh, lifecycle, and Git commit.
