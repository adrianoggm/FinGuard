"""The conversation corpus, run as a test and reported as a measurement.

`evaluation/conversations/*.yaml` holds whole conversations — six to twenty turns, mixing ellipsis,
corrections, abrupt changes of subject, suspended operations, typos and colloquial Spanish. This
file is what runs them.

Two things come out of it. Every episode is a test, so a regression names the conversation and the
turn it broke on rather than a line number. And the whole corpus is a report, with the rates the
review asked for: wrong tool, unnecessary clarification, lost context, unsafe action.

**It measures routing, not figures, and no model runs.** Which of eleven places handled each turn is
a decision this service makes on its own, and every defect reported from the demo so far has been
one of those decisions rather than a wrong number. A corpus that needed a model could not run in CI
and would measure the model instead.
"""

import pytest
from support import episodes
from support.ratchets import Ceiling, Floor

CORPUS = episodes.load()

# One replay, read by the rates and by the coverage rule. Both are facts about the same walk of the
# same conversations, and running them twice would double the slowest test in the suite.
REPORT = episodes.run(CORPUS)


# A floor rather than an equality, so adding an episode is never a failing build — and a floor at
# all, so a glob that matches nothing cannot pass by finding no work. Raise it as files land, never
# lower it.
#
# It said 130 against 164 episodes, which is a third of the corpus that could have gone missing
# without this noticing. Unlike the coverage rule below, this one cannot be derived from what the
# corpus does — its whole job is to fail when the corpus is not there to be measured — so it stays
# a literal, and a literal that is not kept level with what exists is a check about the past.
FLOOR = Floor(166, "episodes loaded from evaluation/conversations")


def test_the_corpus_has_not_shrunk() -> None:
    assert not FLOOR.undercut_by(CORPUS), FLOOR.undercut_by(CORPUS)


# Episodes still replayed without an account directory, which is a path no deployment runs.
#
# It was 169 of 169 and the corpus could not see that: the harness wired no directory at all, so
# every turn took three branches of `_scoped` written for the handler's legacy callers, no turn had
# ever seen the account clarification, and `lost_context` read 0.0 % over 1143 turns while the
# account scope was untested end to end.
#
# Six were left, and the note here said all six were the one defect: a scope the service derived
# for itself, persisted as though the holder had chosen it by an unguarded twin of
# `_remember_explicit_scope` that ran immediately after the guarded one. That defect is fixed and
# three came off this list — a question about last month's spending no longer answered from inside
# an index fund, two bizum-received turns no longer answered from one account, "de mis cuentas"
# answered from all of them again.
#
# The other three were never that defect, and each still reproduces with the fix reverted:
#
#   14-averages   `_scope_ranking` reads "la nómina" as the CURRENT alias, the reading `_scoped`
#                 strips on purpose because it hides payroll paid into another account.
#   17-internal   turn 3 states «al fondo», which is kept as designed, and turns 5-6 inherit it
#                 into questions about supermarkets and restaurants. How long a *stated* selection
#                 lives, not where it came from.
#   28-compound   a ratio paired with a plain query keeps the settled selection through the first
#                 operand and loses it before the second, so one sentence answers half a figure
#                 and half «¿En qué cuenta quieres consultarlo?».
#
# Stating those wrong accounts in the expectations would take this number to zero by recording a
# defect as correct, which is the one way it must never move. Only ever down, and only by a source
# fix. The reason each of the three is here is written above its `scope: legacy` in the corpus file.
#
# `test_a_filter_that_is_doing_something_is_stated` is the instrument that discriminates, not the
# per-episode tests: an episode whose expectations never mention `accounts` passes with a wrong
# scope. Under the reverted fix that rule listed ten turns over five episodes; it now lists none.
LEGACY_SCOPE = Ceiling(3, "episodes still replayed without the account directory")


def test_the_corpus_runs_on_the_path_a_deployment_runs() -> None:
    legacy = [e.name for e in CORPUS if e.scope == episodes.LEGACY]

    assert not LEGACY_SCOPE.exceeded_by(legacy), LEGACY_SCOPE.exceeded_by(legacy)


