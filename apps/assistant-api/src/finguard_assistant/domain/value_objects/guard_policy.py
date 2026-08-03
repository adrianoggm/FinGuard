"""Which deterministic rules are enabled.

Every rule in the guard and the verifier refuses something, so every rule can be wrong in
the expensive direction: it can turn a question the pipeline answers correctly into a dead
end. "Is this rule earning its place?" is therefore a question somebody will ask, and it
deserves a measured answer rather than an argued one.

Naming them and letting each be switched off is what makes that measurable. Disabling one
and re-running the dataset says what it is worth in the only units that matter — how many
unsafe executions it prevents, and how many good answers it costs.

Deny-list rather than allow-list, deliberately. A new rule is on the moment it is written,
so adding one cannot quietly do nothing because a list somewhere was not updated; and the
configuration reads as what somebody chose to give up.
"""

from dataclasses import dataclass, field

from finguard_assistant.domain.errors import InvalidValueError


@dataclass(frozen=True)
class GuardPolicy:
    """The rules that are off. Everything not named here is on."""

    disabled: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.disabled:
            # Nothing to check, and checking would need the registries — which import
            # this module, so the default policy cannot ask them anything without a
            # cycle. Everything-on is also the only policy constructed at import time.
            return

        # Imported here rather than at the top for that same reason: the rule registries
        # live beside the rules they describe, and this is the direction of the loop.
        from finguard_assistant.domain.services import capability_guard, plan_verifier

        known = capability_guard.RULES | plan_verifier.RULES
        unknown = self.disabled - known
        if unknown:
            # A typo must not leave a guard silently on. The quiet outcome is an operator
            # who believes a rule is off, measures with it on, and concludes it is
            # worthless.
            raise InvalidValueError(
                f"unknown guard rules: {sorted(unknown)}; known: {sorted(known)}"
            )

    def allows(self, rule: str) -> bool:
        return rule not in self.disabled


# Everything on. The default a deployment gets unless it says otherwise.
EVERY_RULE = GuardPolicy()
