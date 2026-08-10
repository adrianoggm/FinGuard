"""The conversation, between the domain and the wire.

Both directions in one module because they have to agree: the client sends back what it was given,
so anything this drops on the way out is state the next turn does not have. A round-trip test is the
only honest way to check that, and it lives beside this.

Coming *in*, everything is input. The payload crossed the network and a caller can send anything, so
each piece is rebuilt through the domain type that validates it — a payment of nothing, a category
that is not one, a rank of forty thousand are all refused at the edge with a 422 rather than three
layers in with an apology.
"""

from typing import Any

from finguard_orchestrator.domain.enums import Action, GroupBy, Rail
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.models.conversation import (
    AccountAlias,
    ActiveWorkflow,
    Conversation,
    CounterpartyAlias,
    FinancialClarification,
    PaymentConfirmation,
    PaymentInProgress,
    Slot,
    TransferInProgress,
    UnsupportedCapability,
    Workflow,
    workflow_of,
)
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioDraft, RatioPlan
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
        excluded_categories=list(getattr(plan, "excluded_categories", ())),
        merchants=list(plan.merchants),
        merchant_terms=list(getattr(plan, "merchant_terms", ())),
        excluded_merchants=list(getattr(plan, "excluded_merchants", ())),
        counterparties=list(getattr(plan, "counterparties", ())),
        excluded_counterparties=list(getattr(plan, "excluded_counterparties", ())),
        counterparty_types=list(getattr(plan, "counterparty_types", ())),
        include_internal=getattr(plan, "include_internal", False),
        internal_only=getattr(plan, "internal_only", False),
        across_accounts=getattr(plan, "across_accounts", False),
        only_account_id=getattr(plan, "only_account_id", None),
        only_account_ids=list(getattr(plan, "only_account_ids", ())),
        transaction_ids=list(getattr(plan, "transaction_ids", ())),
        transaction_types=list(getattr(plan, "transaction_types", ())),
        group_limit=getattr(plan, "group_limit", None),
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
            excluded_categories=tuple(payload.excluded_categories),
            merchants=tuple(payload.merchants),
            merchant_terms=tuple(payload.merchant_terms),
            excluded_merchants=tuple(payload.excluded_merchants),
            rank=payload.rank or 1,
        )
    return plan_to_domain(payload)


def to_domain(payload: ConversationPayload | None) -> Conversation:
    """The conversation a caller echoed back.

    An absent payload is an idle conversation rather than an error: the first turn has none, and a
    client that forgets to send one gets a fresh conversation instead of a rejection.

    A payload whose `workflow` does not match the fields it carries — PAYMENT_CONFIRMATION with no
    intent, a clarification with no draft — never reaches this function: the payload refuses itself
    and the caller gets a 422. It used to become idle here, which was quieter and worse. A state
    nobody could have been in is a client bug or an attempt, and silently reinterpreting it as "no
    conversation" tells whoever sent it nothing while discarding whatever was really under way.
    """
    if payload is None or payload.workflow is None:
        return Conversation(
            last_plan=query_to_domain(payload.last_plan) if payload and payload.last_plan else None,
            prior_plan=(
                query_to_domain(payload.prior_plan) if payload and payload.prior_plan else None
            ),
            retry_plan=(
                query_to_domain(payload.retry_plan) if payload and payload.retry_plan else None
            ),
            # The branch this field exists for. A question asked with nothing half-built behind it
            # comes back exactly here — no workflow — and dropping the slot on the way in is what
            # let the same question be asked forever.
            last_asked=payload.last_asked if payload else None,
            answered_the_balance=bool(payload and payload.answered_the_balance),
            referenced_category=payload.referenced_category if payload else None,
            referenced_group_by=(
                payload.referenced_group_by
                if payload and payload.referenced_group_by is not None
                else (GroupBy.CATEGORY if payload and payload.referenced_category else None)
            ),
            referenced_group_key=(
                payload.referenced_group_key
                if payload and payload.referenced_group_key is not None
                else (payload.referenced_category if payload else None)
            ),
            referenced_transaction_ids=(
                tuple(payload.referenced_transaction_ids) if payload else ()
            ),
            last_ratio=_ratio_plan(payload) if payload else None,
            pending_ratio=_ratio_draft(payload) if payload else None,
            last_comparison=_comparison_plan(payload) if payload else None,
            account_aliases=_account_aliases(payload) if payload else (),
            counterparty_aliases=_counterparty_aliases(payload) if payload else (),
            recent_payment_rail=payload.recent_payment_rail if payload else None,
        )

    last_plan = query_to_domain(payload.last_plan) if payload.last_plan else None
    prior_plan = query_to_domain(payload.prior_plan) if payload.prior_plan else None
    retry_plan = query_to_domain(payload.retry_plan) if payload.retry_plan else None
    active = _active(payload)
    return Conversation(
        active=active,
        last_plan=last_plan,
        prior_plan=prior_plan,
        retry_plan=retry_plan,
        last_asked=payload.last_asked,
        answered_the_balance=payload.answered_the_balance,
        referenced_category=payload.referenced_category,
        referenced_group_by=(
            payload.referenced_group_by
            if payload.referenced_group_by is not None
            else (GroupBy.CATEGORY if payload.referenced_category else None)
        ),
        referenced_group_key=payload.referenced_group_key or payload.referenced_category,
        referenced_transaction_ids=tuple(payload.referenced_transaction_ids),
        last_ratio=_ratio_plan(payload),
        pending_ratio=_ratio_draft(payload),
        last_comparison=_comparison_plan(payload),
        account_aliases=_account_aliases(payload),
        counterparty_aliases=_counterparty_aliases(payload),
        recent_payment_rail=payload.recent_payment_rail,
    )


