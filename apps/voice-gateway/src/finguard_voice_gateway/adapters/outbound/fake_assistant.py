import unicodedata

from finguard_voice_gateway.ports.assistant_client import AssistantReply, AssistantStageCallback

# The fake's one charted case: a fixed chart and a fixed decision identity, always the same
# two values, because the fake stays a protocol fixture and never invents evidence.
_CHART_ID = "chart-fake-1"
_CHART_TURN_ID = "11111111-1111-1111-1111-111111111111"


def _fold(text: str) -> str:
    """Strip diacritics so «grafico» and «gráfico» compare equal."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(character for character in decomposed if not unicodedata.combining(character))


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
        if "grafico" in _fold(normalized):
            answer["visualization"] = {
                "kind": "chart",
                "chart_id": _CHART_ID,
                "spec": {
                    "mark": {"type": "bar"},
                    "encoding": {
                        "x": {"field": "dimension", "type": "nominal"},
                        "y": {"field": "value_minor", "type": "quantitative"},
                    },
                },
                "data": [],
            }
            reply_conversation["visual_contexts"] = [
                {"chart_id": _CHART_ID, "turn_id": _CHART_TURN_ID}
            ]
            spoken = (
                "Restaurantes fue la categoría con mayor gasto. En pantalla tienes el "
                "desglose, y puedes preguntarme por cualquiera de las categorías."
            )
            answer["spoken"] = spoken
        return AssistantReply(
            text=reply_text,
            conversation=reply_conversation,
            answer=answer,
            payment_confirmation_required=payment,
            spoken=spoken,
        )
