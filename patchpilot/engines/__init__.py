"""Pluggable repair engines.

The default heuristic engine lives in patchpilot.planner /
patchpilot.patch_runner. This package hosts engines with external
dependencies, imported lazily so the core stays dependency-free.
"""
