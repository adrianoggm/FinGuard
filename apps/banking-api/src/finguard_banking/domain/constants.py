"""Hard ceilings of the banking domain.

These are not the limits a deployment applies — they are the ones it cannot exceed.
A dataclass invariant is evaluated when the object is built, so it cannot read runtime
configuration; that is why the split exists:

    domain/constants.py   the ceiling. Protection that configuration cannot raise.
    bootstrap/config.py   the policy. What this deployment actually allows, always
                          clamped to the ceiling.

So `BANKING_MAX_SUPPORTING_MOVEMENTS=50` narrows the limit and `=5000` does not widen
it past 100.
"""

# Evidence movements returned alongside a figure (vision.md §8.6).
MAX_SUPPORTING_MOVEMENTS = 100
DEFAULT_SUPPORTING_MOVEMENTS = 20

# Rows a single query may return.
MAX_GROUPS = 500

# A query plan arrives from the LLM: everything about it is bounded.
MAX_CATEGORIES = 20
MAX_CATEGORY_LENGTH = 40

# Shops a query may name. Free text, unlike a category, because the ledger's merchant names
# come from the movements themselves and no enum can enumerate them. Bounded anyway: a filter
# with fifty names in it is not a question anybody asked.
MAX_MERCHANTS = 10
MAX_MERCHANT_LENGTH = 140

# Explicit account ids are a narrowing filter, not an alternate bulk-read endpoint.
MAX_SELECTED_ACCOUNTS = 20

# Transaction identifiers supplied to revisit an exact, previously displayed selection.
MAX_SELECTED_TRANSACTIONS = 50

# How far back a card screen may ask. A card is a thing somebody checks about now, and a
# five-year window is a statement, not a screen.
MAX_CARD_WINDOW_DAYS = 366

# Relative periods.
MAX_PERIOD_MONTHS = 24
MAX_PERIOD_DAYS = 366

# A single payment, in minor units: 10.000 €. The ceiling, not the policy — a deployment lowers it
# and the balance is what actually limits a payment. It is here because the request arrives from a
# sentence somebody spoke, and a misheard amount should hit a wall well before the balance does.
MAX_PAYMENT_MINOR = 1_000_000

# A recipient's name as the caller wrote it. Long enough for a full Spanish name with two surnames,
# short enough that the field cannot carry a paragraph into the contact lookup.
MAX_RECIPIENT_NAME = 120

# The categories the ledger uses. They are the bank's, not the assistant's: a movement's category
# is a fact about the ledger, and the planner's copy of this list exists so a constrained decoder
# cannot emit anything else — which is a different job from validating what a holder may assign.
#
# A closed list, and that is the answer to "can a user invent one": not today. The list reaches the
# model's JSON schema, the prompt's glossary and this validation, so a category somebody made up
# would be a category the assistant could never name back to them.
CATEGORIES = (
    "GROCERIES",
    "TRANSPORT_FUEL",
    "RESTAURANTS",
    "TRANSPORT_PUBLIC",
    "HEALTH",
    "LEISURE",
    "SUBSCRIPTIONS",
    "HOUSING_RENT",
    "SALARY",
    "TRANSFER_BIZUM",
    # Interest a savings account paid. Income, and the ledger has been writing it since the piggy
    # bank existed while this list did not have it — so it could not be asked about, could not be
    # chosen as an override, and rendered as "Otros" on the one screen where it is every movement.
    # Adding it widens what may be *asked*; it changes no stored figure.
    "INTEREST",
)

# Sentinel keys for grouping rows that carry no category or merchant. They are contract
# values, so English: the assistant phrases them for the user.
UNCATEGORISED = "UNCATEGORISED"
UNKNOWN_MERCHANT = "UNKNOWN_MERCHANT"

# How far down a ranking a caller may ask. It becomes an OFFSET, so this is the difference between
# a question and a scan: "el gasto número 40000" is the second.
MAX_RANK = 100
