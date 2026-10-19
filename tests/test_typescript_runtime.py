"""One shared entrypoint for the built TypeScript runtime's builtin suite."""
from pathlib import Path
import json
import shutil
import subprocess
import tempfile

import pytest


@pytest.mark.integration
@pytest.mark.typescript_runtime
def test_typescript_runtime() -> None:
    root = Path(__file__).resolve().parents[1]
    # Compile in an isolated project with the same relative layout so source
    # maps remain comparable. Verification must not repair stale shipped dist.
    temporary_root = root / ".build-loop"
    temporary_root.mkdir(exist_ok=True)
    # The ignored child project resolves dependencies from the real ancestor
    # node_modules without privileged directory symlinks on Windows.
    with tempfile.TemporaryDirectory(prefix="runtime-check-", dir=temporary_root) as directory:
        project = Path(directory)
        shutil.copytree(root / "src", project / "src")
        for config in root.glob("tsconfig*.json"):
            shutil.copy2(config, project / config.name)
        compiler = ["node", str(root / "node_modules/typescript/bin/tsc"), "--project", str(project / "tsconfig.json")]
        try:
            shown = subprocess.run(compiler + ["--showConfig"], cwd=project,
                                   capture_output=True, text=True, timeout=30)
            assert shown.returncode == 0, f"Cannot inspect compiler configuration: {shown.stdout}\n{shown.stderr}"
            options = json.loads(shown.stdout)['compilerOptions']
            # --showConfig resolves inherited paths before any emission. A
            # copied config may otherwise write back into the real checkout.
            assert not options.get('outFile'), 'outFile builds require an explicit isolated verification contract'
            assert not any(options.get(k) for k in ('generateTrace', 'generateCpuProfile')), 'Compiler diagnostic file emission is unsupported in this gate'
            assert (project / options.get('outDir', '.')).resolve() == project / 'dist', 'Compiler outDir must be the isolated project dist'
            for key in ('declarationDir', 'tsBuildInfoFile'):
                if options.get(key):
                    assert (project / options[key]).resolve().is_relative_to(project), f'Compiler {key} escapes the isolated project'
            if options.get('incremental') or options.get('composite'):
                compiler += ['--tsBuildInfoFile', str(project / '.runtime.tsbuildinfo')]
            built = subprocess.run(
                compiler,
                cwd=project, capture_output=True, text=True, timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            pytest.fail(f"Isolated TypeScript compilation failed: {error}")
        assert built.returncode == 0, f"Isolated TypeScript compilation failed:\n{built.stdout}\n{built.stderr}"
        compiled = {p.relative_to(project / "dist"): p.read_bytes()
                    for p in (project / "dist").rglob("*") if p.is_file()}
        shipped = {p.relative_to(root / "dist"): p.read_bytes()
                   for p in (root / "dist").rglob("*") if p.is_file()}
        differences = sorted(str(name) for name in compiled.keys() | shipped.keys()
                             if compiled.get(name) != shipped.get(name))
        assert not differences, f"Shipped dist differs from source; run npm run build and review: {differences}"
    try:
        result = subprocess.run(
            ["npm", "run", "test:runtime"], cwd=root, capture_output=True,
            text=True, timeout=120,
        )
    except subprocess.TimeoutExpired as error:
        pytest.fail(
            f"npm run test:runtime exceeded 120 seconds\n"
            f"stdout: {error.stdout!r}\nstderr: {error.stderr!r}"
        )
    except OSError as error:
        pytest.fail(f"TypeScript runtime command npm run test:runtime failed: {error}")
    assert result.returncode == 0, (
        f"npm run test:runtime exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
