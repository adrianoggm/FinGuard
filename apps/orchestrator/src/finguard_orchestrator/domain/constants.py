"""Hard ceilings of the assistant domain.

Same split as banking-api: these are the limits configuration cannot exceed, not the
ones a deployment applies. A dataclass invariant runs when the object is built, so it
cannot read runtime configuration — the catalogue file narrows within these, never past
them.

They used to be scattered: MAX_PROMPT_LENGTH sat in the DTO, where it looked like a wire
detail rather than a decision about what the assistant will read, and MAX_TEMPERATURE
next to the value object that happened to use it. The same drift had started again — the
payment ceilings lived beside `PaymentRequest`, and `MAX_RANK` was declared twice, at 100
in both places, with the DTO importing one and the value object enforcing the other. Two
declarations of one number is not duplication until they disagree, and then it is a plan
this service accepts and banking rejects.

**Three kinds of number, and only one of them belongs here.** The distinction is worth
stating because "move the magic numbers to configuration" is right about a third of them:

    protocol limits     what a field may carry, so a paragraph cannot travel where an
                        identifier belongs. Here. Not configurable: raising one by
                        environment variable makes two services accept different
                        contracts, and the mismatch surfaces as a 422 nobody expected.

    domain invariants   `rank >= 1`, a payment needs a recipient and an amount, a range
                        cannot end before it starts. Not numbers to centralise at all —
                        they live in the `__post_init__` that enforces them, because the
                        rule and its check are one thing.

    operational policy  timeouts, how many movements a screen lists, whether payments are
                        on, which guard rules run. Configuration, in .env.defaults, where
                        the value sits beside the reason it is what it is. Already there.

A ceiling can have a policy under it: `MAX_PAYMENT_MINOR` is what this service will ever
present, and a deployment lowering it would be a setting clamped to this. That setting
does not exist yet and is a follow-up rather than a silent gap — see docs/pr-phase4.md.
"""

# --- what the assistant will read ------------------------------------------------------

# What a single message may carry. It bounds the prompt that reaches a provider, so it is
# a domain limit even though the DTO is where it gets enforced first.
MAX_PROMPT_LENGTH = 4000

# Generation settings.
MAX_TEMPERATURE = 2.0
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_OUTPUT_TOKENS = 1024

# --- mirrored from the banking contract ------------------------------------------------
#
# Same reasoning as the enums: no shared package, so the assistant states the limits it will
# not exceed and banking stays the authority that enforces them. A value this side allows
# that the other side refuses arrives as a 422 rather than as a wrong figure.

MAX_CATEGORIES = 20
# One number, one name. `MAX_CATEGORY_NAME` was a second name for the same 40 — the length of
# a category identifier — declared for the DTO that bounds the field a holder writes into.
MAX_CATEGORY_LENGTH = 40

# Shops a plan may name.
MAX_MERCHANTS = 10
MAX_MERCHANT_LENGTH = 140

# Accounts explicitly selected for one query. A normal chat names one or two; the
# ceiling prevents a list-shaped scan from travelling through the plan contract.
MAX_SELECTED_ACCOUNTS = 20

# A previously displayed movement selection. It is a narrowing filter, and the ceiling matches
# the largest conversational list the assistant will remember.
MAX_SELECTED_TRANSACTIONS = 50

# Complete bounded evidence for temporal-frequency calculations. When a window contains more rows
# the answer reports incomplete evidence rather than extrapolating from the first page.
MAX_SUPPORTING_MOVEMENTS = 100


MAX_PERIOD_MONTHS = 24
MAX_PERIOD_DAYS = 366

# How far down a ranking anybody may ask. It becomes an OFFSET in banking, which is the
# difference between a question and a scan: "el gasto número 40000" is the second. Banking
# bounds it at the same number, and disagreeing would mean a plan this service accepts and
# that one rejects.
MAX_RANK = 100

# A spoken ranking of individual movements. This one becomes a list somebody listens to, so the
# ceiling stays small: "los 40 mayores gastos" read out loud is not an answer.
MAX_GROUP_RANK = 10

