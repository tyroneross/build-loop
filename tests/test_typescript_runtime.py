"""One shared entrypoint for the built TypeScript runtime's builtin suite."""
from pathlib import Path
import subprocess

import pytest


@pytest.mark.integration
@pytest.mark.typescript_runtime
def test_typescript_runtime() -> None:
    root = Path(__file__).resolve().parents[1]
    try:
        result = subprocess.run(
            ["npm", "run", "test:traces"], cwd=root, capture_output=True,
            text=True, timeout=120,
        )
    except subprocess.TimeoutExpired as error:
        pytest.fail(
            f"npm run test:traces exceeded 120 seconds\n"
            f"stdout: {error.stdout!r}\nstderr: {error.stderr!r}"
        )
    except OSError as error:
        pytest.fail(f"TypeScript runtime command npm run test:traces failed: {error}")
    assert result.returncode == 0, (
        f"npm run test:traces exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
