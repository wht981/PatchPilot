"""Conservative patch execution for the heuristic engine.

The heuristic engine performs bounded, mutation-based program repair:
it derives target functions from failing test names and issue keywords,
then proposes single-token mutations inside those functions only —
arithmetic/comparison operator swaps, boolean `and`/`or` swaps, and
off-by-one integer constants. Each candidate patch touches exactly one
token in one file, records the original content, and produces a unified
diff. Files outside the repository root are never modified.

The pluggable OpenHands engine (see patchpilot.engines) replaces this
strategy with a real LLM agent; the pipeline contract is the same.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from patchpilot.models import FileChange, RepairPlan


MAX_FILES = 2
MAX_CANDIDATES = 16

# Mutation classes, tried in this order: arithmetic/comparison operator
# swaps are the most common single-token bugs, then boolean logic, then
# off-by-one integer constants.
CLASS_OPERATOR = 0
CLASS_BOOLEAN = 1
CLASS_CONSTANT = 2

# (regex matching the operator alone, replacement) — two-char operators are
# matched with lookarounds so `<` never matches inside `<=`, `-` never
# matches inside `->`, etc.
_OPERATOR_MUTATIONS: List[Tuple[str, str, str]] = [
    (r"(?<![+=\-*/<>!])-(?![=>\-])", "-", "+"),
    (r"(?<![+=\-*/<>!])\+(?![+=])", "+", "-"),
    (r"(?<![*=/])\*(?![*=])", "*", "/"),
    (r"(?<![/=])/(?![/=])", "/", "*"),
    (r"(?<![=!<>+\-*/])==(?!=)", "==", "!="),
    (r"!=", "!=", "=="),
    (r"<=", "<=", "<"),
    (r">=", ">=", ">"),
    (r"(?<![<=!\-])<(?![<=])", "<", "<="),
    (r"(?<![>=\-])>(?![>=])", ">", ">="),
]

_BOOLEAN_MUTATIONS: List[Tuple[str, str, str]] = [
    (r"\band\b", "and", "or"),
    (r"\bor\b", "or", "and"),
]

# Standalone integer literals (not part of identifiers, floats, or
# attribute access); each occurrence yields n+1 and n-1 candidates.
_INT_LITERAL = re.compile(r"(?<![\w.])(\d+)(?![\w.])")


@dataclass
class CandidatePatch:
    """One proposed single-token mutation."""

    relative_path: str
    description: str
    original_content: str
    new_content: str
    class_rank: int = CLASS_OPERATOR


class PatchBoundaryError(Exception):
    """Raised when a patch would touch a file outside the repository."""


def extract_target_functions(failure_text: str, plan: RepairPlan) -> List[str]:
    """Derive likely-buggy function names from failing tests and the plan."""
    targets: List[str] = []
    for token in re.findall(r"\btest_([A-Za-z0-9_]+)", failure_text):
        if token not in targets:
            targets.append(token)
    for token in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)\s*\(", plan.issue_summary):
        if token not in targets:
            targets.append(token)
    return targets


def _function_spans(content: str) -> Dict[str, Tuple[int, int]]:
    """Map function name -> (start_line, end_line) using indentation."""
    lines = content.splitlines()
    spans: Dict[str, Tuple[int, int]] = {}
    for i, line in enumerate(lines):
        match = re.match(r"(\s*)def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", line)
        if not match:
            continue
        indent = len(match.group(1))
        end = len(lines)
        for j in range(i + 1, len(lines)):
            stripped = lines[j].strip()
            if stripped and (len(lines[j]) - len(lines[j].lstrip())) <= indent:
                end = j
                break
        spans[match.group(2)] = (i, end)
    return spans


def _mutations_for_span(
    content: str, start: int, end: int, function: str, relative_path: str
) -> List[CandidatePatch]:
    lines = content.splitlines(keepends=True)
    candidates: List[CandidatePatch] = []

    def add(line_number: int, span: "Tuple[int, int]", replacement: str,
            old: str, new: str, class_rank: int) -> None:
        line = lines[line_number]
        new_lines = list(lines)
        new_lines[line_number] = line[: span[0]] + replacement + line[span[1] :]
        candidates.append(
            CandidatePatch(
                relative_path=relative_path,
                description=(
                    f"swap `{old}` -> `{new}` in `{function}` "
                    f"({relative_path}:{line_number + 1})"
                ),
                original_content=content,
                new_content="".join(new_lines),
                class_rank=class_rank,
            )
        )

    for line_number in range(start, min(end, len(lines))):
        code = lines[line_number].split("#", 1)[0]
        for pattern, old_op, new_op in _OPERATOR_MUTATIONS:
            for match in re.finditer(pattern, code):
                add(line_number, match.span(), new_op, old_op, new_op,
                    CLASS_OPERATOR)
        for pattern, old_op, new_op in _BOOLEAN_MUTATIONS:
            for match in re.finditer(pattern, code):
                add(line_number, match.span(), new_op, old_op, new_op,
                    CLASS_BOOLEAN)
        for match in _INT_LITERAL.finditer(code):
            value = int(match.group(1))
            for delta in (1, -1):
                add(line_number, match.span(), str(value + delta),
                    str(value), str(value + delta), CLASS_CONSTANT)

    # Operator swaps first, then boolean logic, then constants; within a
    # class, try `return` lines first — the buggy expression is most
    # often there.
    candidates.sort(
        key=lambda c: (c.class_rank, 0 if "return" in _changed_line(c) else 1)
    )
    return candidates


def _changed_line(candidate: CandidatePatch) -> str:
    for old, new in zip(
        candidate.original_content.splitlines(), candidate.new_content.splitlines()
    ):
        if old != new:
            return new
    return ""


class PatchRunner:
    def __init__(self, repo_path: str, max_files: int = MAX_FILES) -> None:
        self.repo = Path(repo_path).resolve()
        self.max_files = max_files

    def propose_candidates(
        self, plan: RepairPlan, failure_text: str
    ) -> List[CandidatePatch]:
        """Bounded list of single-operator mutations in candidate files."""
        targets = extract_target_functions(failure_text, plan)
        candidates: List[CandidatePatch] = []
        for relative in plan.candidate_files[: self.max_files]:
            path = self._resolve_inside_repo(relative)
            if not path.is_file() or path.suffix != ".py":
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            spans = _function_spans(content)
            matched = [name for name in targets if name in spans]
            names = matched or list(spans)
            for name in names:
                start, end = spans[name]
                candidates.extend(
                    _mutations_for_span(content, start, end, name, relative)
                )
        return candidates[:MAX_CANDIDATES]

    def apply(self, candidate: CandidatePatch, reason: str) -> FileChange:
        path = self._resolve_inside_repo(candidate.relative_path)
        path.write_text(candidate.new_content, encoding="utf-8")
        diff = "".join(
            difflib.unified_diff(
                candidate.original_content.splitlines(keepends=True),
                candidate.new_content.splitlines(keepends=True),
                fromfile=f"a/{candidate.relative_path}",
                tofile=f"b/{candidate.relative_path}",
            )
        )
        return FileChange(path=candidate.relative_path, reason=reason, diff=diff)

    def revert(self, candidate: CandidatePatch) -> None:
        path = self._resolve_inside_repo(candidate.relative_path)
        path.write_text(candidate.original_content, encoding="utf-8")

    def _resolve_inside_repo(self, relative: str) -> Path:
        path = (self.repo / relative).resolve()
        try:
            path.relative_to(self.repo)
        except ValueError:
            raise PatchBoundaryError(
                f"Refusing to touch a file outside the repo: {relative}"
            )
        return path