def test_every_episode_declares_a_scope_the_harness_knows() -> None:
    """A typo in `scope:` must not quietly mean the default, because the default is the one value
    that hides the account clarification."""
    unknown = [(e.name, e.scope) for e in CORPUS if e.scope not in episodes.SCOPES]

    assert not unknown, f"episodes declaring a scope nothing implements: {unknown}"


def test_some_episodes_reach_the_account_clarification() -> None:
    """The floor under the other side of `scope`. `settled` is the default precisely so the
    clarification does not cost 169 preamble turns — which makes it possible for the corpus to end
    up never reaching it at all, and a corpus that never reached it is where this started."""
    asking = [e.name for e in CORPUS if e.scope == episodes.UNSET]

    assert len(asking) >= 4, f"only {len(asking)} episodes open with the account scope unsettled"


def test_every_episode_is_long_enough_to_be_a_conversation() -> None:
    """Six turns is the floor the review set, and it is the right one: below it an episode is a
    pair of sentences, and the defects this corpus exists for live further in than that."""
    short = [(e.name, len(e.turns)) for e in CORPUS if len(e.turns) < 6]

    assert not short, f"episodes shorter than six turns: {short}"


def test_every_turn_that_runs_a_query_says_which_one() -> None:
    """Coverage as a rule rather than as a number somebody raises.

    This was two literals — 56 turns, 164 fields — and a literal only discriminates on the day it
    is written. It said nothing about the other 323 turns that ran a query and asserted none of it,
    and it would have gone on saying nothing while they grew: adding a turn cannot lower a floor.

    The rule that replaces it needs no maintenance and cannot drift, because it is derived from
    what the corpus executes. A turn that runs a query and does not say which one is a turn whose
    routing is checked and whose plan is not — which is exactly how a follow-up that inherited the
    wrong window, dropped its category or flipped its direction scored as correct.

    The second literal is gone rather than replaced. It counted fields, to stop the turn count
    being raised by expectations too thin to check anything — but a floor derived from execution
    cannot be gamed that way: adding a turn adds an obligation instead of headroom. A rule against
    thin expectations was written and then deleted, because the corpus deliberately has two: "¿cuál
    es mi gasto medio?" asked with a half-built Bizum open asserts the divisor and nothing else,
    and that is the whole point of those turns.
    """
    assert REPORT.unasserted == [], "\n".join(
        ["turns that ran a query in silence:", *REPORT.unasserted]
    )


def test_a_filter_that_is_doing_something_is_stated() -> None:
    """The other half of the coverage rule, and the answer to what the exclusions raised.

    Asserting a field on every turn is what found the fifteen filler merchants — but six fields
    already make a line nobody reads before appending to it, and four more would make the corpus
    unreadable to buy nothing on the turns where the value is empty. So the fields that change what
    a figure *means* — a subset of accounts, movement between the holder's own accounts, a named
    counterparty — are asserted where they are doing something, and this rule is what makes "where"
    impossible to get wrong: a plan carrying one and an expectation silent about it is the same
    hole the exclusions had.

    It found two wrong figures on the turn it was written. Both were "¿cuánto gasté el mes pasado?"
    run restricted — one to the fund and to internal movements only, the other to internal
    movements only. The holder asked what they spent and got what they had moved into their own
    accounts, with the right window on it. Both are fixed, and each by a different branch reading
    the same predicate: a sentence that states a whole financial question of its own is neither the
    answer to an open clarification nor an ellipsis over the query in view.
    """
    assert REPORT.unstated == [], "\n".join(
        ["filters a query carried and its turn did not mention:", *REPORT.unstated]
    )


def test_every_episode_says_why_it_exists() -> None:
    """A case with no reason is a case nobody can tell from a duplicate, and this corpus is going to
    keep growing."""
    assert [e.name for e in CORPUS if not e.why.strip()] == []