def _account_aliases(payload: ConversationPayload) -> tuple[AccountAlias, ...]:
    return tuple(
        AccountAlias(row.alias, row.account_id, row.display_name) for row in payload.account_aliases
    )


def _counterparty_aliases(payload: ConversationPayload) -> tuple[CounterpartyAlias, ...]:
    return tuple(
        CounterpartyAlias(row.alias, row.counterparty_name, row.counterparty_type)
        for row in payload.counterparty_aliases
    )


def _active(payload: ConversationPayload) -> ActiveWorkflow | None:
    """The state the payload describes.

    Nothing is recovered here any more. The payload's own validator has already refused a workflow
    whose fields do not match it, so a PAYMENT_CONFIRMATION reaching this function has an intent and
    a PAYMENT_IN_PROGRESS has a payment. That is why the asserts below are asserts and not branches:
    they are the invariant restated, not a case a caller can produce.
    """
    match payload.workflow:
        case Workflow.FINANCIAL_CLARIFICATION:
            assert payload.draft is not None  # the payload's validator requires it
            return FinancialClarification(
                draft=draft_to_domain(payload.draft),
                awaiting=payload.awaiting or Slot.WINDOW,
            )
        case Workflow.UNSUPPORTED_CAPABILITY:
            assert payload.capability  # the payload's validator requires it
            return UnsupportedCapability(capability=payload.capability)
        case Workflow.PAYMENT_IN_PROGRESS:
            assert payload.payment is not None  # the payload's validator requires it
            return PaymentInProgress(
                draft=_payment_to_domain(payload.payment),
                awaiting=payload.awaiting or Slot.AMOUNT,
                candidates=tuple(
                    Contact(row.contact_id, row.display_name, row.phone)
                    for row in payload.candidates
                ),
                suspended=payload.suspended,
            )
        case Workflow.TRANSFER_IN_PROGRESS:
            return TransferInProgress(
                to_account_id=payload.transfer_to,
                amount_minor=payload.transfer_amount_minor,
                awaiting=payload.awaiting or Slot.AMOUNT,
            )
        case Workflow.PAYMENT_CONFIRMATION:
            assert payload.intent_id is not None  # the payload's validator requires all three
            assert payload.recipient is not None
            assert payload.amount_minor is not None
            return PaymentConfirmation(
                intent_id=payload.intent_id,
                recipient=payload.recipient,
                amount_minor=payload.amount_minor,
                # The domain's default is EUR, and taking it here is how a preview prepared in
                # dollars came back from a round trip denominated in euros.
                currency=payload.currency or "EUR",
                concept=payload.concept,
                rail=payload.rail or Rail.BIZUM,
                recipient_iban=payload.recipient_iban,
            )
        case _:
            return None


def _payment_to_domain(payload: PaymentDraftPayload) -> PaymentDraft:
    return PaymentDraft(
        recipient_name=payload.recipient_name,
        recipient_contact_id=payload.recipient_contact_id,
        amount_minor=payload.amount_minor,
        currency=payload.currency,
        concept=payload.concept,
        rail=payload.rail,
    )


