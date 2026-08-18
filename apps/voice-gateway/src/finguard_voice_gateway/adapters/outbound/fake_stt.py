from collections.abc import Callable

from finguard_voice_gateway.ports.speech_to_text import SpeechToTextUnavailableError


class FakeSpeechToText:
    """Deterministic transcription for the protocol suite. It does not pretend to hear.

    The benchmark and the protocol tests send their sentence as UTF-8 bytes and want it back
    verbatim, which is what keeps them independent of any model. Real microphone audio is not text,
    and there are only two honest things to do with it here: crash, or say there is no model.

    There was briefly a third — return a configured sentence — and it was the wrong one. Somebody
    said "hazle un bizum a Aitor de 23 euros" and the assistant answered how much they had spent
    this month, because the fake had substituted a different intention for theirs. This service
    refuses to let a recogniser turn noise into a confirmation; inventing a whole instruction is the
    same mistake with a bigger blast radius. Speaking requires a real model, by design.
    """

    async def transcribe(self, audio: bytes, *, cancelled: Callable[[], bool]) -> str:
        if cancelled() or not audio:
            return ""
        try:
            return audio.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SpeechToTextUnavailableError(
                "no speech model is installed: build with INSTALL_LOCAL_STT=1 and set "
                "VOICE_GATEWAY_STT_MODE=local to transcribe real audio"
            ) from exc
