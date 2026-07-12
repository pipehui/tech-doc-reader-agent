import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "script",
    [
        "scripts/benchmark_latency.py",
        "scripts/seed_doc_store.py",
    ],
)
def test_script_file_entrypoint_can_import_shared_project_modules(script):
    result = subprocess.run(
        [sys.executable, script, "--help"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.casefold()
