"""Bounded command execution inside the repo workspace.

Distilled from the OpenHands SDK LocalWorkspace.execute_command contract:
every command is validated by the security policy, runs with the repo as
its working directory, is killed on timeout, and returns a typed
CommandExecution with stdout/stderr/exit code/duration.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Optional

from patchpilot.models import CommandExecution
from patchpilot.security import SecurityPolicy


DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_CAPTURED_OUTPUT = 20_000


def run_command(
    command: str,
    repo_path: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    policy: Optional[SecurityPolicy] = None,
) -> CommandExecution:
    """Run ``command`` inside ``repo_path`` with a hard timeout.

    Raises SecurityError (via the policy) before anything executes if the
    command is classified as dangerous.
    """
    policy = policy or SecurityPolicy()
    policy.validate_command(command)

    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        raise NotADirectoryError(f"Workspace does not exist: {repo_path}")

    start = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        exit_code = completed.returncode
        stdout, stderr = completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = -1
        stdout = _decode(exc.stdout)
        stderr = _decode(exc.stderr) + f"\n[timed out after {timeout}s]"
    duration = time.monotonic() - start

    return CommandExecution(
        command=command,
        exit_code=exit_code,
        stdout=policy.mask_secrets(stdout[-MAX_CAPTURED_OUTPUT:]),
        stderr=policy.mask_secrets(stderr[-MAX_CAPTURED_OUTPUT:]),
        duration_seconds=round(duration, 3),
        timed_out=timed_out,
    )


def _decode(raw: "bytes | str | None") -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw
