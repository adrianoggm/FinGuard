"""What the sentence says outright, and which of it the model may not contradict.

The pipeline asks a model three things at once: whether this turn continues the last one, which
fields it changes, and what the new values are. The first two are judgements and the model is good
at them. The third is not a judgement at all when the user has written the answer down:

    "¿Cuánto he gastado en total en los últimos 3 meses en alquiler?"

emitted `changed: ["categories"]`, so the merge kept the previous window — five months — and the
answer was a real figure about a period nobody asked for. Nothing downstream could tell. The plan
was well formed, the domain accepted it, banking executed it, and the verifier had no rule about
windows because the window it produced was perfectly valid.

The defect is not the regex that was missing. It is that **the model was allowed to decide whether
an explicit fact counted**, and a model that says `changed: []` about a sentence containing "los
últimos 3 meses" has contradicted the user rather than interpreted them.

So this module holds the small set of things a sentence states beyond interpretation, and the
merged plan is made to agree with them. The model still decides everything genuinely ambiguous —
whether "comida" is the supermarket or a restaurant, whether a turn is a correction or a new
question, whether an unfamiliar word looks like a shop. It no longer decides what "3 meses" means.

**Narrow on purpose, like every deterministic reader here.** A fact that is only usually right
would overwrite a correct plan, which is worse than the inheritance bug: the model can be
overruled by the sentence, and there is nothing left to overrule this.

Two facts the review asked for are deliberately *not* enforced here, because refusing is already
the better answer for them and refusing is what happens today:

    direction    `SPENDING_ANSWERED_AS_EVERYTHING` refuses a spending question planned as ALL.
                 Silently rewriting it to EXPENSE would also be the pipeline quietly repairing a
                 prompt injection instead of reporting it, and the refusal is the control that
                 test exists for.
    categories   `CATEGORY_MISREAD` refuses a plan filtered by something the sentence did not
                 name. *Setting* the filter instead would drop the categories the model read
                 correctly out of words this vocabulary does not know — the subset rule that
                 module argues for, in reverse.

What is left is the window, which nothing checked at all, and the shape of the answer to a
question about people.
"""

import re
from dataclasses import dataclass, replace
from datetime import date
from typing import Final

from finguard_orchestrator.domain.enums import GroupBy
from finguard_orchestrator.domain.services import category_words, spanish, window_words
from finguard_orchestrator.domain.services.window_words import Window
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

# "¿A qué personas les he hecho Bizum?" does not ask for a total. It asks who, and the answer shape
# is a list of counterparties — which the domain reaches through MERCHANT, because that is the
# column the ledger's counterparty names are in today.
#
# ponytail: MERCHANT is the counterparty column under another name. It is right today because a
# Bizum to Elena stores "Elena" there, and wrong as vocabulary — a person is not a shop. Upgrade
# path is `counterparty_name` and `counterparty_type` in banking plus `GroupBy.COUNTERPARTY` here,
# at which point this constant changes and nothing else does.
_COUNTERPARTY: Final = GroupBy.MERCHANT
_ASKS_WHO: Final = re.compile(
    r"\ba\s+(?:que\s+(?:personas?|gente|contactos?)|quien(?:es)?)\b"
    r"|\bde\s+que\s+empresa\b"
    r"|\bque\s+empresas?\b"
)

# A correction that widens rather than narrows. "¿Y en general?" is a real thing to say, and it is
# the one case where a filter disappearing is what the user asked for.
_WIDENS: Final = re.compile(
    r"\ben general\b|\ben total\b|\bsin filtr|\bde todo\b|\btodas? las categor"
)

# A turn that rejects the previous answer and supplies the replacement in the same breath: "no, los
# últimos 12 meses no; durante todo el año 2025". The intent router's `_CORRECTING` deliberately
# does not cover this — it looks for a rejection *without* a replacement — so the sentence is
# planned as an ordinary follow-up, which is right. What it also needs is for the fields it did not
# mention to survive, and that is what this flag is for.
_REJECTS: Final = re.compile(r"^(no|mejor|en realidad|que va)\b.+|\bsino\b|\bmas bien\b")


@dataclass(frozen=True)
class ExplicitFacts:
    """What this turn states for certain. Every field is None when the sentence did not say."""

    window: Window | None = None
    group_by: GroupBy | None = None
    # Whether the sentence named a category at all. Not *which* — that is the verifier's business.
    # What this answers is "did the user touch the filter", which is what decides whether a
    # correction about the period is allowed to drop it.
    names_a_category: bool = False
    widens: bool = False
    corrects: bool = False


def read(sentence: str, *, today: date | None = None) -> ExplicitFacts:
    """The facts this sentence states outright.

    `today` is what "el año pasado" is relative to. It travels in rather than being read from a
    clock, like everywhere else in this pipeline, so the evaluation can replay a turn.
    """
    folded = spanish.folded(sentence)
    return ExplicitFacts(
        window=window_words.window_in(sentence, today=today),
        group_by=_COUNTERPARTY if _ASKS_WHO.search(folded) else None,
        names_a_category=bool(category_words.precisely_named_in(sentence)),
        widens=bool(_WIDENS.search(folded)),
        corrects=bool(_REJECTS.search(folded.strip(" ¿?¡!.,"))),
    )


def enforce[T: FinancialPlan | TopMovements](plan: T, facts: ExplicitFacts) -> T:
    """The plan, agreeing with what the sentence stated.

    Applied after the merge and before the verifier, so a field the user wrote down survives both
    the model's opinion about whether it changed and the previous plan it would otherwise be
    inherited from.

    A ranking takes the window and not the grouping: `TopMovements` has none, and "a qué personas"
    is not a question about one movement.
    """
    if facts.window is not None:
        # As a pair, never one at a time: a plan carries exactly one of the two, so setting a range
        # while leaving an inherited period behind builds something the domain refuses outright.
        plan = replace(
            plan,
            period=facts.window if isinstance(facts.window, RelativePeriod) else None,
            date_range=facts.window if isinstance(facts.window, AbsoluteRange) else None,
        )
    if facts.group_by is not None and isinstance(plan, FinancialPlan):
        plan = replace(plan, group_by=facts.group_by)
    return plan


def restored_filters(
    plan: FinancialPlan, previous: FinancialPlan, facts: ExplicitFacts
) -> FinancialPlan:
    """The correction's plan, with the filters the correction never mentioned put back.

    "No, los últimos 12 meses no; durante todo el año 2025" is about the window. It came back with
    the window unchanged *and* the restaurant filter gone, so a question about restaurants in one
    year was answered as every expense in another. Two failures, and this is the second: the model
    emitted `categories: []` and the merge had no way to tell "the filter is gone" from "I did not
    copy it", which is the ambiguity `plan_merger` names in its own docstring.

    A correction patches. Widening is still sayable and still honoured — "¿y en general?" clears
    the filter, because that sentence is about the filter.
    """
    if facts.widens or facts.names_a_category:
        return plan
    return replace(
        plan,
        categories=plan.categories or previous.categories,
        merchants=plan.merchants or previous.merchants,
    )
