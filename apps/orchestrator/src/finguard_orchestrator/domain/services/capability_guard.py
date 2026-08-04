"""What the domain cannot express, recognised before a model is asked.

A domain service for the same reason answer_writer is one: pure, no framework, and it
speaks about what this domain can and cannot represent.

The point is certainty, not coverage. Refusing what we cannot express was a disposition
of whichever model was configured — it moved from 0 % to 60 % held out by swapping model
— and a disposition is not a control. What follows is a control: it recognises a closed
list of capabilities we know we do not have, which is a far smaller job than reading the
sentence and the only one that can be done with certainty.

Two consequences of that framing are deliberate.

**A false refusal costs more than a miss.** Anything this returns never reaches the
model, so a rule that fires on a question the system *can* answer converts working
behaviour into a dead end. Every family below is therefore narrower than the plain
reading of its name — the discriminating case is "más", which means a comparison in
"¿gasté más este mes que el anterior?" and a ranking in "¿dónde gasto más?", and only the
first is beyond us.

**It does not try to recognise "not about money".** That needs an allow-list of financial
vocabulary, and follow-ups like "¿y este mes?" contain none of it, so the allow-list
would refuse the conversation's most ordinary turn. It is also the cheap half of the
problem: "¿cuál es la capital de Francia?" answered with a spending figure is absurd on
sight, while "¿qué parte de mi sueldo se va en ocio?" answered with a real total is
believed. This guards the second kind.

The families are Spanish because the questions are — the same exception rule 14 makes for
product data.
"""

import re
from typing import Final

from finguard_orchestrator.domain.services import spanish
from finguard_orchestrator.domain.value_objects.guard_policy import EVERY_RULE, GuardPolicy

# Each entry is a capability the domain has no representation for. The name travels with
# the refusal so the answer can say which one, rather than "no puedo con eso".
_FAMILIES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "AVERAGE",
        re.compile(r"\b(medio|media|promedio)\b", re.IGNORECASE),
    ),
    (
        "RATIO",
        re.compile(r"(\bporcentaje\b|\bque parte de\b|\bque proporcion\b|%)", re.IGNORECASE),
    ),
    (
        # One extreme movement, not an aggregate of many. This produced the worst answer the
        # system has given: "¿cuál ha sido el mayor gasto?" came back as *3.977,37 € en los
        # últimos 3 meses, en 56 movimientos* — the sum of every expense in the window, offered
        # as the answer to a question about one of them. Nothing downstream could tell, because
        # the plan was valid and the figure is real; it answers a different question.
        #
        # `Metric` is SUM or COUNT, so the maximum is not expressible. Until it is, refusing is
        # the only honest option: a total in place of a maximum is not approximate, it is false.
        #
        # **The discriminator is grammatical.** A superlative attached to a noun asks for one
        # movement — "el gasto más alto", "la compra más cara", "mayor importe". "Gasté más" and
        # "gasto más" are verbs and ask which group is largest, which `groupBy` expresses and the
        # COMPARISON note below already argues must not be refused.
        #
        # **Before COMPARISON, and that ordering is load-bearing.** "El pago más grande *que* he
        # hecho este año" contains "más … que" and is not a comparison: the "que" introduces a
        # relative clause, not a second term. No pattern tells those apart, so the sentence that
        # names a superlative noun is read as being about that noun. Both outcomes refuse, but
        # the family name is what the refusal says out loud, and the wrong name explains the
        # wrong thing.
        "LARGEST_MOVEMENT",
        re.compile(
            r"\b(mayor|maxim[oa])\s+(gasto|compra|pago|movimiento|cargo|importe|cantidad)"
            r"|\b(gasto|compra|pago|movimiento|cargo|importe|factura)s?\s+"
            r"(mas|de\s+mayor)\s+(alt[oa]|car[oa]|grande|elevad[oa]|importe)",
            re.IGNORECASE,
        ),
    ),
    (
        # "más" alone is not a comparison: "¿dónde gasto más?" asks which group is
        # largest inside one window, which groupBy expresses. What the domain cannot do
        # is relate two windows, and in Spanish that needs the second term — "más X *que*
        # Y". Comparing without "que" is possible but it is phrased with these words.
        "COMPARISON",
        re.compile(r"(\bmas\b.*\bque\b|\bcomparad|\bcomparacion\b|\bfrente a\b)", re.IGNORECASE),
    ),
    (
        # Only what already happened is in the ledger.
        "FORECAST",
        re.compile(
            r"(\bvoy a\b|\bllegare\b|\ba este ritmo\b|\bprevision\b|\bpreveo\b"
            r"|\bacabare\b|\bterminare\b)",
            re.IGNORECASE,
        ),
    ),
    (
        # Reading a ledger is not advising on it, and the distance between the two is
        # regulatory rather than technical.
        "ADVICE",
        re.compile(
            r"(\bdeberia\b|\bpodria ahorrar\b|\brecortar\b|\baconsej|\basesor"
            r"|\bque hacer con\b|\bme recomiendas\b|\blimite de gasto\b|\bpresupuesto\b)",
            re.IGNORECASE,
        ),
    ),
    (
        # A request to act, not to read. Phase 5 adds payments behind a confirmation
        # step; until it does, and after, this path only answers questions.
        "ACTION",
        re.compile(
            r"\b(hazle|haz un|hazme|envia|manda|mandale|transfiere|paga|cambiame|cambia)\b",
            re.IGNORECASE,
        ),
    ),
)


# The names, for whoever needs to enumerate them: an ablation loop, a configuration file,
# and the policy that validates its own input.
RULES: Final = frozenset(name for name, _ in _FAMILIES)


def unsupported_reason(sentence: str, policy: GuardPolicy = EVERY_RULE) -> str | None:
    """The capability the sentence needs and the domain does not have, or None.

    None means "no known-missing capability was named", not "answerable". The model still
    decides that, and everything after it still checks.

    `policy` exists so a rule can be switched off and the difference measured. It defaults
    to everything on, because a guard that is off by accident is worse than one that was
    never written — it reads like a control.
    """
    # Folded once, and every pattern above is written unaccented. Nobody types accents into a
    # chat box: the verifier's count rule needed one and therefore never fired on the sentence
    # that produced the worst answer in the reported conversation.
    folded = spanish.folded(sentence)
    for name, pattern in _FAMILIES:
        if policy.allows(name) and pattern.search(folded):
            return name
    return None
