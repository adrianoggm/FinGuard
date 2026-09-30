"""What a credential is allowed to do.

Identity answered "whose money" — the account comes from the credential and the request body
has nowhere to name one. This answers the other half: which operation. One token used to
open every route, including `/admin/llm-policy`, which changes the model that answers every
question for everybody and asked for no credential at all.

The names are the ones §12.2 lists, and they are deliberately per *operation* rather than
per role. A role is a bundle somebody chose once and nobody revisits; an operation is what
an audit entry needs to say. `payments:prepare` and `payments:confirm` are separate for the
reason phase 5 exists: preparing shows a preview and confirming moves money, and a
credential that can do the first is not thereby allowed the second.
"""

from enum import StrEnum


class Scope(StrEnum):
    """Every permission this build knows about.

    An enum rather than free strings so a name nobody recognises cannot become a permission
    by being written down. The token table validates against this, so a typo fails at
    startup instead of granting nothing quietly — a caller who believes they have a
    permission and does not is a support call, and the same string becoming real the day
    somebody adds a scope with that spelling is worse.
    """

    QUESTIONS_READ = "questions:read"
    """Ask a financial question and read the answer. The narrowest useful credential."""

    ASSISTANT_USE = "assistant:use"
    """Send a prompt straight to a model. Separate from questions:read because it is not
    the product — it is a development surface with no plan, no verifier and no ledger."""

    ASSISTANT_ADMIN = "assistant:admin"
    """Change which model answers, for everybody. The one that most needed a scope."""

    MOVEMENTS_CATEGORISE = "movements:categorise"
    """File one of your own movements under a different category.

    A write, and a separate name because scopes here are per operation. Granted to a development
    credential that can already read the account, because it moves no money, reads nothing new and
    is reversible — a wrong label is undone by another request. Withholdable all the same: a
    credential issued to look and not touch does not need it.
    """

    METRICS_READ = "metrics:read"
    METRICS_WRITE = "metrics:write"

    PAYMENTS_PREPARE = "payments:prepare"
    """Build a payment intent and see its preview. Moves no money."""

    PAYMENTS_CONFIRM = "payments:confirm"
    """Move the money. Deliberately not implied by prepare: the human confirmation step of
    §4.1 is meaningless if the credential that drafted the operation can also complete it."""

    AGENTS_ASK = "agents:ask"
    """Ask the external expert agents a question -- a timetable -- through the A2A gateway. It
    sends a third party the activity, the window and a pairwise pseudonym and nothing else, and
    books nothing; booking will be a scope of its own, as confirming a payment is (spec §14 A4)."""
