"""Native visualization node: edit a persisted artifact and commit its verified view."""

from dataclasses import replace
from uuid import NAMESPACE_URL, UUID, uuid5

from finguard_orchestrator.application.graph import codec
from finguard_orchestrator.application.graph.graph_state import Ctx, TurnGraphState
from finguard_orchestrator.application.graph.graph_store import canonical_state, commit_state
from finguard_orchestrator.application.queries.financial_turn import (
    AskFinancialQuestionResult,
    SpokenPlan,
)
from finguard_orchestrator.domain.engine.operations import Visualize
from finguard_orchestrator.domain.engine.state import (
    ActiveVisual,
    ArtifactStatus,
    DialogueState,
    DialogueTurn,
    MovementListFocus,
    PaymentConfirmationPending,
    PaymentDraftPending,
)
from finguard_orchestrator.domain.enums import GroupBy, Metric, PlanningOutcome
from finguard_orchestrator.domain.models.chart import (
    DEFAULT_PRESENTATION,
    ROWS_THAT_FIT,
    Mark,
    PresentationContext,
    VisualDataset,
    VisualRow,
)
from finguard_orchestrator.domain.models.query_answer import Group
from finguard_orchestrator.domain.models.visual_plan import Rejection, draft_of
from finguard_orchestrator.domain.services import answer_writer, category_words
from finguard_orchestrator.domain.services.visual_plan_verifier import verify
from finguard_orchestrator.domain.services.visual_profile import visual_profile_of
from finguard_orchestrator.domain.value_objects.financial_plan import AbsoluteRange


def _view_id(artifact_id: str, version: int) -> str:
    return "chart-" + uuid5(NAMESPACE_URL, f"finguard-view:{artifact_id}:{version}").hex[:16]


