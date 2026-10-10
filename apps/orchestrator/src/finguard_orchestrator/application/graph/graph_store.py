"""Canonical dialogue reads shared by graph workflow slices."""

from uuid import UUID

from finguard_orchestrator.application.graph import codec
from finguard_orchestrator.application.graph.graph_state import GraphContext, TurnGraphState
from finguard_orchestrator.domain.engine.state import DialogueState
from finguard_orchestrator.domain.ports.dialogue_store import StaleDialogueError, TurnCompletion


async def canonical_state(state: TurnGraphState, ctx: GraphContext) -> DialogueState:
    """Load the account-scoped state once per invocation and reject stale expected versions."""
    if "dialogue" not in ctx.cache:
        conversation_id = UUID(state["conversation_id"])
        held = await ctx.ports.store.load(conversation_id, account_id=UUID(state["account_id"]))
        current = held or DialogueState(conversation_id)
        if current.version != state["expected_version"]:
            raise StaleDialogueError(current)
        ctx.cache["dialogue"] = current
    dialogue: DialogueState = ctx.cache["dialogue"]
    return dialogue


async def commit_state(
    ctx: GraphContext,
    dialogue: DialogueState,
    result: object,
    *,
    account_id: UUID,
    expected_version: int,
) -> DialogueState:
    """CAS the conversation and reserve its exact response in one Postgres transaction."""
    completion = None
    if ctx.request_id is not None and ctx.lease_token is not None:
        completion = TurnCompletion(
            request_id=ctx.request_id,
            lease_token=ctx.lease_token,
            result_payload=codec.dumps_typed(result),
            model_calls=max(0, ctx.ports.count_calls() - ctx.call_offset),
            financial_reads=ctx.financial_reads,
        )
    return await ctx.ports.store.save(
        dialogue,
        account_id=account_id,
        expected_version=expected_version,
        completion=completion,
    )
