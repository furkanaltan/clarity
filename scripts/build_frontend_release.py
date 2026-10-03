#!/usr/bin/env python3
"""Create a deployable frontend artifact stamped with the current Git commit."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "frontend" / "index.html"
BUILD_PATTERN = re.compile(
    r'(<meta name="rove-frontend-build" content=")([^"]+)(">)'
)
PLACEHOLDER = "frontend-__GIT_COMMIT__"


def current_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short=7", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def build_release(output: Path, *, require_clean_source: bool = True) -> tuple[str, Path]:
    if require_clean_source:
        clean = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", "frontend/index.html"],
            cwd=ROOT,
            check=False,
        )
        if clean.returncode != 0:
            raise ValueError("frontend/index.html must be committed before release build")

    source = SOURCE.read_text(encoding="utf-8")
    matches = BUILD_PATTERN.findall(source)
    if len(matches) != 1:
        raise ValueError("frontend build marker must occur exactly once")
    if matches[0][1] != PLACEHOLDER:
        raise ValueError("frontend build marker must remain the Git placeholder")

    commit = current_commit()
    build_id = f"frontend-{commit}"
    release = source.replace(PLACEHOLDER, build_id, 1)
    if PLACEHOLDER in release or build_id not in release:
        raise ValueError("frontend build marker was not resolved")
    if output.resolve() == SOURCE.resolve():
        raise ValueError("release output must not overwrite the canonical source")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(release, encoding="utf-8")
    return build_id, output


def main(argv: list[str]) -> int:
    allow_dirty = len(argv) == 3 and argv[1] == "--allow-dirty"
    if len(argv) not in (2, 3) or (len(argv) == 3 and not allow_dirty):
        print(f"usage: {Path(argv[0]).name} [--allow-dirty] OUTPUT", file=sys.stderr)
        return 2
    try:
        output_arg = argv[2] if allow_dirty else argv[1]
        build_id, output = build_release(
            Path(output_arg).expanduser().resolve(), require_clean_source=not allow_dirty
        )
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"frontend release build failed: {error}", file=sys.stderr)
        return 1
    print(f"commit={build_id.removeprefix('frontend-')}")
    print(f"build_id={build_id}")
    print(f"output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
