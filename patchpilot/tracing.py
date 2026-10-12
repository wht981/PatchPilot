"""Execution tracing for PatchPilot runs.

Every pipeline step appends an immutable, timestamped event, and the
whole trace is persisted as JSON so a run can be audited after the
fact. All payloads pass through the security policy's secret masking
before being stored.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from patchpilot.security import SecurityPolicy


class Tracer:
    def __init__(self, policy: Optional[SecurityPolicy] = None) -> None:
        self.policy = policy or SecurityPolicy()
        self.events: List[Dict[str, Any]] = []

    def record(self, event_type: str, **data: Any) -> None:
        self.events.append(
            {
                "time": datetime.now(timezone.utc).isoformat(),
                "type": event_type,
                "data": self.policy.sanitize(data),
            }
        )

    def save(self, path: str) -> None:
        Path(path).write_text(
            json.dumps({"events": self.events}, indent=2, default=str),
            encoding="utf-8",
        )
