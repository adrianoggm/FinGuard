"""Payment and own-transfer workflow nodes for the conversation graph.

All money movement remains behind the existing guarded command handlers. This module owns the
workflow-specific prepare, interrupt, resume and canonical-state updates; graph wiring owns only
where these nodes run.
"""

from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

from langgraph.types import interrupt

from finguard_orchestrator.application.commands import payment_input
from finguard_orchestrator.application.commands.pay import (
    ConfirmPaymentCommand,
    PaymentAnswer,
    PrepareChosenPaymentCommand,
    PrepareOwnTransferCommand,
)
from finguard_orchestrator.application.graph import codec
from finguard_orchestrator.application.graph.graph_grounding import grounds_for_state
from finguard_orchestrator.application.graph.graph_state import Ctx, GraphContext, TurnGraphState
from finguard_orchestrator.application.graph.graph_store import commit_state
from finguard_orchestrator.application.queries.financial_turn import AskFinancialQuestionResult
from finguard_orchestrator.domain.engine.grounding import ground
from finguard_orchestrator.domain.engine.operations import (
    PlanFields,
    PrepareBizum,
    PrepareOwnTransfer,
    PrepareTransfer,
)
from finguard_orchestrator.domain.engine.state import (
    DialogueState,
    DialogueTurn,
    OwnTransferDraftPending,
    PaymentConfirmationPending,
    PaymentDraftPending,
)
from finguard_orchestrator.domain.engine.transitions import (
    SuspendedTaskLimitError,
    suspend_pending_task,
)
from finguard_orchestrator.domain.enums import PaymentOutcome, PlanningOutcome, Rail
from finguard_orchestrator.domain.errors import NotAuthorisedError
from finguard_orchestrator.domain.models.conversation import Conversation, PaymentConfirmation
from finguard_orchestrator.domain.ports.dialogue_store import StaleDialogueError
from finguard_orchestrator.domain.services import answer_writer, payment_writer
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft
from finguard_orchestrator.domain.value_objects.scope import Scope


