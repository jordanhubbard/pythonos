# Skills Index

Reusable AI skills for use with [Claude Code](https://claude.ai/claude-code). Each skill is a prompt template that can be invoked in a project to apply a consistent pattern.

## Behavior Switches

`skills/config.yaml` is the behavior switch file for this template. Downstream repositories can keep defaults or flip switches to opt out of specific conventions.

Current switches:

- `behavior_switches.provenance_story.enabled`
- `behavior_switches.provenance_story.require_readme_section`
- `behavior_switches.provenance_story.update_chronicle`
- `behavior_switches.responsible_vibe_workflow.enabled`

If `config.yaml` is missing, skills should assume default-on behavior.

## Available Files

| File | Type | Description |
|------|------|-------------|
| [PROVENANCE.md](PROVENANCE.md) | Skill | Write a humorous project origin story chapter and chain it into the "Totally True and Not At All Embellished History" chronicle. Includes style guide, character notes, nav link format, and a checklist for adding a new Part. |
| [PYTHONOS_DEBUG.md](PYTHONOS_DEBUG.md) | Skill | Attach a coding agent to a live PythonOS guest, inspect kernel state, and launch/debug GUI software through the TCP REPL. |
| [config.yaml](config.yaml) | Configuration | Controls optional template conventions, including whether PROVENANCE behavior is enforced. |

## How to Use a Skill

Copy the skill file into your project's `.claude/` directory, or reference it directly when prompting Claude Code:

```
Use the PROVENANCE skill from ~/Src/ai-template/skills/PROVENANCE.md to write the origin story for this repository.
```

Or, if the ai-template repo is linked as a Claude Code skill source, invoke it with:

```
/PROVENANCE
```

## Adding a New Skill

1. Create a new `.md` file in this directory named after the skill.
2. Include: when to use it, the pattern/template it applies, style notes, and a checklist.
3. Document any new behavior switches in `config.yaml` if the skill adds optional conventions.
4. Add the skill to the table above.
5. Commit and push.
