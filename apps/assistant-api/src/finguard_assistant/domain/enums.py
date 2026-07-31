from enum import StrEnum


class FallbackPolicy(StrEnum):
    """What to do when the chosen model cannot answer.

    `FAIL` surfaces the error: the caller learns that the model is down. `NEXT` walks
    the rest of the catalogue in order. Which one applies is a configuration decision,
    not a code one: a demo wants NEXT, a measurement run wants FAIL so a silent
    fallback cannot skew the numbers.
    """

    FAIL = "FAIL"
    NEXT = "NEXT"
