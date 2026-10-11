"""Load the holder-scoped catalogues a graph turn may ground against."""

from uuid import UUID

from finguard_orchestrator.domain.engine.grounding import Grounds
from finguard_orchestrator.domain.errors import DirectoryUnreadError, QueryRejectedError
from finguard_orchestrator.infrastructure.graph.graph_state import GraphContext, TurnGraphState


async def grounds_for_state(state: TurnGraphState, ctx: GraphContext) -> Grounds:
    """Read account and contact names once per invocation, under the credential account."""
    if "grounds" not in ctx.cache:
        account_id = UUID(state["account_id"])
        try:
            ctx.cache["grounds"] = Grounds(
                ctx.ports.categories,
                await ctx.ports.accounts.all_of(account_id),
                await ctx.ports.contacts.all_of(account_id),
            )
        except QueryRejectedError as refused:
            raise DirectoryUnreadError(refused.detail) from refused
    grounds: Grounds = ctx.cache["grounds"]
    return grounds
