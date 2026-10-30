"""Planner: question -> research brief + sub-questions, as validated JSON (plan as data, not prose)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import get_settings
from app.llm.prompts import PLAN_SYSTEM, PLAN_USER, PLAN_VERSION, Prompt
from app.llm.schemas import Plan


@dataclass(frozen=True)
class Depth:
    max_sub_questions: int
    sources_per_task: int
    max_steps: int
    max_words: int


def depth_profile(depth: str) -> Depth:
    settings = get_settings()
    profiles = {
        "quick": Depth(2, 2, 1, 400),
        "standard": Depth(min(4, settings.max_sub_questions), 3, min(2, settings.max_steps_per_agent), 800),
        "deep": Depth(settings.max_sub_questions, 4, settings.max_steps_per_agent, 1500),
    }
    return profiles[depth]


class PlanError(ValueError):
    pass


def validate_plan(plan: Plan, max_sub_questions: int) -> Plan:
    """Schema is already checked by Pydantic; this enforces the caps and that dependencies form a DAG."""
    ids = [q.id for q in plan.sub_questions]
    if len(ids) > max_sub_questions:
        raise PlanError(f"The plan has {len(ids)} sub-questions; the limit for this depth is {max_sub_questions}.")
    if len(set(ids)) != len(ids):
        raise PlanError("Sub-question ids must be unique.")
    known = set(ids)
    for q in plan.sub_questions:
        unknown = [d for d in q.depends_on if d not in known]
        if unknown:
            raise PlanError(f"{q.id} depends on unknown sub-question(s): {', '.join(unknown)}.")
        if q.id in q.depends_on:
            raise PlanError(f"{q.id} cannot depend on itself.")
    # Kahn's algorithm: if we cannot order every node, there is a cycle.
    remaining = {q.id: set(q.depends_on) for q in plan.sub_questions}
    while remaining:
        ready = [k for k, deps in remaining.items() if not deps]
        if not ready:
            raise PlanError(f"The dependencies contain a cycle among: {', '.join(sorted(remaining))}.")
        for key in ready:
            del remaining[key]
        for deps in remaining.values():
            deps.difference_update(ready)
    return plan


def plan_prompt(question: str, depth: str) -> Prompt:
    profile = depth_profile(depth)
    return Prompt(
        feature="plan", version=PLAN_VERSION,
        system=PLAN_SYSTEM.format(max_sub=profile.max_sub_questions, max_words=profile.max_words),
        user=PLAN_USER.format(question=question, depth=depth, max_sub=profile.max_sub_questions,
                              today=datetime.now(timezone.utc).date().isoformat()),
    )


def plan_to_tasks(plan: Plan) -> list[dict]:
    """Give each sub-question a task id and rewrite depends_on from planner keys to task ids."""
    ids = {q.id: uuid.uuid4() for q in plan.sub_questions}
    return [{"id": ids[q.id], "key": q.id, "position": i, "question": q.ask, "why": q.why,
             "depends_on": [str(ids[d]) for d in q.depends_on]} for i, q in enumerate(plan.sub_questions)]
