"""What a credential is allowed to do, not only who it is.

Identity and authorisation were the two layers §12.1 puts first and phases 1–3 skipped. The
account came from the credential, which closed "whose money", and nothing closed "which
operation": one token opened `/v1/questions`, `/v1/messages`, and `/admin/llm-policy` —
which changes the model that answers every question and asks for no credential at all.

That is defensible only while nothing is published beyond 127.0.0.1, and it stops being
defensible at phase 5, where confirming a payment and reading a total must not be the same
permission.
"""

import uuid

import pytest

from finguard_orchestrator.domain.errors import InvalidValueError, NotAuthorisedError
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.scope import Scope

ACCOUNT = uuid.uuid4()


def caller(*scopes: str) -> Caller:
    return Caller(holder_reference="ES-0001", account_id=ACCOUNT, scopes=frozenset(scopes))


def test_a_caller_may_do_what_its_scopes_allow() -> None:
    laura = caller(Scope.QUESTIONS_READ)

    laura.assert_may(Scope.QUESTIONS_READ)


def test_a_caller_may_not_do_what_they_were_not_granted() -> None:
    """Deny by default, and the admin scope is the one that matters: it changes which model
    answers every question for everybody."""
    laura = caller(Scope.QUESTIONS_READ)

    with pytest.raises(NotAuthorisedError):
        laura.assert_may(Scope.ASSISTANT_ADMIN)


def test_reading_a_total_and_confirming_a_payment_are_different_permissions() -> None:
    # The distinction phase 5 needs, arriving before phase 5 rather than during it.
    reader = caller(Scope.QUESTIONS_READ)

    assert not reader.may(Scope.PAYMENTS_CONFIRM)
    assert not reader.may(Scope.PAYMENTS_PREPARE)


def test_a_scope_this_build_does_not_have_is_refused_at_construction() -> None:
    """A typo in the token table must not grant something, and must not silently grant
    nothing either: a caller who believes they have a permission and does not is a support
    call, while a name nobody validates is how an unknown string becomes a permission the
    day somebody adds a scope with that spelling."""
    with pytest.raises(InvalidValueError, match="unknown"):
        caller("assistant:aministrator")


def test_no_scopes_means_no_scopes() -> None:
    # An empty set is a legitimate credential that can authenticate and do nothing, which
    # is the correct reading of "granted nothing".
    with pytest.raises(NotAuthorisedError):
        caller().assert_may(Scope.QUESTIONS_READ)
