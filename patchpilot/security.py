"""Safety boundary for autonomous command execution.

Distilled from the OpenHands SDK security analyzer: commands are risk-
classified before execution, dangerous commands are refused outright,
and secrets are masked before anything reaches logs or reports.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import List, Pattern, Tuple


class CommandRisk(Enum):
    SAFE = "safe"
    CAUTION = "caution"
    DANGEROUS = "dangerous"


class SecurityError(Exception):
    """Raised when a command is refused by the security policy."""


_DANGEROUS_PATTERNS: List[Tuple[Pattern[str], str]] = [
    (re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*(/|~|\$HOME)(\s|$)"), "recursive delete of root or home"),
    (re.compile(r"\bsudo\s+rm\b"), "privileged delete"),
    (re.compile(r"\bsudo\b"), "privilege escalation"),
    (re.compile(r"\bchmod\s+(-[a-zA-Z]*\s+)*777\s+/"), "world-writable root filesystem"),
    (re.compile(r"\b(curl|wget)\b[^|;&]*\|\s*(ba|z|da)?sh\b"), "piping a download into a shell"),
    (re.compile(r"\bcat\s+[^\s]*\.env\b"), "reading environment secrets"),
    (re.compile(r"^\s*(printenv|env)\s*$"), "dumping process environment"),
    (re.compile(r"\b(ssh|scp|rsync)\b"), "remote access / exfiltration channel"),
    (re.compile(r"\bmkfs\b|\bdd\s+if="), "raw disk operation"),
    (re.compile(r":\(\)\s*\{.*\};\s*:"), "fork bomb"),
    (re.compile(r"\bgit\s+push\s+(-[a-zA-Z-]*\s+)*.*--force\b"), "force push"),
]

_CAUTION_PATTERNS: List[Pattern[str]] = [
    re.compile(r"\brm\b"),
    re.compile(r"\bmv\b"),
    re.compile(r"\bchmod\b|\bchown\b"),
    re.compile(r"\b(curl|wget)\b"),
    re.compile(r"\bpip3?\s+install\b|\bnpm\s+install\b|\buv\s+add\b"),
    re.compile(r"\bgit\s+(push|reset|clean|checkout)\b"),
    re.compile(r">\s*/"),
]

_SECRET_PATTERNS: List[Pattern[str]] = [
    # Provider key formats (OpenAI/Anthropic-style sk-, GitHub, AWS, Slack).
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b"),
    # key=value / key: value assignments for sensitive names.
    re.compile(
        r"(?i)\b(api[_-]?key|token|password|passwd|secret|access[_-]?key)\b"
        r"(\s*[:=]\s*)(['\"]?)[^\s'\"]{6,}\3"
    ),
]

MASK = "[MASKED_SECRET]"


class SecurityPolicy:
    """Classify, validate, and sanitize everything PatchPilot executes."""

    def classify_command(self, command: str) -> CommandRisk:
        for pattern, _reason in _DANGEROUS_PATTERNS:
            if pattern.search(command):
                return CommandRisk.DANGEROUS
        for pattern in _CAUTION_PATTERNS:
            if pattern.search(command):
                return CommandRisk.CAUTION
        return CommandRisk.SAFE

    def explain_risk(self, command: str) -> str:
        for pattern, reason in _DANGEROUS_PATTERNS:
            if pattern.search(command):
                return reason
        return "no dangerous pattern matched"

    def validate_command(self, command: str) -> None:
        """Raise SecurityError if the command must not be executed."""
        if self.classify_command(command) is CommandRisk.DANGEROUS:
            raise SecurityError(
                f"Command refused ({self.explain_risk(command)}): {command!r}"
            )

    def mask_secrets(self, text: str) -> str:
        masked = text
        for pattern in _SECRET_PATTERNS:
            if pattern.groups >= 3:
                masked = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", masked)
            else:
                masked = pattern.sub(MASK, masked)
        return masked
