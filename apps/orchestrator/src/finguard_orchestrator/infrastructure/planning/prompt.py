"""The fence a person's words go in before they reach a prompt.

The words are data and can only ever be delimited, because arbitrary Spanish has no schema to
parse them against. The visual planner and the expert agents fence what they quote with this.
"""

from typing import Final

# A question containing the closing marker would otherwise end the data block and continue as
# instructions, so the marker is removed from the text before fencing — there is no escaping to
# get wrong.
_OPEN: Final = "<<<QUESTION"
_CLOSE: Final = "QUESTION>>>"


def fence(sentence: str) -> str:
    """The question, with no way to leave its own block."""
    return sentence.replace(_CLOSE, " ").replace(_OPEN, " ")
