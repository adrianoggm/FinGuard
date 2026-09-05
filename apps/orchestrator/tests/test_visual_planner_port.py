import asyncio

import pytest
from support.visual_planners import ScriptedVisualPlanner

from finguard_orchestrator.domain.models.chart import DEFAULT_PRESENTATION, VisualDataset, VisualRow
from finguard_orchestrator.domain.models.visual_plan import ProposedEncoding, VisualPlanDraft
from finguard_orchestrator.domain.ports.visual_planner import VisualPlannerPort
from finguard_orchestrator.domain.services.visual_profile import visual_profile_of


def _profile():
    return visual_profile_of(
        VisualDataset(metric="SUM", rows=(VisualRow("A", 100), VisualRow("B", 50))),
        total_minor=None,
        group_by="NONE",
    )


async def test_the_scripted_planner_conforms_to_the_port_and_records_what_it_saw() -> None:
    draft = VisualPlanDraft("bar", (ProposedEncoding("y", "dimension", "nominal"),))
    planner = ScriptedVisualPlanner(draft)
    assert isinstance(planner, VisualPlannerPort)

    got = await planner.plan(
        "¿en qué gasto más?", _profile(), DEFAULT_PRESENTATION, budget_seconds=2.0
    )

    assert got is draft
    assert planner.received[0][0] == "¿en qué gasto más?"
    assert planner.received[0][3] == 2.0


async def test_the_scripted_planner_can_abstain_raise_or_stall() -> None:
    assert (
        await ScriptedVisualPlanner(None).plan(
            "q", _profile(), DEFAULT_PRESENTATION, budget_seconds=1
        )
        is None
    )
    with pytest.raises(RuntimeError):
        await ScriptedVisualPlanner(RuntimeError("down")).plan(
            "q", _profile(), DEFAULT_PRESENTATION, budget_seconds=1
        )
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            ScriptedVisualPlanner(0.2).plan(
                "q", _profile(), DEFAULT_PRESENTATION, budget_seconds=0.05
            ),
            timeout=0.05,
        )