# Aggregate rows one answer may show. Ten was this number too, and it was the wrong ceiling for it:
# "mis top 25 comercios" is twenty-five aggregate rows, not a list dump, and the list-dump risk the
# number above guards is individual movements. Nothing is asked of banking to allow it: group_limit
# is applied by slicing here, after banking has returned its groups bounded by its own MAX_GROUPS,
# and the sentence stays short either way, because answer_writer enumerates MAX_SPOKEN_GROUPS of
# them and no more. A hundred is where ChartSpec.limit already stops, so a number said to the
# planner and a number said to a chart now mean the same thing.
MAX_GROUP_ROWS = 100
MAX_ENTITY_ALIASES = 12
MAX_ENTITY_ALIAS_LENGTH = 48

# --- what the conversation remembers happening -----------------------------------------

# How many turns of history travel with a conversation. There are no sessions here, so the log
# rides the wire, and every number in it is paid for on every request — which is the honest reason
# it is small rather than an arbitrary one. Six covers the ellipsis chains the corpus actually
# contains ("¿y en gasolina?" → "¿y el mes pasado?" → "¿y el segundo?") with room to spare.
MAX_REMEMBERED_TURNS = 6

# One turn's sentence, as the holder wrote it. Bounded well under MAX_PROMPT_LENGTH because six of
# them plus the rules have to fit in one prompt, and a sentence longer than this is not an ellipsis
# needing context — it is a question that says what it wants.
MAX_TURN_SENTENCE = 200
MAX_VISUAL_CONTEXTS = 5

# What we said the turn was about: the query in words, or the question asked back. Ours rather than
# the holder's, and bounded anyway — the log is echoed by the caller, so every field in it is one
# somebody could rewrite before sending it back into a prompt. Six turns times sentence plus note
# is the whole transcript, and it has to stay small enough to be evidence rather than the prompt.
MAX_TURN_NOTE = 400

# --- what a payment may say -------------------------------------------------------------

CURRENCY_LENGTH = 3

# The ceiling the orchestrator will present. Banking has its own, lower or equal, and enforces
# it regardless of this one — the point of having it here is that an absurd figure never
# reaches a confirmation prompt, where a distracted "sí" is all that stands between it and the
# ledger.
MAX_PAYMENT_MINOR = 1_000_000

# A recipient's name as the caller wrote or chose it. Long enough for a full Spanish name with
# two surnames, short enough that the field cannot carry a paragraph into the contact lookup.
MAX_RECIPIENT_NAME = 120

# A note the user wrote. Long enough for "cena del viernes con Marta y Aitor", short enough
# that the field cannot carry a paragraph into the ledger's description.
MAX_CONCEPT = 140

# --- expert agents ---------------------------------------------------------------------
#
# Protocol limits on what a third party's reply may put in front of the holder (spec §6). An agent
# is somebody else's code, so each field its answer can fill is bounded here, where a paragraph
# would otherwise travel into a slot list, a sealed context or a booking preview.

# Slots accepted from one reply, and slots remembered across agents for a booking. Twenty is more
# than a screen lists, and a reply offering more is not a timetable but a dump.
MAX_AGENT_SLOTS = 20
MAX_OFFERS = 20

# Agents one search fans out to. Each is a parallel call inside one turn's budget.
MAX_EXPERT_AGENTS = 10

# What a slot may carry: an activity name as the agent wrote it («Pilates Reformer»), the agent's
# own reference for the session and the code it answers a booking with.
MAX_ACTIVITY_NAME = 64
MAX_SLOT_REF = 64
MAX_BOOKING_CODE = 32

# An agent's card name as the holder reads it («Gimnasio Norte»). A card is a third party's text:
# a longer name, or one with a control or bidi character in it, is shown as the agent's id instead.
MAX_AGENT_NAME = 64

# The A2A `contextId` an agent answered in, kept in the sealed offers so a booking continues it. An
# agent's own identifier, usually a UUID; a longer one is not kept and the booking starts afresh.
MAX_CONTEXT_ID = 128

# The longest session a slot may claim. Four hours covers any class; a longer "slot" is a day.
MAX_SLOT_MINUTES = 240
