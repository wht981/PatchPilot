"""Detect and run the repository's test suite.

Detection priority:
1. Python test files + pytest installed      -> python -m pytest
2. Python test files (no pytest available)   -> python -m unittest discover
3. package.json with a real test script      -> npm test
4. Python files but no tests                 -> python -m compileall
5. otherwise                                 -> no test command detected
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

from patchpilot.execution import DEFAULT_TIMEOUT_SECONDS, run_command
from patchpilot.models import CommandExecution, TestResult
from patchpilot.security import SecurityPolicy


PYTHON = sys.executable or "python3"


def _has_python_test_files(repo: Path) -> bool:
    return any(repo.rglob("test_*.py")) or any(repo.rglob("*_test.py"))


def _has_python_files(repo: Path) -> bool:
    return any(repo.rglob("*.py"))


def _npm_test_script(repo: Path) -> Optional[str]:
    package_json = repo / "package.json"
    if not package_json.is_file():
        return None
    try:
        scripts = json.loads(package_json.read_text(encoding="utf-8")).get(
            "scripts", {}
        )
    except (json.JSONDecodeError, OSError):
        return None
    script = scripts.get("test", "")
    if script and "no test specified" not in script:
        return "npm test"
    return None


def detect_test_commands(repo_path: str) -> List[str]:
    """Return the commands PatchPilot should use to verify the repo."""
    repo = Path(repo_path)
    if _has_python_test_files(repo):
        if importlib.util.find_spec("pytest") is not None:
            return [f"{PYTHON} -m pytest -q"]
        return [f"{PYTHON} -m unittest discover -v"]
    npm = _npm_test_script(repo)
    if npm:
        return [npm]
    if _has_python_files(repo):
        return [f"{PYTHON} -m compileall -q ."]
    return []


def run_tests(
    repo_path: str,
    commands: List[str],
    policy: Optional[SecurityPolicy] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> TestResult:
    """Run the detected commands inside the repo; all must exit 0 to pass."""
    if not commands:
        return TestResult(commands=[], passed=False, summary="No test command detected.")

    # Hermetic verification: stale .pyc files can silently execute the
    # pre-patch code when a patch write keeps the same file size within
    # the same mtime second (CPython's timestamp-based cache check),
    # making a correct fix look like a failure. Bytecode caches may also
    # live outside the repo (e.g. Apple's Python uses a pycache prefix
    # under ~/Library/Caches), so purging __pycache__ is not enough:
    # point every verification run at a fresh, empty cache prefix and
    # forbid writing new bytecode.
    _purge_bytecode_caches(repo_path)
    cache_prefix = tempfile.mkdtemp(prefix="patchpilot_pyc_")
    hermetic_env = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": cache_prefix,
    }

    executions: List[CommandExecution] = []
    try:
        for command in commands:
            execution = run_command(
                command, repo_path, timeout=timeout, policy=policy,
                extra_env=hermetic_env,
            )
            executions.append(execution)
    finally:
        shutil.rmtree(cache_prefix, ignore_errors=True)

    passed = all(e.exit_code == 0 for e in executions)
    failed = [e for e in executions if e.exit_code != 0]
    if passed:
        summary = f"All {len(executions)} test command(s) passed."
    else:
        first = failed[0]
        detail = "timed out" if first.timed_out else f"exit code {first.exit_code}"
        summary = f"{len(failed)} of {len(executions)} command(s) failed ({first.command}: {detail})."
    return TestResult(commands=executions, passed=passed, summary=summary)


def _purge_bytecode_caches(repo_path: str) -> None:
    for pycache in Path(repo_path).rglob("__pycache__"):
        shutil.rmtree(pycache, ignore_errors=True)


def failure_output(result: TestResult) -> str:
    """Concatenated stdout/stderr of failed commands, for debug analysis."""
    chunks = []
    for execution in result.commands:
        if execution.exit_code != 0:
            chunks.append(execution.stdout)
            chunks.append(execution.stderr)
    return "\n".join(c for c in chunks if c)
