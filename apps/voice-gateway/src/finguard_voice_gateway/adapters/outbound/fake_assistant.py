from finguard_voice_gateway.ports.assistant_client import AssistantReply, AssistantStageCallback


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
        reply_conversation = {"previous": conversation, "last": text}
        return AssistantReply(
            text=reply_text,
            conversation=reply_conversation,
            answer={
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
            },
            payment_confirmation_required=payment,
        )
