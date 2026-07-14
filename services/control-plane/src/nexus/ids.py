"""Prefixed identifiers for Nexus entities (goal_..., task_..., run_...)."""

import uuid


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"
