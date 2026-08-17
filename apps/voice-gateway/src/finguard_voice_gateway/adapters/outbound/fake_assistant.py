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
        return AssistantReply(
            text=(
                "Vista previa preparada; confirma el pago con el control explícito en pantalla."
                if normalized in {"quiero preparar un pago", "preparar pago"}
                else "Confirmación por voz no disponible; usa el control explícito en pantalla."
                if payment
                else f"He entendido: {text}"
            ),
            conversation={"previous": conversation, "last": text},
            payment_confirmation_required=payment,
        )