def to_payload(conversation: Conversation) -> ConversationPayload:
    """What the client stores and sends back.

    Everything the next turn needs is here, and nothing else: a client that reads this to decide
    what to keep is the design this replaced.

    Built in one call rather than a base plus `model_copy` patches, because the payload validates
    itself now: a half-filled PAYMENT_CONFIRMATION is refused, and constructing one on the way to
    filling it would make this function raise on its own correct output.
    """
    return ConversationPayload.model_validate(
        {
            "workflow": workflow_of(conversation.active),
            "awaiting": conversation.awaiting,
            "last_asked": conversation.last_asked,
            "answered_the_balance": conversation.answered_the_balance,
            "referenced_category": conversation.referenced_category,
            "referenced_group_by": conversation.referenced_group_by,
            "referenced_group_key": conversation.referenced_group_key,
            "referenced_transaction_ids": list(conversation.referenced_transaction_ids),
            "account_aliases": [
                {
                    "alias": alias.alias,
                    "account_id": alias.account_id,
                    "display_name": alias.display_name,
                }
                for alias in conversation.account_aliases
            ],
            "counterparty_aliases": [
                {
                    "alias": alias.alias,
                    "counterparty_name": alias.counterparty_name,
                    "counterparty_type": alias.counterparty_type,
                }
                for alias in conversation.counterparty_aliases
            ],
            "recent_payment_rail": conversation.recent_payment_rail,
            "last_plan": plan_payload(conversation.last_plan) if conversation.last_plan else None,
            "prior_plan": (
                plan_payload(conversation.prior_plan) if conversation.prior_plan else None
            ),
            "retry_plan": (
                plan_payload(conversation.retry_plan) if conversation.retry_plan else None
            ),
            "last_ratio_kind": (conversation.last_ratio.kind if conversation.last_ratio else None),
            "last_ratio_part": (
                plan_payload(conversation.last_ratio.part) if conversation.last_ratio else None
            ),
            "last_ratio_whole": (
                plan_payload(conversation.last_ratio.whole) if conversation.last_ratio else None
            ),
            "pending_ratio_kind": (
                conversation.pending_ratio.kind if conversation.pending_ratio else None
            ),
            "pending_ratio_part": (
                plan_payload(conversation.pending_ratio.part)
                if conversation.pending_ratio
                else None
            ),
            "pending_ratio_whole": (
                plan_payload(conversation.pending_ratio.whole)
                if conversation.pending_ratio
                else None
            ),
            "last_comparison_earlier": (
                plan_payload(conversation.last_comparison.earlier)
                if conversation.last_comparison
                else None
            ),
            "last_comparison_latest": (
                plan_payload(conversation.last_comparison.latest)
                if conversation.last_comparison
                else None
            ),
            **_active_payload(conversation.active),
        }
    )


def _ratio_plan(payload: ConversationPayload) -> RatioPlan | None:
    if payload.last_ratio_kind is None:
        return None
    assert payload.last_ratio_part is not None and payload.last_ratio_whole is not None
    part = query_to_domain(payload.last_ratio_part)
    whole = query_to_domain(payload.last_ratio_whole)
    if not isinstance(part, FinancialPlan) or not isinstance(whole, FinancialPlan):
        raise ValueError("ratio operands must be aggregate plans")
    return RatioPlan(part=part, whole=whole, kind=payload.last_ratio_kind)


def _comparison_plan(payload: ConversationPayload) -> ComparisonPlan | None:
    if payload.last_comparison_earlier is None:
        return None
    assert payload.last_comparison_latest is not None
    earlier = query_to_domain(payload.last_comparison_earlier)
    latest = query_to_domain(payload.last_comparison_latest)
    if not isinstance(earlier, FinancialPlan) or not isinstance(latest, FinancialPlan):
        raise ValueError("comparison operands must be aggregate plans")
    return ComparisonPlan(earlier=earlier, latest=latest)


def _ratio_draft(payload: ConversationPayload) -> RatioDraft | None:
    if payload.pending_ratio_kind is None:
        return None
    assert payload.pending_ratio_part is not None and payload.pending_ratio_whole is not None
    return RatioDraft(
        part=draft_to_domain(payload.pending_ratio_part),
        whole=draft_to_domain(payload.pending_ratio_whole),
        kind=payload.pending_ratio_kind,
    )


def _active_payload(active: ActiveWorkflow | None) -> dict[str, Any]:
    """The fields that belong to the active state, and only those."""
    if isinstance(active, FinancialClarification):
        return {"draft": plan_payload(active.draft)}
    if isinstance(active, UnsupportedCapability):
        return {"capability": active.capability}
    if isinstance(active, PaymentInProgress):
        return {
            "payment": PaymentDraftPayload(
                recipient_name=active.draft.recipient_name,
                recipient_contact_id=active.draft.recipient_contact_id,
                amount_minor=active.draft.amount_minor,
                currency=active.draft.currency,
                concept=active.draft.concept,
                rail=active.draft.rail,
            ),
            "suspended": active.suspended,
            "candidates": [
                SpokenContactResponse(
                    contact_id=contact.contact_id,
                    display_name=contact.display_name,
                    phone=contact.phone,
                )
                for contact in active.candidates
            ],
        }
    if isinstance(active, TransferInProgress):
        return {
            "transfer_to": active.to_account_id,
            "transfer_amount_minor": active.amount_minor,
        }
    if isinstance(active, PaymentConfirmation):
        return {
            "intent_id": active.intent_id,
            "recipient": active.recipient,
            "amount_minor": active.amount_minor,
            # Sent as well as read. Dropping it here was the whole of the round-trip loss: the
            # payload had nowhere to put a currency, so the far side rebuilt the default.
            "currency": active.currency,
            "concept": active.concept,
            "rail": active.rail,
            "recipient_iban": active.recipient_iban,
        }
    return {}