async def prepare_payment(state: TurnGraphState, runtime: Ctx) -> TurnGraphState:
    """Ground a typed contact payment against the sentence and holder contacts before asking the
    existing guarded command handler for a bank-authored preview. No command here can confirm."""
    ctx = runtime.context
    account_id = UUID(state["account_id"])
    conversation_id = UUID(state["conversation_id"])
    current = (
        codec.loads_state(state["pending_state"])
        if state.get("pending_state")
        else await ctx.ports.store.load(conversation_id, account_id=account_id)
        or DialogueState(conversation_id)
    )
    base_result = (
        codec.loads_typed(state["pending_result"]) if state.get("pending_result") else None
    )
    if current is None or current.version != state["expected_version"]:
        raise StaleDialogueError(current or DialogueState(conversation_id))
    # A payment request is a new conversational task. Preserve an unrelated open banking
    # clarification before this workflow writes its draft or preview into canonical state.
    try:
        current = suspend_pending_task(current)
    except SuspendedTaskLimitError:
        return await _finish_payment_without_command(
            state,
            ctx,
            "Tienes varias consultas pendientes. Retoma o completa una antes de preparar un pago.",
            current,
            base_result,
        )
    if base_result is not None and not isinstance(base_result, AskFinancialQuestionResult):
        raise RuntimeError("pending graph result is not an answer")
    if ctx.ports.commands is None:
        return await _finish_payment_without_command(
            state, ctx, payment_writer.cannot_pay(), current, base_result
        )
    if "composition" in state.get("refused", []):
        return await _finish_payment_without_command(
            state,
            ctx,
            "No puedo combinar ese Bizum con las otras operaciones de este turno.",
            current,
            base_result,
        )
    operations = codec.loads(state["ops"])
    own_transfer = next((item for item in operations if isinstance(item, PrepareOwnTransfer)), None)
    if own_transfer is not None:
        if isinstance(current.payment, PaymentDraftPending):
            return await _finish_own_transfer_refusal(
                state,
                ctx,
                current,
                base_result,
                "Termina o cancela el pago pendiente antes de iniciar otra operación de pago.",
            )
        return await _prepare_own_transfer(state, ctx, current, base_result, own_transfer)
    op = next(
        (item for item in operations if isinstance(item, (PrepareBizum, PrepareTransfer))), None
    )
    if op is None:
        raise RuntimeError("payment node reached without a contact payment operation")
    if current.own_transfer is not None:
        return await _finish_payment_without_command(
            state,
            ctx,
            "Termina o cancela la transferencia pendiente antes de iniciar otro pago.",
            current,
            base_result,
        )
    contacts = await ctx.ports.contacts.all_of(account_id)
    replacement = (
        current.payment if isinstance(current.payment, PaymentConfirmationPending) else None
    )
    if replacement is not None and replacement.preview.rail is Rail.INTERNAL:
        return await _finish_payment_without_command(
            state,
            ctx,
            "La vista previa de la transferencia entre tus cuentas sigue pendiente. "
            "Confírmala o cancélala antes de preparar otro pago.",
            current,
            base_result,
        )
    previous_payment = current.payment if isinstance(current.payment, PaymentDraftPending) else None
    if replacement is not None:
        prior_draft = replacement.draft or PaymentDraft(
            recipient_name=replacement.preview.recipient,
            recipient_iban=replacement.preview.recipient_iban,
            amount_minor=replacement.preview.amount_minor,
            currency=replacement.preview.currency,
            rail=replacement.preview.rail,
        )
        previous_payment = PaymentDraftPending(prior_draft)
    resolved = payment_input.resolve_contact_payment_draft(
        ctx.sentence,
        op,
        contacts,
        previous=previous_payment.draft if previous_payment is not None else None,
        candidate_contact_ids=(
            previous_payment.candidate_contact_ids if previous_payment is not None else ()
        ),
    )
    if resolved.rejected:
        return await _finish_payment_without_command(
            state,
            ctx,
            "No he podido verificar el importe, el destinatario y el tipo de pago "
            "en tu mensaje "
            "y tus contactos. Indica el importe, un contacto reconocible y si quieres hacer un "
            "Bizum o una transferencia.",
            current,
            base_result,
        )
    if (
        replacement is not None
        and previous_payment is not None
        and resolved.draft == previous_payment.draft
    ):
        return await _finish_payment_correction_without_changes(
            state, ctx, current, replacement, base_result
        )
    if replacement is not None:
        if Scope.PAYMENTS_CONFIRM.value not in ctx.caller_scopes:
            raise NotAuthorisedError(
                f"this credential is not allowed {Scope.PAYMENTS_CONFIRM.value}"
            )
        # A correction is a new proposal, never consent to the preview already shown. Cancel that
        # exact bank intent first; if the bank cannot confirm cancellation, keep the old preview and
        # do not create a second live intent.
        cancellation = await ctx.ports.commands.dispatch(
            ConfirmPaymentCommand(
                reply="no",
                account_id=account_id,
                intent_id=replacement.preview.intent_id,
                preview=replacement.preview,
            )
        )
        if not isinstance(cancellation, PaymentAnswer):
            raise TypeError("payment cancellation returned an unexpected answer")
        if cancellation.outcome != PaymentOutcome.CANCELLED.value:
            result = _combine_payment_result(
                base_result,
                AskFinancialQuestionResult(
                    outcome=cancellation.outcome,
                    text=cancellation.text,
                ),
            )
            saved = await commit_state(
                ctx,
                _remember_result(current, ctx.sentence, result.text),
                result,
                account_id=account_id,
                expected_version=state["expected_version"],
            )
            ctx.cache["dialogue"], ctx.cache["result"], ctx.cache["saved"] = (
                saved,
                result,
                saved,
            )
            return {
                "status": "payment_waiting",
                "payment_workflow_id": (
                    saved.payment.workflow_id
                    if isinstance(saved.payment, PaymentConfirmationPending)
                    else ""
                ),
                "expected_version": saved.version,
                "rearm_payment": isinstance(saved.payment, PaymentConfirmationPending),
            }
        current = replace(current, payment=None)
    if resolved.candidate_contact_ids or resolved.draft.missing:
        if resolved.candidate_contact_ids:
            names = tuple(
                contact.display_name
                for contact in contacts
                if contact.contact_id in resolved.candidate_contact_ids
            )
            question = payment_writer.several_candidates(names)
        else:
            question = payment_writer.still_missing(resolved.draft)
        result = AskFinancialQuestionResult(
            outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
            text=question,
        )
        result = _combine_payment_result(base_result, result)
        candidate = _remember_result(
            replace(
                current,
                payment=PaymentDraftPending(resolved.draft, resolved.candidate_contact_ids),
            ),
            ctx.sentence,
            result.text,
        )
        saved = await commit_state(
            ctx,
            candidate,
            result,
            account_id=account_id,
            expected_version=state["expected_version"],
        )
        ctx.cache["dialogue"], ctx.cache["result"], ctx.cache["saved"] = (
            saved,
            result,
            saved,
        )
        return {"status": "payment_complete", "expected_version": saved.version}
    recipient = next(
        (
            contact
            for contact in contacts
            if contact.contact_id == resolved.draft.recipient_contact_id
        ),
        None,
    )
    recipient_name = (
        resolved.draft.recipient_name
        if resolved.draft.recipient_iban is not None
        else recipient.display_name
        if recipient is not None
        else None
    )
    if recipient_name is None or resolved.draft.amount_minor is None:
        raise RuntimeError("complete payment draft lacks a grounded recipient or amount")
    recipient_contact_id = (
        recipient.contact_id
        if resolved.draft.recipient_iban is None and recipient is not None
        else None
    )
    command = PrepareChosenPaymentCommand(
        account_id=account_id,
        recipient_name=recipient_name,
        recipient_contact_id=recipient_contact_id,
        recipient_iban=resolved.draft.recipient_iban,
        concept=resolved.draft.concept,
        amount_minor=resolved.draft.amount_minor,
        currency=resolved.draft.currency,
        idempotency_key=f"conversation-payment:{state['turn_id']}",
        rail=resolved.draft.rail,
    )
    answer = await ctx.ports.commands.dispatch(command)
    if not isinstance(answer, PaymentAnswer):
        raise TypeError("payment preparation returned an unexpected answer")
    if (
        answer.reason == "contact_has_no_account"
        and resolved.draft.rail is Rail.TRANSFER
        and recipient is not None
    ):
        answer = replace(
            answer,
            text=payment_writer.saved_contact_transfer_unavailable(recipient.display_name),
        )
    pending = (
        PaymentConfirmationPending(answer.preview, uuid4().hex, resolved.draft)
        if answer.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
        and answer.preview is not None
        else None
    )
    candidate_payment: PaymentDraftPending | PaymentConfirmationPending | None = pending
    if (
        replacement is not None
        and pending is None
        and answer.outcome
        in {
            PaymentOutcome.BANKING_UNAVAILABLE.value,
            PaymentOutcome.NEEDS_CLARIFICATION.value,
        }
    ):
        candidate_payment = PaymentDraftPending(resolved.draft)
    payment_result = _payment_result(answer, replace(current, payment=pending), pending is not None)
    result = _combine_payment_result(base_result, payment_result)
    candidate = replace(current, payment=candidate_payment)
    if candidate.turns and candidate.turns[-1].said == ctx.sentence:
        last = candidate.turns[-1]
        candidate = replace(
            candidate, turns=(*candidate.turns[:-1], replace(last, answered=result.text))
        )
    else:
        candidate = candidate.remembered(DialogueTurn(ctx.sentence, result.text))
    result = _combine_payment_result(
        base_result,
        _payment_result(
            answer,
            replace(candidate, version=state["expected_version"] + 1),
            pending is not None,
        ),
    )
    saved = await commit_state(
        ctx,
        candidate,
        result,
        account_id=account_id,
        expected_version=state["expected_version"],
    )
    ctx.cache["dialogue"] = saved
    ctx.cache["result"] = result
    ctx.cache["saved"] = saved
    if pending is not None:
        return {
            "status": "payment_waiting",
            "payment_answer": codec.dumps_typed(answer),
            "payment_workflow_id": pending.workflow_id,
            "expected_version": saved.version,
        }
    return {"status": "payment_complete", "expected_version": saved.version}


