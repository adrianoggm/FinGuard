"""One assertion: the shipped default is `deterministic`, asked of the class.

`visual_planner` is not yet wired into `bootstrap/main.py` -- that is Task 6's last commit, once
`LlmVisualPlanner` exists to give the other three rungs an implementation -- so there is no
`create_app()` case to parametrize the way `test_who_reads_a_turn_is_configurable.py` does for
`turn_interpreter`. What already exists and needs pinning is the property this whole plan's
argument rests on: no model call anywhere until an operator asks for one. A future edit that
flipped the default to `shadow` would put a model call on every charted turn and fail nothing
without this.
"""

from finguard_orchestrator.bootstrap.config import Settings


def test_the_rules_are_what_ships() -> None:
    """Asked of the class, not an instance, so a developer who has flipped their own `.env` does
    not fail a test about what the code ships with.

    `shadow`/`hybrid`/`promoted` all put a model call on a turn that reaches a chart; this
    field's own config comment promises it stays `deterministic` until three runs of
    `just evaluate-visual` say otherwise, and nothing else enforces that promise.
    """
    assert Settings.model_fields["visual_planner"].default == "deterministic"
