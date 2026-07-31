"""Limits and caps of the banking domain.

They live together because they are business policy, not implementation detail.
`MAX_GROUPS` in particular used to sit inside the PostgreSQL adapter even though
§8.6 describes it as the maximum row limit of a query — where the next adapter
would not have seen it.
"""

# Evidence movements returned alongside a figure (vision.md §8.6).
MAX_SUPPORTING_MOVEMENTS = 100
DEFAULT_SUPPORTING_MOVEMENTS = 20

# Rows a single query may return.
MAX_GROUPS = 500

# A query plan arrives from the LLM: everything about it is bounded.
MAX_CATEGORIES = 20
MAX_CATEGORY_LENGTH = 40

# Relative periods.
MAX_PERIOD_MONTHS = 24
MAX_PERIOD_DAYS = 366

# Sentinel keys for grouping rows that carry no category or merchant. They are
# contract values, so English: the assistant phrases them for the user.
UNCATEGORISED = "UNCATEGORISED"
UNKNOWN_MERCHANT = "UNKNOWN_MERCHANT"