async def visualize_and_commit(state: TurnGraphState, runtime: Ctx) -> TurnGraphState:
    """Render the active aggregate through the native graph path; no banking or legacy dispatch."""
    ctx = runtime.context
    if state.get("visual_after_query"):
        if not state.get("pending_state") or not state.get("pending_result"):
            raise RuntimeError("combined query visualization has no checkpointed query result")
        current = codec.loads_state(state["pending_state"])
        base_result = codec.loads_typed(state["pending_result"])
    else:
        current = await canonical_state(state, ctx)
        base_result = None
    artifact = current.active_artifact
    # A same-turn query and chart is committed as one result. If the verified query could not be
    # retained as a chart artifact (for example, a group key exceeded the artifact bound), keep
    # the query's own safe plan trace while returning the explicit visualization limitation below.
    # The trace is assigned again from the artifact when one exists.
    spoken_plan = base_result.plan_context if base_result is not None else None
    visual_version = 1
    view_id: str | None = None
    operations = codec.loads(state["ops"])
    visual_ops = tuple(op for op in operations if isinstance(op, Visualize))
    visual_op = visual_ops[0] if len(visual_ops) == 1 and len(operations) == 1 else None
    if visual_ops and visual_op is None:
        result = AskFinancialQuestionResult(
            outcome=PlanningOutcome.INVALID_DOMAIN.value,
            text="La visualización debe solicitarse como una operación independiente.",
        )
        saved = await commit_state(
            ctx,
            current.remembered(DialogueTurn(ctx.sentence, result.text)),
            result,
            account_id=UUID(state["account_id"]),
            expected_version=state["expected_version"],
        )
        ctx.cache["result"], ctx.cache["saved"] = result, saved
        return {
            "status": (
                "payment_waiting"
                if isinstance(saved.payment, PaymentConfirmationPending)
                else "committed"
            ),
            "payment_workflow_id": (
                saved.payment.workflow_id
                if isinstance(saved.payment, PaymentConfirmationPending)
                else ""
            ),
            "rearm_payment": isinstance(saved.payment, PaymentConfirmationPending),
        }
    if visual_op is None:
        visual_op = Visualize(
            "ACTIVE_CHART" if current.active_visual is not None else "ACTIVE_RESULT",
            state.get("visual_style", "AUTO"),
        )
    if (
        visual_op.target == "ACTIVE_CHART"
        and current.active_visual is None
        and not state.get("visual_after_query")
    ):
        result = AskFinancialQuestionResult(
            outcome=PlanningOutcome.INVALID_DOMAIN.value,
            text="Todavía no hay un gráfico activo que pueda cambiar. Primero crea uno.",
        )
    elif artifact is None and current.active_movement_artifact is not None:
        result, spoken_plan, visual_version, view_id = await _visualize_movement_page(
            current, ctx, visual_op
        )
    elif artifact is None:
        result = AskFinancialQuestionResult(
            outcome=PlanningOutcome.INVALID_DOMAIN.value,
            text=(
                "Ya tengo la lista de movimientos, pero todavía no puedo crear una gráfica "
                "a partir de sus filas. La consulta y su paginación siguen disponibles."
                if isinstance(current.focus, MovementListFocus)
                else "Primero necesito una consulta con resultados para poder crear el gráfico."
            ),
        )
    else:
        plan = artifact.plan
        groups = artifact.groups
        if artifact.comparison is not None and artifact.comparison_groups is not None:
            earlier_plan, latest_plan = artifact.comparison.earlier, artifact.comparison.latest
            earlier_label = answer_writer.plan_in_words(earlier_plan)
            latest_label = answer_writer.plan_in_words(latest_plan)
            dataset_metric = "COMPARISON"
            profile_metric = plan.metric.value
            plan_window = None
            earlier_groups, latest_groups = artifact.comparison_groups
            if earlier_groups or latest_groups:
                grouping = {
                    GroupBy.DAY: "día",
                    GroupBy.WEEK: "semana",
                    GroupBy.MONTH: "mes",
                    GroupBy.CATEGORY: "categoría",
                    GroupBy.MERCHANT: "comercio",
                    GroupBy.COUNTERPARTY: "contraparte",
                    GroupBy.ACCOUNT: "cuenta",
                }.get(plan.group_by, "resultado")

                def comparison_rows(
                    groups: tuple[Group, ...], series: str
                ) -> tuple[VisualRow, ...]:
                    return tuple(
                        VisualRow(
                            category_words.name_of(group.key)
                            if plan.group_by is GroupBy.CATEGORY
                            else group.key,
                            answer_writer.comparison_group_value(group, plan.metric),
                            series,
                        )
                        for group in groups
                    )

                rows = comparison_rows(earlier_groups, earlier_label) + comparison_rows(
                    latest_groups, latest_label
                )
                dataset_title = f"{earlier_label} frente a {latest_label}, por {grouping}"
                profile_group_by = plan.group_by.value
            elif artifact.comparison_values is not None:
                # Scalar comparisons have no group rows: show their two verified answers as two
                # ordinary bars, with the period labels on the axis and no invented series.
                earlier_value, latest_value = artifact.comparison_values
                rows = (
                    VisualRow(earlier_label, earlier_value),
                    VisualRow(latest_label, latest_value),
                )
                dataset_title = f"{earlier_label} frente a {latest_label}"
                profile_group_by = GroupBy.NONE.value
            else:
                rows = ()
                dataset_title = f"{earlier_label} frente a {latest_label}"
                profile_group_by = plan.group_by.value
        else:
            rows = tuple(
                VisualRow(
                    (
                        category_words.name_of(group.key)
                        if plan.group_by is GroupBy.CATEGORY
                        else group.key
                    ),
                    group.movement_count
                    if plan.metric in {Metric.COUNT, Metric.FREQUENCY}
                    else group.total_minor,
                )
                for group in groups
            )
            if not rows:
                rows = (VisualRow("total", artifact.total_minor),)
            dataset_metric = plan.metric.value
            dataset_title = answer_writer.describe(plan)
            profile_group_by = plan.group_by.value
            profile_metric = plan.metric.value
            plan_window = (
                plan.date_range
                if plan.date_range is not None
                else AbsoluteRange(artifact.period_start, artifact.period_end)
                if artifact.period_start is not None and artifact.period_end is not None
                else None
            )
        dataset = VisualDataset(
            rows=rows,
            metric=dataset_metric,
            title=dataset_title,
            window=plan_window,
        )
        profile = visual_profile_of(
            dataset,
            total_minor=None if artifact.comparison is not None else artifact.total_minor,
            group_by=profile_group_by,
            plan_limited=(
                plan.group_limit is not None or artifact.status is ArtifactStatus.PARTIAL
            ),
            data_complete=artifact.status is not ArtifactStatus.PARTIAL,
            plan_metric=profile_metric,
        )
        if ctx.ports.visual_policy is None:
            raise RuntimeError("the graph visual policy is not configured")
        presentation = DEFAULT_PRESENTATION
        if plan.group_by is GroupBy.CATEGORY and plan.group_limit is None:
            presentation = PresentationContext(
                channel=DEFAULT_PRESENTATION.channel,
                viewport=DEFAULT_PRESENTATION.viewport,
                max_visible_rows=min(100, max(ROWS_THAT_FIT, len(rows))),
            )
        before = ctx.ports.count_calls()
        decision = await ctx.ports.visual_policy.decide(ctx.sentence, profile, presentation)
        ctx.cache["visual_model_calls"] = max(0, ctx.ports.count_calls() - before)
        style_note: str | None = None
        if decision is not None and visual_op.style != "AUTO":
            mark: Mark = "line" if visual_op.style == "LINE" else "bar"
            styled = replace(decision.spec, mark=mark, stack=None)
            verified = verify(draft_of(styled), profile)
            if isinstance(verified, Rejection):
                # A requested mark may not override domain rules. Keep the verified chart chosen
                # by the visual policy for a new result. For an edit to the active chart, leave
                # that chart untouched instead of recording an alternative as a successful edit.
                style_note = (
                    "No puedo usar un gr\u00e1fico de l\u00edneas sin una dimensi\u00f3n temporal. "
                    "Te muestro una vista v\u00e1lida de estos resultados."
                    if mark == "line"
                    else "Ese tipo de barras no encaja con estos resultados. "
                    "Te muestro una vista v\u00e1lida en su lugar."
                )
                if visual_op.target == "ACTIVE_CHART":
                    decision = None
            else:
                decision = replace(
                    decision,
                    spec=verified,
                    source="patch",
                    fallback_reason=None,
                    accepted=True,
                    proposed=True,
                    valid=True,
                    proposed_spec=verified,
                )
        previous = current.active_visual
        visual_version = (
            previous.version + 1
            if previous is not None and previous.result_version == artifact.result_version
            else 1
        )
        artifact_ref = artifact.artifact_id or (
            f"{current.conversation_id}:{artifact.result_version}"
        )
        view_id = _view_id(artifact_ref, visual_version) if decision is not None else None
        spoken_plan = SpokenPlan(
            direction=plan.direction.value,
            period=plan.period.period.value if plan.period is not None else None,
            n=plan.period.n if plan.period is not None else None,
            start=plan_window.start.isoformat() if plan_window is not None else None,
            end=plan_window.end.isoformat() if plan_window is not None else None,
            categories=list(plan.categories),
            excluded_categories=list(plan.excluded_categories),
            merchants=list(plan.merchants),
            merchant_terms=list(plan.merchant_terms),
            excluded_merchants=list(plan.excluded_merchants),
            counterparties=list(plan.counterparties),
            excluded_counterparties=list(plan.excluded_counterparties),
            counterparty_types=[item.value for item in plan.counterparty_types],
            include_internal=plan.include_internal,
            internal_only=plan.internal_only,
            only_account_id=(str(plan.only_account_id) if plan.only_account_id else None),
            only_account_ids=[str(item) for item in plan.only_account_ids],
            transaction_ids=[str(item) for item in plan.transaction_ids],
            transaction_types=[item.value for item in plan.transaction_types],
            group_by=plan.group_by.value,
            metric=plan.metric.value,
            group_limit=plan.group_limit,
            result_kind=plan.result_kind.value if plan.result_kind is not None else None,
            query_id=artifact.query_id or None,
            artifact_id=artifact.artifact_id or None,
            view_id=view_id,
        )
        visual_words = (
            dataset.title if artifact.comparison is not None else answer_writer.plan_in_words(plan)
        )
        if visual_op.target == "ACTIVE_CHART":
            visual_text = "He actualizado el gr\u00e1fico con la misma consulta."
        elif artifact.comparison is not None:
            visual_text = (
                f"Aqu\u00ed tienes la comparaci\u00f3n en un gr\u00e1fico: {visual_words}."
            )
        else:
            visual_text = "Aqu\u00ed tienes el gr\u00e1fico de los resultados de la consulta."
        if decision is not None and artifact.status is ArtifactStatus.PARTIAL:
            visual_text = (
                f"{visual_text}\n\nEl gr\u00e1fico muestra una muestra acotada de los grupos "
                "devueltos por la consulta. El total bancario conserva el resultado completo; "
                "la vista puede omitir grupos posteriores al l\u00edmite de visualizaci\u00f3n."
            )
        if style_note is not None:
            visual_text = f"{style_note}\n\n{visual_text}"
        if decision is None:
            result = AskFinancialQuestionResult(
                outcome=(
                    PlanningOutcome.INVALID_DOMAIN.value
                    if style_note is not None
                    else PlanningOutcome.PLANNED.value
                ),
                text=(
                    style_note
                    if style_note is not None
                    else "La consulta no tiene suficientes grupos para dibujar un gráfico útil."
                ),
                plan_in_words=visual_words,
                plan_context=spoken_plan,
                currency=artifact.currency,
                total_minor=artifact.total_minor,
                movement_count=artifact.movement_count,
                explicit_visualization=True,
                visual_dataset=dataset,
                metric_definition=plan,
            )
        else:
            result = AskFinancialQuestionResult(
                outcome=PlanningOutcome.PLANNED.value,
                text=visual_text,
                plan_in_words=visual_words,
                plan_context=spoken_plan,
                currency=artifact.currency,
                total_minor=artifact.total_minor,
                movement_count=artifact.movement_count,
                explicit_visualization=True,
                visual_dataset=dataset,
                visual_decision=decision,
                metric_definition=plan,
            )
    if base_result is not None:
        result = replace(
            result,
            text=f"{base_result.text}\n\n{result.text}",
            plan=base_result.plan,
            plan_in_words=base_result.plan_in_words,
            groups=base_result.groups,
            movements=base_result.movements,
            movement_count=base_result.movement_count,
            total_minor=base_result.total_minor,
            currency=base_result.currency,
            plan_context=spoken_plan or base_result.plan_context,
        )
    canonical = current
    result_version = (
        artifact.result_version
        if artifact is not None
        else current.active_movement_artifact.result_version
        if current.active_movement_artifact is not None
        else None
    )
    if (
        result.visual_decision is not None
        and result.visual_dataset is not None
        and result_version is not None
    ):
        assert view_id is not None
        canonical = replace(
            current,
            active_visual=ActiveVisual(
                result_version,
                visual_version,
                result.visual_decision.spec,
                result.visual_decision.profile,
                view_id,
            ),
        )
    else:
        canonical = replace(
            current,
            active_visual=(current.active_visual if visual_op.target == "ACTIVE_CHART" else None),
        )
    if isinstance(canonical.payment, PaymentDraftPending):
        canonical = replace(
            canonical,
            payment=replace(canonical.payment, suspended=True),
        )
    saved = await commit_state(
        ctx,
        canonical.remembered(DialogueTurn(ctx.sentence, result.plan_in_words or result.text)),
        result,
        account_id=UUID(state["account_id"]),
        expected_version=state["expected_version"],
    )
    ctx.cache["result"], ctx.cache["saved"] = result, saved
    return {
        "status": (
            "payment_waiting"
            if isinstance(saved.payment, PaymentConfirmationPending)
            else "committed"
        ),
        "payment_workflow_id": (
            saved.payment.workflow_id
            if isinstance(saved.payment, PaymentConfirmationPending)
            else ""
        ),
        "model_calls": state.get("model_calls", 0) + ctx.cache.get("visual_model_calls", 0),
        "rearm_payment": isinstance(saved.payment, PaymentConfirmationPending),
    }


