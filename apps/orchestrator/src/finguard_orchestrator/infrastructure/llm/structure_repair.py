"""The one retry suffix every structured-output reader appends after an invalid answer."""

from typing import Final

STRUCTURE_REPAIR: Final = (
    "\n\nYour previous response did not satisfy the required JSON schema. "
    "Return only one JSON object that matches the supplied schema exactly."
)
