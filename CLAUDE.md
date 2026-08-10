# CLAUDE.md — Project Conventions for AI-Assisted Repositories

This file is inherited by every repository cloned from ai-template.
It tells Claude Code (and any other AI assistant) what is expected automatically, without the user having to ask.

---

## Project Structure

Every project MUST have the following top-level directories and files:

- `tests/` — All test files go here (not scattered in `src/__tests__/`, `lib/test/`, etc.)
- `docs/` — All documentation goes here (architecture, setup guides, API docs, etc.)
- `README.md` — Unique to this project (see README section below)
- `Makefile` — With at least the required targets (see Makefile section below)
- `LICENSE` — Project license file

## Behavior Switches (`skills/config.yaml`)

Before enforcing convention-level rules, read `skills/config.yaml` in the repository root.
If the file is missing, use these defaults:

- `behavior_switches.provenance_story.enabled: true`
- `behavior_switches.provenance_story.require_readme_section: true`
- `behavior_switches.provenance_story.update_chronicle: true`

Downstream repositories created from this template can override these switches to opt out of specific conventions.

## README.md

Every project README must be **unique to the project** — not the generic ai-template README.
When creating or rewriting a README for a new project:

1. Write a project-specific README covering: what it is, features, quick start, configuration, development, and deployment.
2. If `behavior_switches.provenance_story.enabled` and `behavior_switches.provenance_story.require_readme_section` are both `true`, **include the PROVENANCE origin story**. Use the PROVENANCE skill (`skills/PROVENANCE.md`) to write a new chapter in the "Totally True and Not At All Embellished History" chronicle. Place it near the end of the README, before the License section.
3. If step 2 is active and `behavior_switches.provenance_story.update_chronicle` is `true`, follow the full PROVENANCE checklist: determine the next Part number, update the previous Part's forward links and project count, write the new chapter, and update the chronicle table in `skills/PROVENANCE.md`.
4. If either provenance switch is off, the origin story is optional and must not be enforced.

If the README still looks like the generic ai-template README (for example, title still `ai-template` and unchanged template sections), it has not been customized yet — fix it.

## Makefile

Every project MUST have a top-level `Makefile` with at least these targets:

| Target | Description |
|--------|-------------|
| `make` / `make all` | Default build (install deps + compile/bundle) |
| `make start` | Build and start the application |
| `make stop` | Stop the running application |
| `make restart` | Stop then start |
| `make test` | Run all tests |
| `make clean` | Remove build artifacts, deps, generated files |

Additional targets (`lint`, `status`, `configure`, `help`, etc.) are encouraged but not required.

## Testing

- **All tests MUST go in the `tests/` directory** at the top level of the repository.
- **Code coverage must be at least 70%.** When writing new code, write tests to cover it. When modifying existing code, check coverage and add tests if it falls below 70%.
- **All tests must pass before push.** Do not push code with failing tests. Run `make test` and verify before any push.
- Test files should mirror the source structure (e.g., `tests/backend/sessionBroker.test.ts` for `backend/src/services/sessionBroker.ts`).
- Use the project's existing test framework (Jest, Vitest, pytest, etc.) — do not introduce a second test runner.

## Code Quality

- Write safe, secure code. Follow OWASP top-10 guidelines.
- Do not introduce security vulnerabilities (command injection, XSS, SQL injection, etc.).
- Keep solutions simple. Do not over-engineer.
- Do not add features, refactoring, or "improvements" beyond what was requested.

## Skills

Reusable AI prompt templates live in `skills/`. See `skills/README.md` for the index.
When a skill applies to the current task, use it. Key skills:

- **PROVENANCE** — Write the project origin story chapter when provenance switches are enabled in `skills/config.yaml`


<!-- BEGIN MAC INTEGRATION -->
## MAC Task Ledger

This project uses **mac** (`mac task` / `mac project`) for issue tracking.
The hub ledger is authoritative. Project name: **pythonos**.
Do not run `bd`. See https://github.com/jordanhubbard/mac.

### Quick Reference

```bash
mac task list --project pythonos --state=open
mac task list --project pythonos
mac task show <task_id>
mac task create "title" --project pythonos --description-file desc.txt
mac task claim <task_id> <agent_id>
mac task close <task_id> --reason "..."
mac memory remember <key> "content" --project pythonos
```

Dispatch is paused, so `mac task ready` stays empty until
`mac project activate pythonos`.

### Rules

- Use `mac task` for ALL task tracking — do NOT use TodoWrite, TaskCreate, markdown TODO lists, or `bd`
- Use `mac memory remember` for persistent knowledge — do NOT use MEMORY.md files
- `.tickets/` is an optional gitignored local mirror; do not commit it
- Legacy `.beads/` is frozen import history

## Session Completion

When ending a work session:

1. File remaining work with `mac task create --project pythonos`
2. Run quality gates if code changed (`make test-chipset`; `make test` when Docker/QEMU are available)
3. Close or update mac tasks to match reality
4. Commit and push only when the user asks
<!-- END MAC INTEGRATION -->

<!-- literate-ai: See SKILL.md for project-specific agent guidance. -->
