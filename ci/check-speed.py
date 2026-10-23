#!/usr/bin/env python3
"""Compare the live session's measured speed (screenshots/speed.json, from
ci/screenshots.sh) with ci/speed-budget.json; a summary table and an error
for each number over its budget.

    ci/check-speed.py SPEED_JSON
"""
import json
import os
import sys
from pathlib import Path

BUDGET = json.loads((Path(__file__).parent / "speed-budget.json").read_text())


def main(argv: list[str]) -> int:
    try:
        measured = json.loads(Path(argv[1]).read_text())
    except (IndexError, OSError, ValueError):
        print("::warning title=Speed::no measurements (the live session didn't come up for the screenshots)")
        return 0
    rows, over, missing = [], 0, []
    for name, limits in BUDGET.items():
        if name.startswith("_"):
            continue
        got = measured.get(name, {})
        for kind, limit in limits.items():
            value = got.get(kind)
            label = f"{name} ({kind})" if kind != "seconds" else name
            if value is None:
                rows.append(f"| ⚠️ | {label} | not measured | {limit} s |")
                missing.append(label)
                continue
            ok = value <= limit
            over += not ok
            rows.append(f"| {'✅' if ok else '❌'} | {label} | {value:.1f} s | {limit} s |")
            if not ok:
                print(f"::error title=Speed: {label}::{value:.1f} s (budget {limit} s)")
    # One line for everything (GitHub keeps only a few annotations per job).
    measured_text = "; ".join(r.split(" | ")[1] + " " + r.split(" | ")[2] for r in rows)
    print(f"::notice title=Speed::{measured_text}")
    if missing:
        print(f"::warning title=Speed::not measured: {', '.join(missing)}")
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a") as fh:
        fh.write("### Speed (live session in a virtual machine)\n\n| | What | Took | Budget |\n|---|---|---|---|\n")
        fh.write("\n".join(rows) + "\n")
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