async def _prepare_own_transfer(
    state: TurnGraphState,
    ctx: GraphContext,
    current: DialogueState,
    previous: AskFinancialQuestionResult | None,
    operation: PrepareOwnTransfer,
) -> TurnGraphState:
    """Resolve the transfer only from the caller's words and bank account catalogue.

    The model selects the typed transfer operation. Account and amount are independently grounded;
    only the existing command can ask banking for a preview, and this node never confirms it.
    """
    grounds = await grounds_for_state(state, ctx)
    accounts = grounds.accounts
    prior = current.own_transfer
    destination_candidates = payment_input.destination_candidates(ctx.sentence, accounts, prior)
    destination: Any | None = None
    if operation.destination_account is not None:
        grounded = ground(PlanFields(accounts=(operation.destination_account,)), grounds)
        if isinstance(grounded, PlanFields) and grounded.accounts:
            destination = next(
                (holding for holding in accounts if holding.display_name == grounded.accounts[0]),
                None,
            )
    if destination_candidates:
        if len(destination_candidates) == 1:
            lexical = destination_candidates[0]
            if destination is not None and destination.account_id != lexical.account_id:
                return await _finish_own_transfer_refusal(
                    state,
                    ctx,
                    current,
                    previous,
                    "No he podido verificar la cuenta de destino que nombraste.",
                )
            destination = lexical
        else:
            destination = None
    elif destination is not None and prior is not None:
        if prior.to_account_id != destination.account_id:
            return await _finish_own_transfer_refusal(
                state,
                ctx,
                current,
                previous,
                "No he podido verificar la cuenta de destino que nombraste.",
            )
    elif destination is not None:
        return await _finish_own_transfer_refusal(
            state,
            ctx,
            current,
            previous,
            "No he podido verificar la cuenta de destino que nombraste.",
        )

    if destination is None and prior is not None and prior.to_account_id is not None:
        destination = next(
            (holding for holding in accounts if holding.account_id == prior.to_account_id), None
        )

    amount_spoken = payment_input.amount(ctx.sentence)
    if payment_input.mentions_amount(ctx.sentence) and amount_spoken is None:
        return await _finish_own_transfer_refusal(
            state,
            ctx,
            current,
            previous,
            "El importe indicado queda fuera de los límites permitidos. Indica otro importe.",
            retain=True,
        )
    amount = amount_spoken if amount_spoken is not None else prior.amount_minor if prior else None

    candidate_ids = tuple(holding.account_id for holding in destination_candidates)
    if destination is None and prior is not None and prior.candidate_account_ids:
        candidate_ids = prior.candidate_account_ids
    if destination is None or amount is None:
        draft = OwnTransferDraftPending(
            to_account_id=destination.account_id if destination is not None else None,
            amount_minor=amount,
            candidate_account_ids=candidate_ids if destination is None else (),
            workflow_id=prior.workflow_id if prior is not None else uuid4().hex,
        )
        question = (
            answer_writer.which_account(
                [
                    holding
                    for holding in accounts
                    if not candidate_ids or holding.account_id in candidate_ids
                ]
            )
            if destination is None
            else answer_writer.how_much_to_move(destination)
        )
        result = AskFinancialQuestionResult(
            outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
            text=question,
        )
        result = _combine_payment_result(previous, result)
        saved = await _save_own_transfer_state(current, draft, result, ctx, state)
        ctx.cache["result"], ctx.cache["saved"] = result, saved
        return {"status": "payment_complete", "expected_version": saved.version}

    workflow_id = prior.workflow_id if prior is not None else uuid4().hex
    command = PrepareOwnTransferCommand(
        account_id=UUID(state["account_id"]),
        to_account_id=destination.account_id,
        amount_minor=amount,
        currency=destination.currency,
        idempotency_key=f"conversation-own-transfer:{workflow_id}",
    )
    if ctx.ports.commands is None:
        return await _finish_own_transfer_refusal(
            state, ctx, current, previous, payment_writer.cannot_pay()
        )
    answer = await ctx.ports.commands.dispatch(command)
    if not isinstance(answer, PaymentAnswer):
        raise TypeError("own transfer preparation returned an unexpected answer")
    waiting = (
        answer.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value and answer.preview is not None
    )
    uncertain = answer.outcome == PaymentOutcome.BANKING_UNAVAILABLE.value
    pending = None
    if waiting:
        if answer.preview is None:
            raise RuntimeError("a transfer awaiting confirmation has no bank preview")
        pending = PaymentConfirmationPending(answer.preview, uuid4().hex)
    retained = (
        OwnTransferDraftPending(destination.account_id, amount, workflow_id=workflow_id)
        if uncertain
        else None
    )
    payment = _payment_result(answer, replace(current, payment=pending), waiting)
    result = _combine_payment_result(previous, payment)
    candidate = _remember_result(
        replace(current, own_transfer=retained, payment=pending), ctx.sentence, result.text
    )
    result = _combine_payment_result(
        previous,
        _payment_result(
            answer,
            replace(candidate, version=state["expected_version"] + 1),
            waiting,
        ),
    )
    saved = await commit_state(
        ctx,
        candidate,
        result,
        account_id=UUID(state["account_id"]),
        expected_version=state["expected_version"],
    )
    ctx.cache["dialogue"] = saved
    ctx.cache["result"] = result
    ctx.cache["saved"] = saved
    if waiting:
        return {
            "status": "payment_waiting",
            "payment_answer": codec.dumps_typed(answer),
            "payment_workflow_id": pending.workflow_id if pending is not None else "",
            "expected_version": saved.version,
        }
    return {"status": "payment_complete", "expected_version": saved.version}


