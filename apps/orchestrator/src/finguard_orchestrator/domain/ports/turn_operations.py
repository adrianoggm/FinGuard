"""The model-led engine's interpreter: one sentence and the canonical state in, operations out.

Not `turn_interpreter.TurnInterpreterPort`, which is the rules engine's and answers with one intent
from a closed list. This one answers with what the turn *does*, as typed operations, and it is the
only component of the engine the person's sentence is ever passed to (spec: "Hard boundaries").

The state goes in whole because the same words are different turns depending on what is on
screen: «¿y en julio?» moves the window of a query, moves both sides of a comparison, or answers a
question asked back.
"""

from collections.abc import Mapping
from datetime import date
from types import MappingProxyType
from typing import Final, Protocol, runtime_checkable
from uuid import UUID

from finguard_orchestrator.domain.engine.operations import Operation
from finguard_orchestrator.domain.engine.state import DialogueState
from finguard_orchestrator.domain.errors import DomainError

# No names known: the selected accounts are shown as a count.
NO_ACCOUNT_NAMES: Final[Mapping[UUID, str]] = MappingProxyType({})


class InterpretationFailedError(DomainError):
    """The model behind the interpreter could not answer at all.

    Not raised for an answer that cannot be read: that one becomes a question back, because the
    model did answer and the person can still be asked. This is the model being unreachable,
    which no question to the person can fix.
    """

    code = "interpretation_failed"


@runtime_checkable
class TurnOperationsPort(Protocol):
    async def interpret(
        self,
        sentence: str,
        state: DialogueState,
        *,
        today: date,
        account_names: Mapping[UUID, str] = NO_ACCOUNT_NAMES,
    ) -> tuple[Operation, ...]:
        """What the turn does, as one to four operations applied in order. Never empty.

        `account_names` names the holder's accounts by id, so the accounts the state selects are
        shown by name (ruling R15). The state holds only ids; without names they are a count.
        """
        ...
