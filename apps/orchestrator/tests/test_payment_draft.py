"""A payment being built over several turns.

The conversation this exists for, from the report:

    "Quiero hacerle bizum a marta"   → "¿De cuánto quieres hacerle el bizum a Marta?"
    "15 euros"                       → "¿De qué periodo te refieres?"

The first turn read the recipient correctly and asked for the amount. Then the whole thing was
thrown away: `PaymentReading` can hold a complete request, a question or a reason, and nothing in
between — so "15 euros" arrived as a fresh sentence with no history, and a sentence with money and
no payment verb is not a payment, so it went to the financial planner.

A draft is the state between "somebody wants to pay" and "this is the payment". It is not a
`PaymentRequest` because a request is an amount and a recipient, and the invariants that refuse a
payment of nothing are right — the draft is the honest way to hold a payment that is not one yet.
"""

import pytest

from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft, PaymentField

MARTA = PaymentDraft(recipient_name="Marta")


def test_a_draft_knows_what_it_is_missing() -> None:
    assert MARTA.missing == frozenset({PaymentField.AMOUNT})
    assert PaymentDraft().missing == frozenset({PaymentField.RECIPIENT, PaymentField.AMOUNT})
    assert PaymentDraft(recipient_name="Marta", amount_minor=1_500).missing == frozenset()


def test_a_complete_draft_becomes_a_request() -> None:
    request = PaymentDraft(recipient_name="Marta Solis Vega", amount_minor=1_500).into_request()

    assert request is not None
    assert request.recipient_name == "Marta Solis Vega"
    assert request.amount_minor == 1_500
    assert request.currency == "EUR"


def test_an_incomplete_draft_becomes_nothing() -> None:
    """None rather than a request with a zero in it. `PaymentRequest` refuses a payment of nothing
    and that invariant is the reason this type exists."""
    assert MARTA.into_request() is None
    assert PaymentDraft().into_request() is None


def test_a_turn_fills_the_gaps_and_never_overwrites_silently() -> None:
    """What the merge is for. "15 euros" carries an amount and no recipient; the recipient comes
    from the draft, and the amount from the sentence."""
    filled = MARTA.filled_with(PaymentDraft(amount_minor=1_500))

    assert filled == PaymentDraft(recipient_name="Marta", amount_minor=1_500)


def test_a_turn_that_names_a_recipient_replaces_the_one_being_asked_about() -> None:
    """ "Marta Solis Vega" after "¿a quién?" is a choice, not a second recipient. A merge that kept
    the first would answer the ambiguity question with the ambiguous name again."""
    filled = MARTA.filled_with(PaymentDraft(recipient_name="Marta Solis Vega"))

    assert filled.recipient_name == "Marta Solis Vega"


def test_a_turn_that_changes_its_mind_about_the_amount_wins() -> None:
    """ "Mejor 20" after a preview of 15 is a correction. The new value is the user's latest word on
    it, which is the only reading that lets somebody correct themselves."""
    settled = PaymentDraft(recipient_name="Marta", amount_minor=1_500)

    assert settled.filled_with(PaymentDraft(amount_minor=2_000)).amount_minor == 2_000


def test_an_empty_turn_changes_nothing() -> None:
    assert MARTA.filled_with(PaymentDraft()) == MARTA


def test_the_concept_travels_with_it() -> None:
    """ "Con el concepto tapas" was read and dropped: `PaymentRequest` has no concept, so the words
    the user wrote never reached the payment. Holding it here is the first half of that."""
    with_concept = MARTA.filled_with(PaymentDraft(concept="tapas"))

    assert with_concept.concept == "tapas"
    assert with_concept.missing == frozenset({PaymentField.AMOUNT}), (
        "a concept is optional — a payment without one is still a payment"
    )


def test_an_amount_that_is_not_a_payment_is_refused_at_the_draft() -> None:
    """The draft crosses the wire and comes back, so it is input. A negative or absurd amount is
    refused here rather than at `into_request`, where the honest answer is no longer a 422."""
    with pytest.raises(InvalidValueError):
        PaymentDraft(recipient_name="Marta", amount_minor=-100)

    with pytest.raises(InvalidValueError):
        PaymentDraft(recipient_name="Marta", amount_minor=99_999_999)


def test_a_zero_amount_is_absent_rather_than_a_payment_of_nothing() -> None:
    """0 is the schema's sentinel for "no amount in the sentence", and it arrives here from the
    model. Reading it as a value would build a payment of nothing; reading it as absent is what it
    means."""
    assert PaymentDraft(recipient_name="Marta", amount_minor=0).amount_minor is None


def test_a_blank_recipient_is_absent_too() -> None:
    assert PaymentDraft(recipient_name="   ").recipient_name is None
    assert PaymentField.RECIPIENT in PaymentDraft(recipient_name="").missing