async def _finish_own_transfer_refusal(
    graph_state: TurnGraphState,
    ctx: GraphContext,
    current: DialogueState,
    previous: AskFinancialQuestionResult | None,
    text: str,
    *,
    retain: bool = False,
) -> TurnGraphState:
    prior = current.own_transfer
    result = _combine_payment_result(
        previous,
        AskFinancialQuestionResult(outcome=PlanningOutcome.INVALID_DOMAIN.value, text=text),
    )
    candidate = _remember_result(
        replace(current, own_transfer=prior if retain else None), ctx.sentence, result.text
    )
    saved = await commit_state(
        ctx,
        candidate,
        result,
        account_id=UUID(graph_state["account_id"]),
        expected_version=graph_state["expected_version"],
    )
    ctx.cache["dialogue"], ctx.cache["result"], ctx.cache["saved"] = saved, result, saved
    return {"status": "payment_complete", "expected_version": saved.version}


async def _save_own_transfer_state(
    current: DialogueState,
    draft: OwnTransferDraftPending,
    result: AskFinancialQuestionResult,
    ctx: GraphContext,
    state: TurnGraphState,
) -> DialogueState:
    candidate = _remember_result(replace(current, own_transfer=draft), ctx.sentence, result.text)
    saved = await commit_state(
        ctx,
        candidate,
        result,
        account_id=UUID(state["account_id"]),
        expected_version=state["expected_version"],
    )
    ctx.cache["dialogue"] = saved
    return saved


