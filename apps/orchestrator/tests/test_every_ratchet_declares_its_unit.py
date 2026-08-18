"""No number in a test file may be a bare integer nobody can question.

`UNMEASURED = 107` was written from a count of turns and compared against a count of distinct
sentences. Both numbers were right about something; neither was right about the other, and the
ratchet passed in green for as long as it existed with thirty-five sentences of slack under it.

The fix for that one was to correct the literal. The fix for the *category* is this file: a
module-level integer constant in `tests/` is either a `Ceiling`, a `Floor`, or a name listed below
with a reason. There is no fourth option, so the next one cannot be written by accident.

This reads the source rather than importing it, deliberately — a constant is a fact about the file
somebody edits, and an import would only see what the module chose to expose.
"""

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent

# Integers that are not ratchets, with why. Anything not here has to declare its unit.
NOT_A_RATCHET: dict[str, str] = {
    "MAX_TURNS": "a bound on a loop, not a measurement of the corpus",
}


def _module_level_integers(path: Path) -> list[str]:
    """Every `NAME = <int>` at the top level of a test module, in the order they appear."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, int) or isinstance(node.value.value, bool):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.isupper():
                found.append(target.id)
    return found


def test_no_test_file_holds_a_ratchet_that_cannot_say_what_it_counts() -> None:
    """The whole category, closed by construction.

    A `Ceiling` or a `Floor` counts the collection it is given and prints its unit in the failure,
    so the number and the thing it is about cannot be two different statements. A bare `int` can be
    anything, and one of them was.
    """
    bare = {
        f"{path.name}:{name}"
        for path in sorted(TESTS.glob("test_*.py"))
        for name in _module_level_integers(path)
        if name not in NOT_A_RATCHET
    }

    assert not bare, "these compare against a number with no unit on it:\n" + "\n".join(
        f"  {where}" for where in sorted(bare)
    )
