import unicodedata

from finguard_voice_gateway.ports.assistant_client import AssistantReply, AssistantStageCallback

# The fake's one charted case: a fixed chart and a fixed decision identity, always the same
# two values, because the fake stays a protocol fixture and never invents evidence.
_CHART_ID = "chart-fake-1"
_CHART_TURN_ID = "11111111-1111-1111-1111-111111111111"
# The fake's one restyle case: `visualization` stays empty and the chart rides
# `visualization_derivation` instead, the shape `_visual_only_response` actually returns (I-1).
_RESTYLE_CHART_ID = "chart-fake-2"
_RESTYLE_TURN_ID = "22222222-2222-2222-2222-222222222222"
# G-8: a compound answer's charted part.
_COMPOUND_CHART_ID = "chart-fake-3"
_COMPOUND_TURN_ID = "33333333-3333-3333-3333-333333333333"
# G-8: a chart offered for saving.
_SAVE_CHART_ID = "chart-fake-4"
_SAVE_TURN_ID = "44444444-4444-4444-4444-444444444444"

# G-2/G-3: a bar spec both the charted and the restyled case reuse -- shaped exactly like a real
# `spec`, since `VisualizationDerivation`'s own validator (`llm_dto.py`) rejects a `mark.type`
# outside {bar, line, area, point, arc}, requires `encoding`, and refuses embedded data/values.
_BAR_SPEC: dict[str, object] = {
    "mark": {"type": "bar"},
    "encoding": {
        "x": {"field": "dimension", "type": "nominal"},
        "y": {"field": "value_minor", "type": "quantitative"},
    },
}
_LINE_SPEC: dict[str, object] = {
    "mark": {"type": "line"},
    "encoding": {
        "x": {"field": "dimension", "type": "nominal"},
        "y": {"field": "value_minor", "type": "quantitative"},
    },
}


