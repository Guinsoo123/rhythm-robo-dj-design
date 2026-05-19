#!/usr/bin/env python3
"""Rewrite `math` backticks to $...$ in Markdown (skip fenced code blocks)."""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from sync_notion import backtick_to_latex  # noqa: E402

BACKTICK_RE = re.compile(r"`([^`]+)`")


def convert_line(line: str) -> str:
    def repl(match: re.Match[str]) -> str:
        inner = match.group(1)
        latex = backtick_to_latex(inner)
        if latex:
            return f"${latex}$"
        # Keep non-math inline code readable in GitHub (paths, fence names).
        if re.fullmatch(r"[\w./\-]+", inner) and inner.isascii():
            return f"`{inner}`"
        return inner

    return BACKTICK_RE.sub(repl, line)


def convert_file(path: pathlib.Path) -> int:
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    in_fence = False
    changes = 0

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        new_line = convert_line(line)
        if new_line != line:
            changes += 1
        out.append(new_line)

    text = "\n".join(out)
    if lines and path.read_text(encoding="utf-8") and not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")
    return changes


def main() -> int:
    targets = [ROOT / "02-算法设计/README_摇滚机器人跳舞原理.md"]
    if len(sys.argv) > 1:
        targets = [pathlib.Path(p) for p in sys.argv[1:]]

    for path in targets:
        n = convert_file(path)
        print(f"{path.relative_to(ROOT)}: {n} lines updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
