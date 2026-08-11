"""Diff the two engines' output, field by field.

Run via ``make parity``, which builds and executes both sides first. Any
difference is a bug in one of them: the demo is meant to be the same engine,
not a similar one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

#: Floating-point noise between Python and JavaScript is tolerated. Nothing else is.
TOLERANCE = 1e-6


def compare(path: str, left, right, problems: list[tuple[str, object, object]]) -> None:
    if isinstance(left, float) or isinstance(right, float):
        if left is None or right is None:
            if left != right:
                problems.append((path, left, right))
        elif abs(float(left) - float(right)) > TOLERANCE:
            problems.append((path, left, right))
        return

    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            compare(f"{path}.{key}", left.get(key), right.get(key), problems)
        return

    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            problems.append((f"{path}[length]", len(left), len(right)))
            return
        for index, (a, b) in enumerate(zip(left, right)):
            compare(f"{path}[{index}]", a, b, problems)
        return

    if left != right:
        problems.append((path, left, right))


def main() -> int:
    here = Path(__file__).resolve().parent
    server = json.loads((here / ".server.json").read_text())
    demo = json.loads((here / ".demo.json").read_text())

    if len(server) != len(demo):
        print(f"✗ {len(server)} server cases against {len(demo)} demo cases")
        return 1

    problems: list[tuple[str, object, object]] = []
    for left, right in zip(server, demo):
        compare(left["name"], left, right, problems)

    for case in server:
        route = case["route"]
        print(
            f"  {case['name']:<44} {case['decision']:<22} "
            f"score={route['opportunity_score']} coverage={route['coverage']}"
        )

    print()
    if not problems:
        print(f"✓ The two engines agree on every field across {len(server)} case(s).")
        return 0

    print(f"✗ {len(problems)} difference(s) between the server and the demo:\n")
    for path, left, right in problems:
        print(f"  {path}\n     server: {left!r}\n     demo:   {right!r}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
