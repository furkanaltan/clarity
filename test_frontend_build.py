from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "frontend" / "index.html"
BUILDER = ROOT / "scripts" / "build_frontend_release.py"


class FrontendBuildTests(unittest.TestCase):
    def test_source_uses_commit_placeholder(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertEqual(
            source.count('<meta name="rove-frontend-build" content="frontend-__GIT_COMMIT__">'),
            1,
        )
        self.assertNotIn("20260911-a91c7e4", source)

    def test_release_artifact_uses_current_commit_id(self):
        commit = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "index.html"
            result = subprocess.run(
                [sys.executable, str(BUILDER), "--allow-dirty", str(output)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            release = output.read_text(encoding="utf-8")

        self.assertIn(f'content="frontend-{commit}"', release)
        self.assertNotIn("__GIT_COMMIT__", release)
        self.assertRegex(
            result.stdout,
            rf"commit={re.escape(commit)}\nbuild_id=frontend-{re.escape(commit)}",
        )


if __name__ == "__main__":
    unittest.main()
