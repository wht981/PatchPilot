"""Typed data models shared across the PatchPilot pipeline.

Distilled from the OpenHands SDK kernel contracts: every step of the loop
(issue -> context -> plan -> patch -> test) exchanges immutable, typed
records so the run is replayable and reportable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class Issue:
    """A parsed local issue file."""

    title: str
    body: str
    raw_text: str
    path: str


@dataclass(frozen=True)
class RepoContext:
    """A compact, LLM-friendly view of the repository."""

    repo_path: str
    file_tree: List[str]
    readme_summary: str
    config_files: List[str]
    test_files: List[str]
    source_files: List[str]
    candidate_files: List[str]
    keywords: List[str]


@dataclass(frozen=True)
class RepairPlan:
    """A repair plan derived from the issue and the repo context."""

    issue_summary: str
    suspected_root_cause: str
    candidate_files: List[str]
    proposed_changes: str
    test_strategy: str
    risk_notes: str


@dataclass(frozen=True)
class FileChange:
    """One applied file modification, with its unified diff."""

    path: str
    reason: str
    diff: str


@dataclass(frozen=True)
class PatchResult:
    """Outcome of one repair attempt."""

    changed_files: List[FileChange]
    attempted: bool
    notes: str


@dataclass(frozen=True)
class CommandExecution:
    """One command executed inside the repo workspace."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool


@dataclass(frozen=True)
class TestResult:
    """Outcome of running the detected test commands."""

    commands: List[CommandExecution]
    passed: bool
    summary: str


@dataclass(frozen=True)
class DebugRound:
    """One debug iteration: failure analysis, new patch, new test run."""

    round_number: int
    failure_analysis: str
    patch: Optional[PatchResult]
    test: Optional[TestResult]


@dataclass
class PatchPilotResult:
    """Full result of a PatchPilot run, consumed by the report writer."""

    issue: Issue
    repo_context: RepoContext
    plan: RepairPlan
    initial_patch: Optional[PatchResult] = None
    initial_test: Optional[TestResult] = None
    baseline_test: Optional[TestResult] = None
    debug_rounds: List[DebugRound] = field(default_factory=list)
    final_status: str = "unknown"
    dry_run: bool = False

    @property
    def all_commands(self) -> List[CommandExecution]:
        commands: List[CommandExecution] = []
        for test in [self.baseline_test, self.initial_test]:
            if test is not None:
                commands.extend(test.commands)
        for round_ in self.debug_rounds:
            if round_.test is not None:
                commands.extend(round_.test.commands)
        return commands

    @property
    def all_changed_files(self) -> List[FileChange]:
        changes: List[FileChange] = []
        if self.initial_patch is not None:
            changes.extend(self.initial_patch.changed_files)
        for round_ in self.debug_rounds:
            if round_.patch is not None:
                changes.extend(round_.patch.changed_files)
        return changes
