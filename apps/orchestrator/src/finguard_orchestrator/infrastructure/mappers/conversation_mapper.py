"""The conversation, between the domain and the wire.

Both directions in one module because they have to agree: the client sends back what it was given,
so anything this drops on the way out is state the next turn does not have. A round-trip test is the
only honest way to check that, and it lives beside this.

Coming *in*, everything is input. The payload crossed the network and a caller can send anything, so
each piece is rebuilt through the domain type that validates it — a payment of nothing, a category
that is not one, a rank of forty thousand are all refused at the edge with a 422 rather than three
layers in with an apology.
"""

import uuid

from finguard_orchestrator.domain.enums import Action
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.models.conversation import (
    ActiveWorkflow,
    Conversation,
    FinancialClarification,
    PaymentConfirmation,
    PaymentInProgress,
    Slot,
    UnsupportedCapability,
    Workflow,
    workflow_of,
)
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements
from finguard_orchestrator.infrastructure.dto.llm_dto import (
    ConversationPayload,
    PaymentDraftPayload,
    PlanPayload,
    SpokenContactResponse,
)
from finguard_orchestrator.infrastructure.mappers.llm_mapper import (
    draft_to_domain,
    plan_to_domain,
)


def plan_payload(plan: FinancialPlan | FinancialPlanDraft | TopMovements) -> PlanPayload:
    """A plan or a half-built one, in fields.

    One function for both because the payload is the same shape and the difference is exactly what
    the payload already allows: a draft has no window, and `period` and the range are optional.
    """
    common = PlanPayload(
        direction=plan.direction,
        period=plan.period.period if plan.period else None,
        n=plan.period.n if plan.period else None,
        start=plan.date_range.start if plan.date_range else None,
        end=plan.date_range.end if plan.date_range else None,
        categories=list(plan.categories),
        merchants=list(plan.merchants),
    )
    if isinstance(plan, TopMovements):
        # No grouping and no metric: the payload keeps their defaults rather than inventing values a
        # caller could echo back into an aggregate.
        return common.model_copy(update={"action": Action.RANK_MOVEMENTS, "rank": plan.rank})
    return common.model_copy(
        update={
            "group_by": plan.group_by,
            "metric": plan.metric,
            "action": Action.AGGREGATE,
        }
    )


def query_to_domain(payload: PlanPayload) -> FinancialPlan | TopMovements:
    """The last answered question, whichever kind it was.

    The tag decides, not the fields: a payload with a rank *and* a grouping is a caller
    improvising, and the tag keeps the two shapes from blending into something neither service
    issues.
    """
    if payload.action is Action.RANK_MOVEMENTS:
        absolute = payload.start is not None and payload.end is not None
        return TopMovements(
            direction=payload.direction,
            period=RelativePeriod(period=payload.period, n=payload.n) if payload.period else None,
            date_range=AbsoluteRange(start=payload.start, end=payload.end) if absolute else None,  # type: ignore[arg-type]
            categories=tuple(payload.categories),
            merchants=tuple(payload.merchants),
            rank=payload.rank or 1,
        )
    return plan_to_domain(payload)


def to_domain(payload: ConversationPayload | None) -> Conversation:
    """The conversation a caller echoed back.

    An absent payload is an idle conversation rather than an error: the first turn has none, and a
    client that forgets to send one gets a fresh conversation instead of a rejection.

    A payload whose `workflow` does not match the fields it carries — PAYMENT_CONFIRMATION with no
    intent, a clarification with no draft — becomes idle too. It cannot have come from here, and
    idle is the reading that cannot act on it.
    """
    if payload is None or payload.workflow is None:
        return Conversation(
            last_plan=query_to_domain(payload.last_plan) if payload and payload.last_plan else None,
            retry_plan=(
                query_to_domain(payload.retry_plan) if payload and payload.retry_plan else None
            ),
        )

    last_plan = query_to_domain(payload.last_plan) if payload.last_plan else None
    retry_plan = query_to_domain(payload.retry_plan) if payload.retry_plan else None
    active = _active(payload)
    return Conversation(active=active, last_plan=last_plan, retry_plan=retry_plan)


def _active(payload: ConversationPayload) -> ActiveWorkflow | None:
    match payload.workflow:
        case Workflow.FINANCIAL_CLARIFICATION:
            if payload.draft is None:
                return None
            return FinancialClarification(
                draft=draft_to_domain(payload.draft),
                awaiting=payload.awaiting or Slot.WINDOW,
            )
        case Workflow.UNSUPPORTED_CAPABILITY:
            if not payload.capability:
                return None
            return UnsupportedCapability(capability=payload.capability)
        case Workflow.PAYMENT_IN_PROGRESS:
            return PaymentInProgress(
                draft=_payment_to_domain(payload.payment),
                awaiting=payload.awaiting or Slot.AMOUNT,
                candidates=tuple(
                    Contact(uuid.UUID(row.contact_id), row.display_name, row.phone)
                    for row in payload.candidates
                ),
                suspended=payload.suspended,
            )
        case Workflow.PAYMENT_CONFIRMATION:
            if not payload.intent_id or not payload.recipient or payload.amount_minor is None:
                return None
            return PaymentConfirmation(
                intent_id=uuid.UUID(payload.intent_id),
                recipient=payload.recipient,
                amount_minor=payload.amount_minor,
            )
        case _:
            return None


def _payment_to_domain(payload: PaymentDraftPayload | None) -> PaymentDraft:
    if payload is None:
        return PaymentDraft()
    return PaymentDraft(
        recipient_name=payload.recipient_name,
        recipient_contact_id=payload.recipient_contact_id,
        amount_minor=payload.amount_minor,
        currency=payload.currency,
        concept=payload.concept,
    )


def to_payload(conversation: Conversation) -> ConversationPayload:
    """What the client stores and sends back.

    Everything the next turn needs is here, and nothing else: a client that reads this to decide
    what to keep is the design this replaced.
    """
    payload = ConversationPayload(
        workflow=workflow_of(conversation.active),
        awaiting=conversation.awaiting,
        last_plan=plan_payload(conversation.last_plan) if conversation.last_plan else None,
        retry_plan=plan_payload(conversation.retry_plan) if conversation.retry_plan else None,
    )
    active = conversation.active

    if isinstance(active, FinancialClarification):
        return payload.model_copy(update={"draft": plan_payload(active.draft)})
    if isinstance(active, UnsupportedCapability):
        return payload.model_copy(update={"capability": active.capability})
    if isinstance(active, PaymentInProgress):
        return payload.model_copy(
            update={
                "payment": PaymentDraftPayload(
                    recipient_name=active.draft.recipient_name,
                    recipient_contact_id=active.draft.recipient_contact_id,
                    amount_minor=active.draft.amount_minor,
                    currency=active.draft.currency,
                    concept=active.draft.concept,
                ),
                "suspended": active.suspended,
                "candidates": [
                    SpokenContactResponse(
                        contact_id=str(contact.contact_id),
                        display_name=contact.display_name,
                        phone=contact.phone,
                    )
                    for contact in active.candidates
                ],
            }
        )
    if isinstance(active, PaymentConfirmation):
        return payload.model_copy(
            update={
                "intent_id": str(active.intent_id),
                "recipient": active.recipient,
                "amount_minor": active.amount_minor,
            }
        )
    return payload
