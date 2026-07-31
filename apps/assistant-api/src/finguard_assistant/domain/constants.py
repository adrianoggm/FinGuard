"""Hard ceilings of the assistant domain.

Same split as banking-api: these are the limits configuration cannot exceed, not the
ones a deployment applies. A dataclass invariant runs when the object is built, so it
cannot read runtime configuration — the catalogue file narrows within these, never past
them.

They used to be scattered: MAX_PROMPT_LENGTH sat in the DTO, where it looked like a wire
detail rather than a decision about what the assistant will read, and MAX_TEMPERATURE
next to the value object that happened to use it.
"""

# What a single message may carry. It bounds the prompt that reaches a provider, so it is
# a domain limit even though the DTO is where it gets enforced first.
MAX_PROMPT_LENGTH = 4000

# Generation settings.
MAX_TEMPERATURE = 2.0
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_OUTPUT_TOKENS = 1024