def _remember_result(state: DialogueState, sentence: str, answer: str) -> DialogueState:
    if state.turns and state.turns[-1].said == sentence:
        last = state.turns[-1]
        return replace(state, turns=(*state.turns[:-1], replace(last, answered=answer)))
    return state.remembered(DialogueTurn(sentence, answer))


async def _finish_payment_without_command(
    state: TurnGraphState,
    ctx: GraphContext,
    text: str,
    current: DialogueState | None = None,
    base_result: AskFinancialQuestionResult | None = None,
) -> TurnGraphState:
    account_id = UUID(state["account_id"])
    current = current or await ctx.ports.store.load(
        UUID(state["conversation_id"]), account_id=account_id
    )
    if current is None or current.version != state["expected_version"]:
        raise StaleDialogueError(current or DialogueState(UUID(state["conversation_id"])))
    payment_result = AskFinancialQuestionResult(
        outcome=PlanningOutcome.INVALID_DOMAIN.value,
        text=text,
    )
    result = _combine_payment_result(base_result, payment_result)
    candidate = current
    if candidate.turns and candidate.turns[-1].said == ctx.sentence:
        last = candidate.turns[-1]
        candidate = replace(
            candidate, turns=(*candidate.turns[:-1], replace(last, answered=result.text))
        )
    else:
        candidate = candidate.remembered(DialogueTurn(ctx.sentence, result.text))
    saved = await commit_state(
        ctx,
        candidate,
        result,
        account_id=account_id,
        expected_version=state["expected_version"],
    )
    ctx.cache["result"], ctx.cache["saved"] = result, saved
    return {
        "status": (
            "payment_waiting"
            if isinstance(saved.payment, PaymentConfirmationPending)
            else "payment_complete"
        ),
        "payment_workflow_id": (
            saved.payment.workflow_id
            if isinstance(saved.payment, PaymentConfirmationPending)
            else ""
        ),
        "expected_version": saved.version,
        "rearm_payment": isinstance(saved.payment, PaymentConfirmationPending),
    }


