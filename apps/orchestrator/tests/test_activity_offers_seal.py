"""The offers of a search travel sealed, under a token type of their own (spec §5.3).

The same HMAC codec as the visual contexts, the same body and MAC, with the prefix and the signed
`"t"` set to `o1` and no position: offers are one set per conversation, not a list a context moves
through. A visual token can never open offers and an offers token can never open a chart -- the
prefix routes, and the signed `"t"` is the check (`_open`).
"""

from uuid import UUID

import pytest

from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec

CODEC = MetricTokenCodec({"k1": b"k" * 32}, "k1")
HOLDER = "ES-0001"
CONVERSATION = UUID(int=1)


def semantics(start: str = "2026-10-01T18:30:00+02:00") -> dict[str, object]:
    return {
        "slots": [
            {
                "agent_id": "gym-sur",
                "agent_name": "Gimnasio Sur",
                "activity": "spinning",
                "start": start,
                "end": None,
                "free_places": 6,
                "slot_ref": None,
            }
        ],
        "agent_names": [{"agent_id": "gym-sur", "name": "Gimnasio Sur"}],
        "contexts": [{"agent_id": "gym-sur", "context_id": "ctx-sur"}],
    }


def test_offers_are_sealed_under_their_own_prefix() -> None:
    token = CODEC.seal_offers(HOLDER, CONVERSATION, semantics())

    assert token.startswith("o1.")
    assert CODEC.seal_offers(HOLDER, CONVERSATION, semantics()) == token, "deterministic"


def test_the_seal_opens_only_for_its_holder_its_conversation_and_its_content() -> None:
    token = CODEC.seal_offers(HOLDER, CONVERSATION, semantics())

    assert CODEC.verify_offers(token, HOLDER, CONVERSATION, semantics()) == "ok"
    assert CODEC.verify_offers(token, "ES-0002", CONVERSATION, semantics()) == "holder"
    assert CODEC.verify_offers(token, HOLDER, UUID(int=2), semantics()) == "conversation"
    moved = semantics("2026-10-01T19:30:00+02:00")  # one slot's time changed
    assert CODEC.verify_offers(token, HOLDER, CONVERSATION, moved) == "content"


def test_a_visual_token_never_opens_offers_and_an_offers_token_never_opens_a_chart() -> None:
    visual = CODEC.seal_context(HOLDER, CONVERSATION, 0, semantics())
    offers = CODEC.seal_offers(HOLDER, CONVERSATION, semantics())

    assert CODEC.verify_offers(visual, HOLDER, CONVERSATION, semantics()) == "format"
    assert CODEC.verify_context(offers, HOLDER, CONVERSATION, 0, semantics()) == "format"
    # Relabelled: a genuine signature over a body that says it is the other kind of seal.
    relabelled_visual = "o1." + visual.split(".", 1)[1]
    relabelled_offers = "c2." + offers.split(".", 1)[1]
    assert CODEC.verify_offers(relabelled_visual, HOLDER, CONVERSATION, semantics()) == "format"
    assert CODEC.verify_context(relabelled_offers, HOLDER, CONVERSATION, 0, semantics()) == "format"


@pytest.mark.parametrize("token", [None, "", "o1.", "o1.abc.def", "o1.@@.@@", "m1.x.y"])
def test_anything_that_is_not_an_offers_seal_is_format_and_never_raises(
    token: str | None,
) -> None:
    assert CODEC.verify_offers(token, HOLDER, CONVERSATION, semantics()) == "format"


def test_a_seal_made_under_another_key_does_not_open() -> None:
    other = MetricTokenCodec({"k1": b"x" * 32}, "k1")
    token = other.seal_offers(HOLDER, CONVERSATION, semantics())

    assert CODEC.verify_offers(token, HOLDER, CONVERSATION, semantics()) == "format"