@pytest.mark.parametrize("episode", CORPUS, ids=lambda e: e.name)
def test_episode(episode: episodes.Episode) -> None:
    failures = list(episodes.walk(episode))

    assert not failures, "\n".join(
        f"  turn {f.turn} · {f.said!r}\n      {f.complaint}" for f in failures
    )


def test_the_corpus_reports_its_rates(capsys: pytest.CaptureFixture[str]) -> None:
    """The measurement, printed once so a run says what it measured rather than only whether it
    passed. Thresholds are zero on purpose: every one of these rates counts a decision this service
    makes deterministically, so a non-zero one is a defect and not a long tail."""
    report = REPORT

    lines = [
        f"conversations {report.episodes} · turns {report.turns}",
        f"wrong tool               {report.wrong_tool:>4}  {report.rate(report.wrong_tool)} %",
        f"lost context             {report.lost_context:>4}  {report.rate(report.lost_context)} %",
        f"unnecessary model calls  {report.unnecessary_clarifications:>4}"
        f"  {report.rate(report.unnecessary_clarifications)} %",
        f"unsafe routing           {report.unsafe:>4}  {report.rate(report.unsafe)} %",
    ]
    with capsys.disabled():
        print("\n  " + "\n  ".join(lines))

    assert report.wrong_tool == 0
    assert report.unsafe == 0


# --- the instrument has to be able to fail -----------------------------------------------------
#
# A check nobody has watched fail is not a check. `lost_context` read 0.0 % on a corpus written
# entirely out of ellipsis, and the honest reading of that was not "no context is lost" — it was
# that the metric could only see a workflow disappear, never a window. These three tests are the
# calibration: a right expectation passes, a wrong one is caught, and being caught counts as the
# thing it is.


def _one_turn(**expectation: object) -> episodes.Episode:
    return episodes.Episode(
        name="calibration/one_turn",
        why="the harness measuring itself",
        turns=(episodes.Turn(say="¿cuánto gasté el mes pasado?", **expectation),),  # type: ignore[arg-type]
    )


def test_the_query_a_turn_ran_can_be_asserted() -> None:
    episode = _one_turn(route="QUERY", ran={"window": "LAST_MONTH", "direction": "EXPENSE"})

    assert list(episodes.walk(episode)) == []


def test_a_wrong_expectation_about_the_query_is_caught() -> None:
    """The test that makes the other two mean something. Without it, `ran` could be silently
    ignored — a field the loader reads, nothing compares, and every episode passes."""
    episode = _one_turn(route="QUERY", ran={"window": "THIS_YEAR"})

    failures = list(episodes.walk(episode))

    assert len(failures) == 1
    assert "the query ran window='LAST_MONTH'" in failures[0].complaint


def _a_comparison(**expectation: object) -> episodes.Episode:
    return episodes.Episode(
        name="calibration/comparison",
        why="the harness measuring itself",
        turns=(episodes.Turn(say="compárame julio y junio", **expectation),),  # type: ignore[arg-type]
    )


JUNE = {"window": "2026-06-01..2026-06-30"}
JULY = {"window": "2026-07-01..2026-07-31"}


def test_both_operands_of_a_comparison_can_be_asserted() -> None:
    """One mapping only ever reaches the last query, so "compárame julio y junio" asserted July and
    said nothing about June. A list asserts every query the turn ran, in order."""
    assert list(episodes.walk(_a_comparison(route="QUERY", ran=[JUNE, JULY]))) == []


def test_a_comparison_that_ran_the_wrong_operand_is_caught() -> None:
    """The test that makes the one above mean something. Without it a comparison could drop an
    operand, run the same window twice or reverse the pair, and the corpus would agree."""
    failures = list(episodes.walk(_a_comparison(route="QUERY", ran=[JULY, JULY])))

    assert len(failures) == 1
    assert "(query 1)" in failures[0].complaint


