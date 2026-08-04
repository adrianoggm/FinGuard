"""The JSON Schema the decoder is constrained to when reading a payment.

Written out rather than generated, unlike the query plan's: there are five fields and one of them is
an enum of a single currency. The plan's schema is generated because a hand-copied list of nine
period names goes stale; nothing here is a list that grows.

The same two decisions carry over, and both matter more here than there:

**It admits refusal.** If the schema only described a payment, a constrained decoder could not emit
anything else — the model would be physically unable to say "how much?" even when the sentence does
not say, so a missing amount would come back as a confident, schema-valid guess. On the query path
that costs a wrong figure; here it would cost a prompt to move money nobody named.

**Every field is required, with sentinels for the unused ones.** Optional fields make a small model
choose twice — whether to emit the key and what to put in it — and it gets the first question wrong
often enough to matter.

`amountMinor` is an integer of minor units, with no maximum here. The ceiling is a domain invariant
in `PaymentRequest` and a `CHECK` in banking; putting it in the schema too would mean three places
to change and a constrained decoder quietly clamping a figure instead of the pipeline refusing it.
"""

from typing import Any, Final

from finguard_orchestrator.domain.enums import PlanningOutcome
from finguard_orchestrator.domain.services.payment_grounding import SUPPORTED_CURRENCY
from finguard_orchestrator.domain.value_objects.payment_request import MAX_CONCEPT

# The outcomes a model may choose about its own reading. The other three are things that happen TO
# a model — malformed output, a rejected value, a dead daemon — and offering them would only invite
# it to explain away its own mistakes.
CHOOSABLE_OUTCOMES: Final = (
    PlanningOutcome.PLANNED,
    PlanningOutcome.NEEDS_CLARIFICATION,
    PlanningOutcome.UNSUPPORTED,
)

PAYMENT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "outcome": {"type": "string", "enum": [outcome.value for outcome in CHOOSABLE_OUTCOMES]},
        # Spanish, because it reaches the user. Empty when the outcome is not a question.
        "question": {"type": "string"},
        # The name as the sentence writes it, not a normalised one: it is checked against the
        # sentence, and banking resolves it against the account's own contacts.
        "recipient": {"type": "string"},
        # Minor units. 0 is the sentinel for "no amount in the sentence".
        "amountMinor": {"type": "integer", "minimum": 0},
        # One value, so a payment in another currency is unreachable rather than refused later.
        "currency": {"type": "string", "enum": [SUPPORTED_CURRENCY]},
        # What the payment is for, in the user's words. Free text, like a merchant name and for the
        # same reason: a note is data rather than vocabulary. Empty when the sentence gives none.
        #
        # It was being read from the answer before this existed, which meant never: the schema
        # forbids additional properties, so a model that wanted to emit one could not.
        "concept": {"type": "string", "maxLength": MAX_CONCEPT},
    },
    "required": ["outcome", "question", "recipient", "amountMinor", "currency", "concept"],
    "additionalProperties": False,
}
