#!/usr/bin/env python3
"""Keep the browsable examples self-locating and useful in source panes."""

import ast
import pathlib


ROOT = pathlib.Path(__file__).resolve().parent.parent


def main() -> int:
    failures = []
    files = sorted((ROOT / "examples").rglob("*.py"))
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        try:
            doc = ast.get_docstring(ast.parse(path.read_text())) or ""
        except (OSError, SyntaxError) as exc:
            failures.append(f"{relative}: cannot inspect: {exc}")
            continue
        expected = f"Path: /{relative}"
        if expected not in doc:
            failures.append(f"{relative}: module docstring lacks {expected!r}")
        if path.name != "__init__.py" and len(doc.split()) < 18:
            failures.append(f"{relative}: teaching description is too short")

    print("examples_teaching_test")
    if failures:
        for failure in failures:
            print("  FAIL ", failure)
        print(f"\n{len(files) - len(failures)} passed, {len(failures)} failed")
        return 1
    print(f"  PASS  {len(files)} Python sources identify their VFS path and purpose")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