def test_a_comparison_asserted_as_one_query_is_caught() -> None:
    """And a turn that says two and runs one — or the reverse — is a mismatch rather than a pass on
    whichever half happens to line up."""
    failures = list(episodes.walk(_a_comparison(route="QUERY", ran=[JUNE, JULY, JULY])))

    assert len(failures) == 1
    assert "ran 2 queries, and the turn expects 3" in failures[0].complaint


def test_a_turn_that_runs_a_query_in_silence_is_counted() -> None:
    """The calibration for the coverage rule. Derived from execution rather than from a literal,
    which only means anything if a turn that executes and says nothing actually shows up here."""
    report = episodes.run([_one_turn(route="QUERY")])

    assert report.unasserted == ["calibration/one_turn · turn 1 · '¿cuánto gasté el mes pasado?'"]
    assert report.failures == [], "and it is not an episode failure — the episode is fine"


def test_a_query_that_lost_its_window_is_counted_as_lost_context() -> None:
    """A wrong window *is* the metric. Counting it only when a workflow vanished is what kept the
    rate at zero while the reported defects were all abbreviated turns reaching the wrong period."""
    report = episodes.run([_one_turn(ran={"window": "THIS_YEAR"})])

    assert report.lost_context == 1


# --- Task 7: a `VISUAL` route, and four wire-visible per-turn assertions -----------------------

_TWO_GROUPS = [["RESTAURANTS", 46556, 3], ["TRANSPORT", 21000, 2]]

# The breakdown floor's canonical form, probed against the real harness: chart-wide `sort` is
# `None` and the ranking is the y axis's own sort.
_CANONICAL_BREAKDOWN_CHART = {
    "mark": "bar",
    "channels": [
        ["y", "dimension", "nominal"],
        ["x", "value_minor", "quantitative"],
        ["tooltip", "dimension", "nominal"],
        ["tooltip", "value_minor", "quantitative"],
    ],
    "axis_sort": ["y", "descending", "value_minor"],
}


def _a_chart_then(second: episodes.Turn) -> episodes.Episode:
    """A two-turn episode: the first draws a chart (two groups, so the fake actually has
    something to plot), the second is whatever the test is calibrating."""
    return episodes.Episode(
        name="calibration/after_a_chart",
        why="the harness measuring itself",
        turns=(
            episodes.Turn(say="¿cuánto gasté el mes pasado?", route="QUERY", groups=_TWO_GROUPS),
            second,
        ),
    )


def test_a_visual_outcome_is_recognised_as_its_own_route() -> None:
    """`tool_of` maps `VISUAL_UPDATED` (and its siblings) to `VISUAL` before its `QUERY`
    fallthrough. A restyle of the chart drawn above must be assertable as its own route."""
    episode = _a_chart_then(
        episodes.Turn(say="Ordena el gráfico anterior de mayor a menor.", route="VISUAL")
    )

    assert list(episodes.walk(episode)) == []


def test_a_visual_outcome_asserted_as_a_query_is_caught() -> None:
    """Without the `VISUAL` route, a `VISUAL_UPDATED` answer fell through `tool_of`'s `QUERY`
    fallthrough and was silently accepted as a query -- exactly the blindness a `route: QUERY`
    expectation on a restyle turn must now catch."""
    episode = _a_chart_then(
        episodes.Turn(say="Ordena el gráfico anterior de mayor a menor.", route="QUERY")
    )

    failures = list(episodes.walk(episode))

    assert len(failures) == 1
    assert "QUERY was the right place, VISUAL answered" in failures[0].complaint


def test_a_turns_chart_can_be_asserted() -> None:
    episode = _one_turn(route="QUERY", groups=_TWO_GROUPS, chart=_CANONICAL_BREAKDOWN_CHART)

    assert list(episodes.walk(episode)) == []


def test_a_wrong_chart_expectation_is_caught() -> None:
    """Without this, `chart:` could be silently ignored -- a field the loader reads, nothing
    compares, and every episode passes."""
    episode = _one_turn(route="QUERY", groups=_TWO_GROUPS, chart={"mark": "line"})

    failures = list(episodes.walk(episode))

    assert len(failures) == 1
    assert "the chart's mark should be 'line' and is 'bar'" in failures[0].complaint


