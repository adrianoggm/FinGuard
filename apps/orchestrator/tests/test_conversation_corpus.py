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

CORPUS = episodes.load()


# A floor rather than an equality, so adding an episode is never a failing build — and a floor at
# all, so a glob that matches nothing cannot pass by finding no work. Raise it as files land, never
# lower it.
FLOOR = 130


def test_the_corpus_has_not_shrunk() -> None:
    assert len(CORPUS) >= FLOOR, f"only {len(CORPUS)} episodes were loaded"


def test_every_episode_is_long_enough_to_be_a_conversation() -> None:
    """Six turns is the floor the review set, and it is the right one: below it an episode is a
    pair of sentences, and the defects this corpus exists for live further in than that."""
    short = [(e.name, len(e.turns)) for e in CORPUS if len(e.turns) < 6]

    assert not short, f"episodes shorter than six turns: {short}"


def test_the_corpus_asserts_the_queries_it_runs() -> None:
    """A floor on `ran`, for the same reason `FLOOR` exists: a check nothing uses passes by finding
    no work. Every expectation could be deleted and the corpus would still be green, so the number
    of turns that state which query ran is itself measured. Raise it as episodes gain them.

    Fields as well as turns. A turn asserting one field and a turn asserting five both count once,
    and the second is four times the check — counting only turns would let coverage be diluted
    while the number went up.
    """
    stated = [turn.ran for episode in CORPUS for turn in episode.turns if turn.ran]

    assert len(stated) >= 56, f"only {len(stated)} turns say which query they ran"
    assert sum(len(ran) for ran in stated) >= 164, "the assertions got thinner"


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
    report = episodes.run(CORPUS)

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


def test_a_query_that_lost_its_window_is_counted_as_lost_context() -> None:
    """A wrong window *is* the metric. Counting it only when a workflow vanished is what kept the
    rate at zero while the reported defects were all abbreviated turns reaching the wrong period."""
    report = episodes.run([_one_turn(ran={"window": "THIS_YEAR"})])

    assert report.lost_context == 1
