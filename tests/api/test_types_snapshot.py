"""examples/types.ts must equal the output of `python -m app.cli openapi-ts`."""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.api

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "examples" / "types.ts"
REGENERATE = "uv run python -m app.cli openapi-ts --output examples/types.ts"


def test_examples_types_ts_matches_the_generated_output(tmp_path: Path) -> None:
    assert SNAPSHOT.is_file(), f"examples/types.ts is missing; generate it with: {REGENERATE}"
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "openapi-ts"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    generated = result.stdout
    if not generated:  # the command may write to a file only
        out = tmp_path / "types.ts"
        retry = subprocess.run(
            [sys.executable, "-m", "app.cli", "openapi-ts", "--output", str(out)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert retry.returncode == 0, retry.stderr
        generated = out.read_text(encoding="utf-8")

    committed = SNAPSHOT.read_text(encoding="utf-8")
    assert committed.replace("\r\n", "\n") == generated.replace("\r\n", "\n"), (
        f"examples/types.ts is stale (the API changed). Regenerate it with: {REGENERATE}"
    )