def test_a_chart_wide_sort_the_axis_already_owns_is_read_through_the_domains_nulling() -> None:
    """`chart:` is compared through the domain's own `visual_plan.canonical(spec)`, not a second
    copy of it -- so it must inherit that function's one non-obvious rule: `present()` always lets
    an axis's own per-encoding sort win over the chart-wide `sort`/`sort_field` on the wire, so a
    chart-wide sort equal to (and shadowed by) the axis's own sort is not a live decision. Mirrors
    `test_visual_plan.py::test_canonical_ignores_a_chart_wide_sort_the_presenter_would_drop`.

    The restyle turn below produces exactly that shape: the y axis sorts itself descending by
    `value_minor`, and the spec also carries that same sort as a chart-wide `sort`/`sort_field`.
    The live value is the axis's own -- asserted as `axis_sort`, and it passes. The dead
    chart-wide `sort`/`sort_field` must read back as `None`, and asserting it as though it were
    still live -- the naive reading a harness-local `canonical()` used to validate -- must not
    silently pass: the presenter never shows it, so validating it validates nothing on the wire.
    """
    live_axis_sort_only = _a_chart_then(
        episodes.Turn(
            say="Ordena el gráfico anterior de mayor a menor.",
            route="VISUAL",
            chart={
                "sort": None,
                "sort_field": None,
                "axis_sort": ["y", "descending", "value_minor"],
            },
        )
    )
    assert list(episodes.walk(live_axis_sort_only)) == []

    dead_sort_asserted_as_live = _a_chart_then(
        episodes.Turn(
            say="Ordena el gráfico anterior de mayor a menor.",
            route="VISUAL",
            chart={"sort": "descending", "sort_field": "value_minor"},
        )
    )
    failures = list(episodes.walk(dead_sort_asserted_as_live))
    assert len(failures) == 2
    complaints = {f.complaint for f in failures}
    assert "the chart's sort should be 'descending' and is None" in complaints
    assert "the chart's sort_field should be 'value_minor' and is None" in complaints


def test_a_turns_banking_calls_can_be_asserted() -> None:
    episode = _one_turn(route="QUERY", banking_calls=1)

    assert list(episodes.walk(episode)) == []


def test_a_wrong_banking_calls_expectation_is_caught() -> None:
    episode = _one_turn(route="QUERY", banking_calls=2)

    failures = list(episodes.walk(episode))

    assert len(failures) == 1
    assert "the turn should call the ledger 2 time(s) and called it 1" in failures[0].complaint


def test_a_turns_derived_from_can_be_asserted() -> None:
    """The turn's own chart derives from nothing -- it is the first one drawn."""
    episode = _one_turn(route="QUERY", groups=_TWO_GROUPS, derived_from=False)

    assert list(episodes.walk(episode)) == []


def test_a_wrong_derived_from_expectation_is_caught() -> None:
    episode = _one_turn(route="QUERY", groups=_TWO_GROUPS, derived_from=True)

    failures = list(episodes.walk(episode))

    assert len(failures) == 1
    assert "derive from a previous one" in failures[0].complaint


def test_a_turns_groups_override_the_ledgers_answer() -> None:
    """`groups:` overrides the `Ledger` fake's one hardcoded row -- proven through the figure it
    changes, `total`, which the harness already knows how to check."""
    episode = _one_turn(route="QUERY", groups=_TWO_GROUPS, total=67_556)

    assert list(episodes.walk(episode)) == []


def test_a_wrong_total_against_the_overridden_groups_is_caught() -> None:
    """The test that makes the one above mean something. Without the override actually reaching
    the fake, this would still pass by returning the one hardcoded group's figure instead."""
    episode = _one_turn(route="QUERY", groups=_TWO_GROUPS, total=999)

    failures = list(episodes.walk(episode))

    assert len(failures) == 1
    assert "the figure should be 999 and is 67556" in failures[0].complaint