def _fold(text: str) -> str:
    """Strip diacritics so «grafico» and «gráfico» compare equal."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(character for character in decomposed if not unicodedata.combining(character))


# G-9: the real cap (`domain/constants.py`'s `MAX_VISUAL_CONTEXTS`), mirrored here so the fake's
# own append-and-trim behaves like `Conversation.with_visual_context` at the boundary too.
_MAX_VISUAL_CONTEXTS = 5


def _with_visual_context(existing: object, chart_id: str, turn_id: str) -> list[dict[str, str]]:
    """Mirrors `Conversation.with_visual_context` (G-9): append the new context and keep at most
    `_MAX_VISUAL_CONTEXTS`, removing any earlier entry for the same chart id first so the newest
    occurrence of a chart is always last. Before this, every charted/restyled branch below
    *replaced* the whole list with a single element, so a conversation carrying more than one
    context -- the ordinary case after a second or third chart -- was never exercised by any
    gateway test; `chart_identity`'s "newest last" reading of `visual_contexts[-1]` was only ever
    proven true for lists of length one."""
    contexts = (
        [item for item in existing if isinstance(item, dict)] if isinstance(existing, list) else []
    )
    contexts = [item for item in contexts if item.get("chart_id") != chart_id]
    contexts.append({"chart_id": chart_id, "turn_id": turn_id})
    return contexts[-_MAX_VISUAL_CONTEXTS:]


def _chart_visualization(chart_id: str, *, unit: str = "money") -> dict[str, object]:
    """A `ChartVisualizationResponse`-shaped dict, `unit`/`limit` included (G-3): the real DTO
    always emits both (`unit` defaults to `"money"`, never absent), and `apps/web/src/api.ts`
    rejects any chart whose `unit` is not one of `money|count|ratio`."""
    return {
        "kind": "chart",
        "chart_id": chart_id,
        "unit": unit,
        "spec": _BAR_SPEC,
        "data": [],
        "limit": None,
    }


class FakeAssistantClient:
    def __init__(self) -> None:
        self.call_count = 0
        self.received_credential = False

    async def ask(
        self,
        text: str,
        conversation: object,
        *,
        credential: str,
        idempotency_key: str,
        on_stage: AssistantStageCallback | None = None,
    ) -> AssistantReply:
        self.call_count += 1
        self.received_credential = bool(credential)
        normalized = text.strip().lower()
        folded = _fold(normalized)
        payment = normalized in {
            "quiero preparar un pago",
            "preparar pago",
        }
        reply_text = (
            "Vista previa preparada; confirma el pago con el control explícito en pantalla."
            if normalized in {"quiero preparar un pago", "preparar pago"}
            else "Confirmación por voz no disponible; usa el control explícito en pantalla."
            if payment
            else f"He entendido: {text}"
        )
        reply_conversation: dict[str, object] = {"previous": conversation, "last": text}
        incoming_contexts = (
            conversation.get("visual_contexts") if isinstance(conversation, dict) else None
        )
        if incoming_contexts is not None:
            # Visual contexts persist across turns whether or not this one draws a new
            # chart (`Conversation.with_visual_context`) -- carried forward here, not just
            # for the charted/restyle branches below, so a stale one can be probed (I-2).
            reply_conversation["visual_contexts"] = incoming_contexts
        answer: dict[str, object] = {
            "outcome": "AWAITING_CONFIRMATION" if payment else "ANSWERED",
            "text": reply_text,
            "question": None,
            "intent_id": "fake-intent" if payment else None,
            "plan": None,
            "conversation": reply_conversation,
            "currency": "EUR",
            "total_minor": None,
            "movement_count": 0,
            "groups": [],
            "movements": [],
            "suggestions": [],
        }
        spoken: str | None = None
        if "reestilo" in normalized:
            # G-2: mirrors `VisualizationDerivation` fully -- `source_chart_id`, `chart_id`,
            # `spec` and `limit` are all required by the real DTO, and the real controller's
            # outcome for this path is `VISUAL_UPDATED`, never `ANSWERED` (`_visual_only_
            # response`). `visualization` stays `None`, the shape `_visual_only_response`
            # actually returns (I-1).
            answer["outcome"] = "VISUAL_UPDATED"
            answer["text"] = "He creado una nueva versión del gráfico."
            answer["visualization"] = None
            answer["visualization_derivation"] = {
                "source_chart_id": _CHART_ID,
                "chart_id": _RESTYLE_CHART_ID,
                "spec": _LINE_SPEC,
                "limit": None,
            }
            reply_conversation["visual_contexts"] = _with_visual_context(
                incoming_contexts, _RESTYLE_CHART_ID, _RESTYLE_TURN_ID
            )
        elif "kpi" in folded:
            # G-8: a `KpiVisualizationResponse` -- no `chart_id` at all, so `chart_identity`
            # must stamp neither `chartId` nor `visualTurnId` for this case; that is its own
            # docstring's claim (`app.py`), never exercised by any test before this one.
            answer["visualization"] = {
                "kind": "kpi",
                "value_field": "value_minor",
                "unit": "money",
                "data": [{"value_minor": 12345}],
            }
        elif "explicame" in folded:
            # G-8/G-6: `VISUAL_EXPLAINED`, mirroring `_explanation_response` -- `explanation`
            # is a full `VisualExplanationResponse`, `conversation` is unchanged (no new
            # context), and there is no `visualization`/`visualization_derivation` anywhere on
            # this answer: the real controller never puts one there. `chart_identity` therefore
            # has nothing that names the chart being explained -- a real gap on the
            # orchestrator's side (NEEDS_CONTEXT), not something this gateway can read around.
            answer["outcome"] = "VISUAL_EXPLAINED"
            answer["text"] = "Es un gráfico de barras porque compara categorías."
            answer["explanation"] = {
                "chart_reason": "Es un gráfico de barras porque compara categorías.",
                "ordering_reason": None,
                "scope_summary": "Movimientos de agosto.",
                "provenance": None,
                "text": "Es un gráfico de barras porque compara categorías.",
            }
        elif "sin grafico" in folded:
            # G-8/G-6: `VISUAL_NEEDS_TARGET` -- no chart named anywhere on the answer, matching
            # `_visual_only_response`/`_explanation_response`'s own shape for this outcome.
            answer["outcome"] = "VISUAL_NEEDS_TARGET"
            answer["text"] = "Indica qué gráfico quieres cambiar."
        elif "grafico viejo" in folded:
            # G-8/G-6: `VISUAL_UNTRUSTED`.
            answer["outcome"] = "VISUAL_UNTRUSTED"
            answer["text"] = "No reconozco ese gráfico; pide uno nuevo y podré cambiarlo."
        elif "ya esta asi" in folded:
            # G-8/G-6: `VISUAL_UNCHANGED` -- a restyle that draws the same chart already on
            # screen mints nothing.
            answer["outcome"] = "VISUAL_UNCHANGED"
            answer["text"] = "El gráfico ya está así; lo dejo como está."
        elif "regla rota" in folded:
            # G-8/G-6: `VISUAL_INCOMPATIBLE` -- a refused restyle (`_refusal_response`).
            answer["outcome"] = "VISUAL_INCOMPATIBLE"
            answer["text"] = (
                "No puedo aplicar ese cambio a este gráfico: ese tipo de gráfico no encaja "
                "con estos datos; el anterior se mantiene."
            )
        elif "ambiguo" in folded:
            # G-8/G-6: `VISUAL_AMBIGUOUS` -- two readers (financial vs. visual) both claim the
            # sentence and only the holder can say which; nothing merged, no chart named.
            answer["outcome"] = "VISUAL_AMBIGUOUS"
            answer["text"] = "¿Preguntas por un dato o por cómo se ve el gráfico?"
        elif "compuesta" in folded:
            # G-8: a compound (`parts`) answer. Its own top-level `text` is the real
            # controller's own numbered join (`_compound_read_reply`) -- "1. … \n2. …" -- read
            # aloud verbatim before G-5's fix. The parts are built with no `spoken_for_voice`
            # by the real orchestrator too (a real gap, not a fake-only shortcut), so neither
            # part carries its own `spoken`. The second part draws a chart; its context is the
            # conversation's newest, which is where G-5's identity fallback reads it from.
            part_one_text = "En agosto gastaste 100,00 €."
            part_two_text = "En septiembre gastaste 200,00 €."
            answer["outcome"] = "COMPOUND"
            answer["text"] = f"1. {part_one_text}\n2. {part_two_text}"
            answer["parts"] = [
                {
                    "outcome": "ANSWERED",
                    "text": part_one_text,
                    "question": None,
                    "intent_id": None,
                    "plan": None,
                    "conversation": None,
                    "currency": "EUR",
                    "total_minor": 10000,
                    "movement_count": 1,
                    "groups": [],
                    "movements": [],
                    "visualization": None,
                    "visualization_derivation": None,
                    "suggestions": [],
                    "parts": [],
                },
                {
                    "outcome": "ANSWERED",
                    "text": part_two_text,
                    "question": None,
                    "intent_id": None,
                    "plan": None,
                    "conversation": None,
                    "currency": "EUR",
                    "total_minor": 20000,
                    "movement_count": 1,
                    "groups": [],
                    "movements": [],
                    "visualization": _chart_visualization(_COMPOUND_CHART_ID),
                    "visualization_derivation": None,
                    "suggestions": [],
                    "parts": [],
                },
            ]
            reply_conversation["visual_contexts"] = _with_visual_context(
                incoming_contexts, _COMPOUND_CHART_ID, _COMPOUND_TURN_ID
            )
        elif "guardalo" in folded:
            # G-8: an answer with `save_candidate` -- `SaveCandidate.chart_id` names the same
            # chart this answer just drew, matching the real controller's own invariant.
            answer["visualization"] = _chart_visualization(_SAVE_CHART_ID)
            answer["save_candidate"] = {
                "definition_token": "fake-definition-token",
                "chart_id": _SAVE_CHART_ID,
            }
            reply_conversation["visual_contexts"] = _with_visual_context(
                incoming_contexts, _SAVE_CHART_ID, _SAVE_TURN_ID
            )
        elif "grafico" in folded:
            answer["visualization"] = _chart_visualization(_CHART_ID)
            reply_conversation["visual_contexts"] = _with_visual_context(
                incoming_contexts, _CHART_ID, _CHART_TURN_ID
            )
            spoken = (
                "Restaurantes fue la categoría con mayor gasto. En pantalla tienes el "
                "desglose, y puedes preguntarme por cualquiera de las categorías."
            )
            answer["spoken"] = spoken
        # `answer["text"]` is the branch above's own word, when it set one; keep `reply_text`
        # (what rides the event's own top-level `text`) equal to it, exactly as a real
        # `QuestionResponse` has one `text` a client reads both ways, never two independent
        # copies that could drift apart.
        answer_text = answer["text"]
        assert isinstance(answer_text, str)
        reply_text = answer_text
        return AssistantReply(
            text=reply_text,
            conversation=reply_conversation,
            answer=answer,
            payment_confirmation_required=payment,
            spoken=spoken,
        )
