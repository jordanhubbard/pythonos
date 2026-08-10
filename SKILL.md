---
name: literate-ai
description: Use Literate AI to create, change, generate, validate, build, test, review, or explain specification-led Components, Flavors, skills, workflows, routing policies, and project layouts. Use whenever a repository contains literate.project.json or the user asks to work with Literate AI.
metadata:
  author: Literate AI maintainers <literate-ai-maintainers@users.noreply.github.com>
---

# Literate AI

Treat specifications as application authority and generated source as replaceable
derived output.

## Follow the project flow

1. Before substantive implementation, follow
   `skills/agent/record-user-directed-work/SKILL.md`: record the reasoned next action in
   `docs/roadmap/active-work.md` and execute from its unchecked evidence-gated queue.
2. Locate `literate.project.json` and run `litai verify` for the project's
   declared gates in one call: authority, Component locks, index currency, and
   receipt. It writes nothing and builds nothing. Use `litai rebuild` when the
   artifact must actually be produced through the project's own build harness,
   and `litai update` only to pull framework changes from upstream. For the
   authority gate alone, run `litai project validate`. When `.codegraph/`
   exists, query it before broad text search. Its database is derived evidence, not
   specification or source authority.
3. Read the selected Component's `component.md`, local behavioral documents, direct
   public interface contracts, and acceptance contract. Never flatten private
   transitive Component details into a consumer prompt.
4. Run `litai lock COMPONENT --check` for the intended target and ordered Flavor
   selectors. Review `--diff` before deliberately updating stale lock authority.
5. Run `litai plan COMPONENT --target TARGET` with those same selectors. Review the
   exact specification, Flavor-slot, skill, workflow, routing, entrypoint, and model
   identities before model egress.
6. Change specifications, Flavors, or pinned skills to change application intent. Do
   not hand-author generated application source. `BUILD_DIR` (default `generated/`) is
   the accepted source cache; `OBJ_DIR` (default `<project>/_build`) contains
   host-specific objects and executables and is excluded from Git and source indexing.
   Cache identity, not timestamps, decides reuse.
7. Preflight the selected coding CLI's authentication or provide its supported
   environment token. Literate AI forwards credential values but never writes them.
8. Require explicit acknowledgement before compiling or running generated host code.
   Use `litai rebuild` for the complete authorized lifecycle and `litai generate` only
   when intentionally stopping at generated source. A cache hit remains untrusted until
   current indexing, validation, build, tests, execution, and independent acceptance.
9. Keep inverse proof proportional. Representative language samples may prove
   spec-to-source-to-spec parity locally; cross-platform gates should fan out a small
   forward sample through the project's declared test matrix.
10. Run long validation ladders fail-fast and checkpoint only successfully completed
    named gates under ignored `OBJ_DIR`. Resume after a repair without replaying earlier
    expensive gates. Delete the checkpoint after a complete pass so the next run begins
    at gate one; checkpoint individual tests inside long gates as well. Resumed
    diagnostic evidence is not release attestation.
    The installed portable Python runner is
    `python -m literate_ai.test_checkpointing python --state
    "$OBJ_DIR/python-test-checkpoint.json" --start-directory tests`; native harnesses
    should apply the same versioned checkpoint contract at their stable test boundary.

## Author specifications without boilerplate

Begin a Component with one readable `component.md`. Its frontmatter carries concise
composition metadata and its Markdown body states objective, scope, public contract,
behavior, invariants, errors, examples, and measurable acceptance. Exact resolution
belongs in generated `component.lock.json`, never a duplicate hand-maintained manifest.

- Put OS, architecture, language, toolchain, packaging, deployment, and build-system
  choices in Flavors.
- Put reusable conversion practice in exact skills: context assembly, public-interface
  discipline, current-test generation, source disposal, and conflict surfacing.
- Put cross-Component relationships in capability requirements and public interface
  contracts. A consumer sees direct contracts, not private implementations.
- Add another specification document only for a named domain, module, protocol, or
  independently useful public boundary. Constrained Markdown frontmatter should derive
  IDs and parents from paths and reject unknown keys, drift, and cycles.
