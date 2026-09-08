# Agent Instructions

This project uses **mac** for project and task tracking. The hub ledger
(`mac task`) is authoritative. Do not run `bd`. See
[mac](https://github.com/jordanhubbard/mac).

The mac project name is **pythonos**. Fleet auto-dispatch stays paused
until an operator runs `mac project activate pythonos`. While paused,
`mac task ready --project pythonos` is empty; use
`mac task list --project pythonos --state=open` to see unblocked work.

## Quick Reference

```bash
mac project show pythonos
mac task ready --project pythonos
mac task list --project pythonos --state=open
mac task show <task_id>
mac task create "title" --project pythonos --description-file desc.txt
mac task claim <task_id> <agent_id>
mac task close <task_id> --reason "..."
mac memory remember <key> "content" --project pythonos
```

`.tickets/` is an optional gitignored local mirror. Do not commit it.

## Rules

- Use `mac task` for all task tracking — do not use TodoWrite, TaskCreate,
  markdown TODO lists, or `bd`.
- Use `mac memory remember` for persistent knowledge — do not use MEMORY.md.
- Legacy `.beads/` is frozen history from the Beads import. Do not file new
  beads issues.

## Non-Interactive Shell Commands

**ALWAYS use non-interactive flags** with file operations to avoid hanging on confirmation prompts.

Shell commands like `cp`, `mv`, and `rm` may be aliased to include `-i` (interactive) mode on some systems, causing the agent to hang indefinitely waiting for y/n input.

**Use these forms instead:**
```bash
# Force overwrite without prompting
cp -f source dest           # NOT: cp source dest
mv -f source dest           # NOT: mv source dest
rm -f file                  # NOT: rm file

# For recursive operations
rm -rf directory            # NOT: rm -r directory
cp -rf source dest          # NOT: cp -r source dest
```

**Other commands that may prompt:**
- `scp` - use `-o BatchMode=yes` for non-interactive
- `ssh` - use `-o BatchMode=yes` to fail instead of prompting
- `apt-get` - use `-y` flag
- `brew` - use `HOMEBREW_NO_AUTO_UPDATE=1` env var

## Session Completion

When ending a work session:

1. File remaining work with `mac task create --project pythonos`.
2. Run quality gates if code changed (`make test-chipset`; `make test` when Docker/QEMU are available).
3. Close or update mac tasks to match reality.
4. Commit and push only when the user asks.
