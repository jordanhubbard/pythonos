---
name: record-user-directed-work
description: Turn substantive user suggestions, corrections, architectural direction, and requested changes into durable reasoned repository work items before implementation, keep their evidence checklists current while working, and record completed outcomes in the appropriate project changelog. Use whenever an agent changes a Literate AI project in response to user direction or discovers follow-up work while executing it.
metadata:
  author: Literate AI maintainers <literate-ai-maintainers@users.noreply.github.com>
---

# Record user-directed work

Before implementation, read `docs/roadmap/active-work.md`, inspect current authority,
and turn the user's desired outcome into a stable unchecked `AREA-NNN` work item.

Record the narrowest owning Component, Flavor, skill, workflow, routing policy,
documentation area, or framework core; the reasoned conclusion; dependencies; concrete
subtasks; and exact evidence required for completion. Link an existing detailed roadmap
instead of duplicating it. The active item must still name the next unchecked action so
another agent can resume without conversation history.

Do not edit project authority first. Diagnostic inspection may precede recording only
when needed to understand scope. If emergency containment requires a reversible change,
record it in the same turn and explain the exceptional ordering.

While executing, add discovered follow-ups as unchecked subtasks. Keep secrets,
hostnames, raw prompt journals, and temporary paths out of the roadmap. Do not check a
box because code exists: record the named test, platform, artifact identity, or commit
that proves it.

When all evidence passes, check the parent item and add a concise user-facing outcome to
an existing Component-local changelog or the root `CHANGELOG.md`. Keep rationale in the
roadmap and release outcomes in the changelog. Git provides history; do not create a
second timestamped task ledger.

Classify reusable lessons into the narrowest owning specification, Flavor, or skill and
record that authority update in the same item. Keep one-off candidate mistakes in run
evidence rather than teaching them as permanent rules.