- Treat agent-written glue as generated planning or implementation. If it adds public
  behavior, propose an authored specification change for review.

## Preflight host prerequisites

Honor exact specification or Flavor pins first. Otherwise use bounded `PATH` discovery
and report a missing selected tool rather than silently changing provider, language,
build system, or target. Never install or upgrade host tools without authorization.

- Literate AI requires Python 3.11+ and a compatible CodeGraph installation.
- Model-generated source requires one authenticated `codex`, `claude`, or
  `cursor-agent`; honor `CODING_CLI`, otherwise stop at the first compatible command.
- Selected Flavors may additionally require Git, Node.js, C++, Rust, Bazel, package
  resolvers, binary inspectors, browsers, or other target tools.
- Prefer Bazel only through its removable build-system Flavor. Explicit specification
  requirements, another build Flavor, and `-bazel` take precedence.
- Reconcile package manifests, locks, and imports before build. Produce and validate
  CycloneDX source and resolved SBOMs; inspect binaries without executing them.
- A coding CLI must enforce its declared filesystem boundary. If a requested writable
  workspace becomes read-only or a broader sandbox would be required, fail explicitly;
  only an independently isolated runner may authorize a stronger provider mode.

## Reuse validated host bootstraps

Host setup evidence is dated operational knowledge, not an implicit version pin.
Re-resolve unpinned tools on `PATH`, retain package-manager locks, and obtain
authorization before installation. Keep Python dependencies in an ignored project
environment and Node dependencies behind the project's lockfile.

On Linux and macOS, use native paths and platform package managers. On Windows, preserve
native paths and `.cmd` launchers; do not add a POSIX shell to conceal a portability
defect. Keep SSH host verification and browser sandboxes enabled on every platform.

This is a derived project: a derived project must not assume it owns the framework's
`Makefile`, contributor Node closure, or support-host bootstrap scripts. Use the
installed `litai` CLI plus this project's declared package metadata, locks, Flavors, and
test configuration. An on-demand runner may pre-register provider API keys, but its
runner identity and isolation level remain separate execution evidence.

## Preserve authority boundaries

- Specifications decide observable behavior; Flavors decide target requirements;
  pinned skills guide conversion technique.
- Workflows decide stage order and routing policies decide eligible providers/models.
- Skill preferences always yield to explicit specification and Flavor requirements.
- Generated tests represent current generated code, but independent acceptance remains
  verifier-owned and never enters the generation prompt.
- Generated source, tests, CodeGraph databases, objects, binaries, and cache entries are
  derived evidence. None may silently become authored intent.
- Every generated source tree carries a healthy CodeGraph sidecar and a CycloneDX source
  SBOM. Post-build dependency evidence binds the same Component graph and exact build.
- Source caches verify immutable identities on every read, rerun current acceptance,
  and publish only newly accepted candidates. Cache integrity is not origin authenticity.
- A specification-to-source skill may guide planning and implementation but cannot
  grant build, execution, publication, or release authority.
- Before committing a new or modified skill, run the project's pinned NVIDIA
  SkillEvaluator gate. A failed evaluation blocks admission; an unchanged skill must not
  incur evaluator installation or model cost.
- Treat run evidence as a learning observation, not automatic authority. Classify a
  reusable lesson into the narrowest owning Component, Flavor, skill, workflow, routing
  policy, or framework rule; review and rebuild that semantic change before committing
  it. Keep one-off candidate mistakes in compact run history rather than teaching them.
- Fail on missing authority, digest drift, dependency mismatch, ambiguous Flavors,
  sandbox weakening, or output outside the authorized workspace.

## Read only the relevant detail

Use the project definition's `documentation_roots` as the documentation spine. Start
with the [project guide](docs/README.md), then its declared getting-started,
architecture, security, model-generation, cache,
source-promotion, and traceability documents when present. `litai project validate`
checks that declared documentation and onboarding links remain current.

Read the selected Component and its direct interfaces before framework internals. Read
skill architecture before adding or changing skills, source-promotion architecture
before inverse translation, and security policy before generated host execution. Do not
copy detailed protocols into this entry point; keep one canonical explanation and link
to it from project documentation.