async def _finish_payment_correction_without_changes(
    state: TurnGraphState,
    ctx: GraphContext,
    current: DialogueState,
    pending: PaymentConfirmationPending,
    base_result: AskFinancialQuestionResult | None,
) -> TurnGraphState:
    """Keep the exact preview live when a requested correction did not name a new field."""
    text = (
        f"{payment_writer.unclear(pending.preview)} "
        "Si quieres cambiar el pago, dime el nuevo importe o destinatario. "
        "La vista previa anterior sigue pendiente; no he enviado nada."
    )
    old_answer = codec.loads_typed(state["payment_answer"]) if state.get("payment_answer") else None
    if isinstance(old_answer, PaymentAnswer) and old_answer.preview == pending.preview:
        answer = replace(
            old_answer,
            outcome=PaymentOutcome.AWAITING_CONFIRMATION.value,
            text=text,
        )
    else:
        preview = pending.preview
        answer = PaymentAnswer(
            outcome=PaymentOutcome.AWAITING_CONFIRMATION.value,
            text=text,
            intent_id=preview.intent_id,
            recipient=preview.recipient,
            recipient_phone=preview.recipient_phone,
            recipient_iban=preview.recipient_iban,
            rail=preview.rail.value,
            amount_minor=preview.amount_minor,
            currency=preview.currency,
            balance_after_minor=preview.balance_after_minor,
            expires_at=preview.expires_at.isoformat(),
            preview=preview,
            workflow_id=pending.workflow_id,
        )
    candidate = _remember_result(current, ctx.sentence, text)
    result = _combine_payment_result(
        base_result,
        _payment_result(answer, replace(candidate, version=state["expected_version"] + 1), True),
    )
    saved = await commit_state(
        ctx,
        candidate,
        result,
        account_id=UUID(state["account_id"]),
        expected_version=state["expected_version"],
    )
    ctx.cache["dialogue"], ctx.cache["result"], ctx.cache["saved"] = saved, result, saved
    return {
        "status": "payment_waiting",
        "payment_answer": codec.dumps_typed(answer),
        "payment_workflow_id": pending.workflow_id,
        "expected_version": saved.version,
    }


async def wait_for_payment_reply(state: TurnGraphState) -> TurnGraphState:
    answer = codec.loads_typed(state["payment_answer"])
    if not isinstance(answer, PaymentAnswer) or answer.preview is None:
        raise RuntimeError("payment confirmation checkpoint has no bank preview")
    preview = answer.preview
    reply = interrupt(
        {
            "intent_id": str(preview.intent_id),
            "recipient": preview.recipient,
            "amount_minor": preview.amount_minor,
            "currency": preview.currency,
            "balance_after_minor": preview.balance_after_minor,
            "expires_at": preview.expires_at.isoformat(),
        }
    )
    if not isinstance(reply, str):
        raise ValueError("payment confirmation reply must be text")
    if reply == "respuesta ambigua":
        # The checkpoint contains only the classifier's marker. The user's original words live in
        # this invocation's runtime context and are interpreted by the normal typed planner.
        return {"status": "payment_interpretation"}
    return {"payment_reply": reply, "status": "payment_waiting"}


