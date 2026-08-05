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


# Where the corpus is, and where it is going. The floor is asserted so a glob that matches nothing
# cannot pass by finding no work; the target is stated so nobody has to guess whether 40 was the
# plan. Raise the floor as files land — never lower it.
EPISODES_TODAY = 40
TARGET = 130


def test_the_corpus_has_not_shrunk() -> None:
    assert len(CORPUS) >= EPISODES_TODAY, f"only {len(CORPUS)} episodes were loaded"


def test_every_episode_is_long_enough_to_be_a_conversation() -> None:
    """Six turns is the floor the review set, and it is the right one: below it an episode is a
    pair of sentences, and the defects this corpus exists for live further in than that."""
    short = [(e.name, len(e.turns)) for e in CORPUS if len(e.turns) < 6]

    assert not short, f"episodes shorter than six turns: {short}"


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
