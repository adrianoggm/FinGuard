"""D-H-6 (fix wave): every continuation-kind reader stamps `plan_continues_conversation`.

`AskFinancialQuestionHandler._answering` (F-H3) centralises the stamp for the nineteen-reader
cascade -- `claimed.reader in _CONTINUATION_READERS` (the `_ON_A_COMPARISON` and
`_ON_THE_QUERY_IN_VIEW` runs), stamped once rather than in each producer method. That
centralisation is real, but it is not the whole truth: the standalone `DRILL_DOWN` branch in
`_steps` answers *before* the cascade's own dispatch is ever reached (its own comment says so --
"outside `_answering`'s reader-registry dispatch") and stamps the flag by hand, a second site a
future continuation path could just as easily forget. `llm_mapper._remember_visual_context`'s
`derived_from` reads this flag as the one thing that tells "this chart reshapes that one" apart
from "the model read the same window again, coincidentally" (F-H3) -- a reader that continues the
conversation but forgets to say so silently loses lineage, not loudly.

This is the registry walk the brief's own "or the stamp centralised in the analyser" alternative
asks for, driven end to end rather than asserted from the tables alone: one turn per
continuation-kind shape -- comparison-on-view, and reshape/regroup/split (all three shapes
`contextual_follow_up_plan` reads) for the cascade's own centralisation, plus the standalone drill
branch for the site the cascade's centralisation cannot reach. A future reader that joins either
run, or a new standalone branch like the drill one, is not covered here by construction -- but
every shape this wave's own audit named is.
"""

from __future__ import annotations

from test_decisions_call_sites import turn_for
from test_drill_down import FALLBACK_PLAN, MONTH_CONVERSATION, RecordingLedger
from test_visual_follow_up import CHARTED, PREVIOUS

from finguard_orchestrator.domain.models.conversation import IDLE


async def test_a_comparison_on_view_stamps_the_continuation_flag() -> None:
    """`_ON_A_COMPARISON` (`_direct_period_comparison`/`_comparison_with_previous_period`):
    a comparison needs no chart or previous plan in view to be a continuation-kind reader in the
    registry's own sense -- it is asked from the sentence alone, and `_answering` stamps every
    reader in this run regardless."""
    result, _ = await turn_for("¿He gastado más este mes que el anterior?", None, conversation=IDLE)
    assert result.plan_continues_conversation is True


async def test_a_regroup_over_the_query_in_view_stamps_the_continuation_flag() -> None:
    """`_ON_THE_QUERY_IN_VIEW` (`contextual_follow_up_plan`), the categorical/calendar regroup
    shape -- same fixture `test_the_handler_and_the_mapper_together_prove_derived_from` already
    proves `derived_from` from; this asserts the flag `derived_from` itself depends on."""
    result, _ = await turn_for("agrúpalo por meses", PREVIOUS, conversation=CHARTED)
    assert result.plan_continues_conversation is True


async def test_a_reshape_over_the_query_in_view_stamps_the_continuation_flag() -> None:
    """The same reader's calendar-regroup shape over a different bucket -- "por semanas" (U-5's
    own WEEK-grouping sentence), proving the stamp is not somehow tied to the specific bucket
    the other two tests exercise."""
    result, _ = await turn_for("por semanas", PREVIOUS, conversation=CHARTED)
    assert result.plan_continues_conversation is True


async def test_a_split_over_the_query_in_view_stamps_the_continuation_flag() -> None:
    """The same reader's split-by-names shape (`_SPLIT_BY_NAMES`, D-H-9's own vocabulary) --
    "sepáralo en transporte público y gasolina" names two real categories `category_words` knows."""
    result, _ = await turn_for(
        "sepáralo en transporte público y gasolina", PREVIOUS, conversation=CHARTED
    )
    assert result.plan_continues_conversation is True


async def test_the_standalone_drill_branch_stamps_the_continuation_flag() -> None:
    """The one continuation path outside the registry's own centralisation: `_steps`'s
    `DRILL_DOWN` branch answers before `_answering`'s dispatch table is ever consulted and stamps
    the flag by hand (its own comment says why) -- the site D-H-6 exists to keep honest."""
    ledger = RecordingLedger()
    result, _ = await turn_for(
        "¿Por qué agosto es tan alto?",
        FALLBACK_PLAN,
        ledger=ledger,
        conversation=MONTH_CONVERSATION,
    )
    assert result.outcome == "PLANNED"
    assert len(ledger.calls) == 1
    assert result.plan_continues_conversation is True