def _after_payment_reply(state: TurnGraphState) -> str:
    return "plan" if state.get("status") == "payment_interpretation" else "confirm_payment"


async def confirm_payment(state: TurnGraphState, runtime: Ctx) -> TurnGraphState:
    ctx = runtime.context
    if Scope.PAYMENTS_CONFIRM.value not in ctx.caller_scopes:
        raise NotAuthorisedError(f"this credential is not allowed {Scope.PAYMENTS_CONFIRM.value}")
    if ctx.ports.commands is None:
        raise RuntimeError("payment command bus is unavailable")
    account_id = UUID(state["account_id"])
    conversation_id = UUID(state["conversation_id"])
    current = await ctx.ports.store.load(conversation_id, account_id=account_id)
    pending = current.payment if current is not None else None
    if (
        current is None
        or not isinstance(pending, PaymentConfirmationPending)
        or not state.get("payment_workflow_id")
        or pending.workflow_id != state["payment_workflow_id"]
    ):
        raise StaleDialogueError(current or DialogueState(conversation_id))
    command = ConfirmPaymentCommand(
        reply=state["payment_reply"],
        account_id=account_id,
        intent_id=pending.preview.intent_id,
        preview=pending.preview,
    )
    answer = await ctx.ports.commands.dispatch(command)
    if not isinstance(answer, PaymentAnswer):
        raise TypeError("payment confirmation returned an unexpected answer")
    answer = replace(answer, preview=pending.preview)
    remains_pending = answer.outcome in {
        PaymentOutcome.AWAITING_CONFIRMATION.value,
        PaymentOutcome.BANKING_UNAVAILABLE.value,
    }
    candidate = replace(current, payment=pending if remains_pending else None).remembered(
        DialogueTurn(ctx.sentence, answer.text)
    )
    result = _payment_result(
        answer,
        replace(candidate, version=state["expected_version"] + 1),
        remains_pending,
    )
    saved = await commit_state(
        ctx,
        candidate,
        result,
        account_id=account_id,
        expected_version=state["expected_version"],
    )
    ctx.cache["dialogue"] = saved
    ctx.cache["result"] = result
    ctx.cache["saved"] = saved
    return {
        "status": "payment_waiting" if remains_pending else "payment_complete",
        "payment_answer": codec.dumps_typed(answer),
        "payment_workflow_id": pending.workflow_id if remains_pending else "",
        "expected_version": saved.version,
    }


def _payment_result(
    answer: PaymentAnswer, state: DialogueState, waiting: bool
) -> AskFinancialQuestionResult:
    pending = state.payment
    preview = (
        answer.preview
        if answer.preview is not None
        else pending.preview
        if isinstance(pending, PaymentConfirmationPending)
        else None
    )
    conversation = Conversation(
        conversation_id=state.conversation_id,
        version=state.version,
        active=(
            PaymentConfirmation(
                intent_id=preview.intent_id,
                recipient=preview.recipient,
                amount_minor=preview.amount_minor,
                currency=preview.currency,
                rail=preview.rail,
                recipient_iban=preview.recipient_iban,
            )
            if waiting and preview is not None
            else None
        ),
    )
    return AskFinancialQuestionResult(
        outcome=answer.outcome,
        text=answer.text,
        conversation=conversation,
    )


def _combine_payment_result(
    previous: AskFinancialQuestionResult | None, payment: AskFinancialQuestionResult
) -> AskFinancialQuestionResult:
    if previous is None:
        return payment
    return replace(
        previous,
        text=f"{previous.text}\n\n{payment.text}",
        conversation=payment.conversation,
    )