async def _visualize_movement_page(
    current: DialogueState,
    ctx: Ctx,
    visual_op: Visualize,
) -> tuple[AskFinancialQuestionResult, SpokenPlan, int, str | None]:
    """Choose a view over the bounded summary of the movement page already read from banking."""
    artifact = current.active_movement_artifact
    focus = current.focus
    if artifact is None or not isinstance(focus, MovementListFocus):
        raise RuntimeError("movement visual artifact is detached from its list focus")
    profile = visual_profile_of(
        artifact.dataset,
        total_minor=sum(row.value_minor for row in artifact.dataset.rows),
        group_by=artifact.group_by.value,
        plan_metric="SUM",
    )
    if ctx.ports.visual_policy is None:
        raise RuntimeError("the graph visual policy is not configured")
    before = ctx.ports.count_calls()
    decision = await ctx.ports.visual_policy.decide(ctx.sentence, profile, DEFAULT_PRESENTATION)
    ctx.cache["visual_model_calls"] = max(0, ctx.ports.count_calls() - before)
    style_note: str | None = None
    if decision is not None and visual_op.style != "AUTO":
        mark: Mark = "line" if visual_op.style == "LINE" else "bar"
        verified = verify(draft_of(replace(decision.spec, mark=mark, stack=None)), profile)
        if isinstance(verified, Rejection):
            style_note = (
                "No puedo usar un grafico de lineas sin una dimension temporal."
                if mark == "line"
                else "Ese tipo de barras no encaja con estos resultados."
            )
            if visual_op.target == "ACTIVE_CHART":
                decision = None
        else:
            decision = replace(
                decision,
                spec=verified,
                source="patch",
                fallback_reason=None,
                accepted=True,
                proposed=True,
                valid=True,
                proposed_spec=verified,
            )
    previous = current.active_visual
    visual_version = (
        previous.version + 1
        if previous is not None and previous.result_version == artifact.result_version
        else 1
    )
    view_id = _view_id(artifact.artifact_id, visual_version) if decision is not None else None
    plan = focus.plan
    spoken_plan = SpokenPlan(
        direction=plan.direction.value,
        period=plan.period.period.value if plan.period is not None else None,
        n=plan.period.n if plan.period is not None else None,
        start=plan.date_from.isoformat() if plan.date_from is not None else None,
        end=plan.date_to.isoformat() if plan.date_to is not None else None,
        categories=list(plan.categories),
        merchant_terms=list(plan.merchant_terms),
        across_accounts=plan.across_accounts,
        group_by=artifact.group_by.value,
        metric="SUM",
        result_kind="RECORDS",
        query_id=artifact.query_id,
        artifact_id=artifact.artifact_id,
        view_id=view_id,
    )
    title = artifact.dataset.title
    if decision is None:
        text = style_note or "Esta página no tiene suficientes grupos para dibujar un gráfico útil."
        outcome = (
            PlanningOutcome.INVALID_DOMAIN.value
            if style_note is not None
            else PlanningOutcome.PLANNED.value
        )
    else:
        text = "Aquí tienes un gráfico de los movimientos de esta página."
        outcome = PlanningOutcome.PLANNED.value
    result = AskFinancialQuestionResult(
        outcome=outcome,
        text=text,
        plan=title,
        plan_in_words=title,
        plan_context=spoken_plan,
        currency=artifact.currency,
        movement_count=artifact.movement_count,
        explicit_visualization=True,
        visual_dataset=artifact.dataset,
        visual_decision=decision,
    )
    return result, spoken_plan, visual_version, view_id
