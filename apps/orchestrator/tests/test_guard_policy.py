"""Which deterministic rules are on, and what each one is worth.

Every rule here refuses something, and a rule that refuses is a rule that can be wrong in
the expensive direction — it turns a question the pipeline answers into a dead end. That
makes "is this rule earning its place?" a question somebody will ask, and it deserves an
answer that is measured rather than argued.

So each rule is named, individually switchable, and its contribution is a number.
"""

import pytest

from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.services import capability_guard, plan_verifier
from finguard_orchestrator.domain.value_objects.guard_policy import GuardPolicy


def test_every_rule_is_on_by_default() -> None:
    everything = GuardPolicy()

    assert capability_guard.unsupported_reason("¿Cuál es mi gasto medio?", everything) is not None


def test_a_disabled_family_stops_firing() -> None:
    """The point of the switch: measuring a rule means running without it, and running
    without it must be a configuration rather than an edit."""
    without_averages = GuardPolicy(disabled=frozenset({"AVERAGE"}))

    assert capability_guard.unsupported_reason("¿Cuál es mi gasto medio?", without_averages) is None


def test_disabling_one_family_leaves_the_others_alone() -> None:
    without_averages = GuardPolicy(disabled=frozenset({"AVERAGE"}))

    assert (
        capability_guard.unsupported_reason("¿Voy a gastar mucho?", without_averages) == "FORECAST"
    )


def test_an_unknown_rule_name_is_refused_at_construction() -> None:
    """A typo in configuration must not silently leave a guard on. The quiet outcome is
    an operator who believes a rule is off, measures with it on, and concludes it is
    worthless."""
    # A DomainError and not a bare ValueError: an invariant that fails carries a code the
    # HTTP layer maps without matching on message text.
    with pytest.raises(InvalidValueError, match="unknown"):
        GuardPolicy(disabled=frozenset({"AVERGE"}))


def test_the_rule_names_are_discoverable() -> None:
    # An ablation loop needs to enumerate them, and so does anybody writing the
    # configuration. Neither should have to read the regexes.
    assert "AVERAGE" in capability_guard.RULES
    assert "SPENDING_ANSWERED_AS_EVERYTHING" in plan_verifier.RULES
    assert not capability_guard.RULES & plan_verifier.RULES, "a name means one rule"
