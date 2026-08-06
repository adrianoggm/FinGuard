"""Identity and authorisation — the two layers of §12.1 that were missing.

The property being pinned is not "authentication works". It is that **there is nowhere
left to name an account**: the request body no longer carries one, so a caller cannot
ask about somebody else's money even if it wants to. A test that only checked a valid
token gets a 200 would pass on the old design too.
"""

import uuid

import pytest

from finguard_orchestrator.domain.errors import InvalidValueError, NotAuthenticatedError
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.infrastructure.dto.llm_dto import QuestionRequest
from finguard_orchestrator.infrastructure.security.static_token_identity import StaticTokenIdentity

LAURA = Caller(holder_reference="ES-0001", account_id=uuid.uuid4())
MARTA = Caller(holder_reference="ES-0002", account_id=uuid.uuid4())
TOKENS = {"laura-development-token": LAURA, "marta-development-token": MARTA}


def identity() -> StaticTokenIdentity:
    return StaticTokenIdentity(TOKENS)


# --- the shape of the request ------------------------------------------------------


def test_the_body_cannot_name_an_account() -> None:
    """The load-bearing one. The account used to travel here, which meant any caller
    could read any account; `extra="forbid"` now makes sending one an error rather than
    something quietly ignored."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        QuestionRequest.model_validate(
            {"question": "¿cuánto gasté?", "account_id": str(uuid.uuid4())}
        )


def test_a_question_needs_nothing_but_the_question() -> None:
    request = QuestionRequest.model_validate({"question": "¿cuánto gasté el mes pasado?"})

    assert request.question
    assert not hasattr(request, "account_id")


# --- resolving a credential --------------------------------------------------------


async def test_a_known_token_resolves_to_its_own_account() -> None:
    caller = await identity().resolve("Bearer laura-development-token")

    assert caller == LAURA


@pytest.mark.parametrize(
    "credential",
    [
        "laura-development-token",
        "Bearer laura-development-token",
        # HTTP auth schemes are case-insensitive (RFC 7235). This used to resolve to
        # nobody, so a client following the spec could not log in.
        "bearer laura-development-token",
        "BEARER laura-development-token",
        "  Bearer   laura-development-token  ",
    ],
)
async def test_the_scheme_is_accepted_however_it_is_spelled(credential: str) -> None:
    assert await identity().resolve(credential) == LAURA


@pytest.mark.parametrize("credential", [None, "", "   ", "Bearer ", "Bearer wrong-token"])
async def test_anything_but_a_known_token_is_refused(credential: str | None) -> None:
    with pytest.raises(NotAuthenticatedError):
        await identity().resolve(credential)


async def test_the_refusal_says_nothing_useful_to_a_guesser() -> None:
    # A message distinguishing "unknown token" from "no token" tells an attacker which
    # half of the guess was right.
    missing = None
    wrong = None
    try:
        await identity().resolve(None)
    except NotAuthenticatedError as exc:
        missing = str(exc)
    try:
        await identity().resolve("Bearer nope-nope-nope-nope")
    except NotAuthenticatedError as exc:
        wrong = str(exc)

    assert missing == wrong


# --- authorisation -----------------------------------------------------------------


def test_a_caller_may_read_only_its_own_account() -> None:
    assert LAURA.may_read(LAURA.account_id)
    assert not LAURA.may_read(MARTA.account_id)
    assert not LAURA.may_read(uuid.uuid4())


def test_a_caller_without_a_holder_is_not_a_caller() -> None:
    with pytest.raises(InvalidValueError):
        Caller(holder_reference="   ", account_id=uuid.uuid4())


# --- refusing to start in a bad state -----------------------------------------------


def test_an_empty_token_table_refuses_to_start() -> None:
    """Starting with nobody configured sounds safe and is worse: every request 401s,
    and the first person to debug it is tempted to loosen something."""
    with pytest.raises(ValueError):
        StaticTokenIdentity({})


def test_a_short_token_refuses_to_start() -> None:
    with pytest.raises(ValueError):
        StaticTokenIdentity({"short": LAURA})


def test_a_duplicated_token_refuses_to_start() -> None:
    """Two entries sharing a secret is a typo, and the quiet outcome is the dangerous
    one: the token resolves to whichever account was configured last."""
    from finguard_orchestrator.bootstrap.config import Settings

    settings = Settings(
        dev_tokens=(
            f"shared-development-token:ES-0001:{LAURA.account_id}",
            f"shared-development-token:ES-0002:{MARTA.account_id}",
        )
    )

    with pytest.raises(ValueError, match="twice") as caught:
        settings.callers()

    assert "shared-development-token" not in str(caught.value)


def test_authorisation_is_a_check_and_not_a_comment() -> None:
    from finguard_orchestrator.domain.errors import NotAuthorisedError

    LAURA.assert_may_read(LAURA.account_id)

    with pytest.raises(NotAuthorisedError):
        LAURA.assert_may_read(MARTA.account_id)
