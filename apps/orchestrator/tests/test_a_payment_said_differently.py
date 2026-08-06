"""How far a payment instruction can drift before the slots come out wrong.

The third layer to get this treatment, after dispatch and planning, and the one where being wrong
costs money rather than credibility. Same method throughout: a model that has never seen the
patterns writes eight ways of saying one thing, and the layer is scored on whether all eight land
in the same place. See evaluation/payments/drift.yaml for where the sentences came from.

It stands at 20 of 40, and the split is the finding rather than the rate. **The amount is right
forty times out of forty** — `amount_in` reads Spanish money properly, including "12,50" and the
phone numbers it has to refuse. Every one of the twenty failures is the note, and the note is worse
to get wrong than a figure: it is the user's own text, quoted back in the confirmation and then
written to the bank. "Con el concepto cena por bizum ahora mismo" sends six words the user never
meant as a note; "viaje a Málaga" arrives as "viaje".

Not asserted per sentence. Like the dispatch corpus this states what the layer does rather than
what it must do, so the twenty are printed and one floor stops the rate regressing quietly while
somebody improves something else. When the concept reader is fixed, the floor moves up and this
file is how the fix is judged.
"""

from pathlib import Path

import yaml

from finguard_orchestrator.domain.services import payment_words

CORPUS = Path(__file__).resolve().parents[1] / "evaluation" / "payments" / "drift.yaml"

# Where it stood when the file was written: 20 of 40 instructions yield both the amount and the
# note the sentence names. Raised whenever a change earns it, never lowered to make one pass.
FLOOR = 20


def _cases() -> list[tuple[str, int, str | None, str]]:
    loaded = yaml.safe_load(CORPUS.read_text(encoding="utf-8"))
    return [
        (group["means"], group["minor"], group["concept"], said)
        for group in loaded["groups"]
        for said in group["says"]
    ]


CASES = _cases()


def test_the_corpus_loaded() -> None:
    """A file nobody read is a measurement nobody made."""
    assert len(CASES) == 40


def test_the_amount_survives_every_phrasing() -> None:
    """Asserted rather than measured, because it holds: this is the part of the layer that works.

    Kept as a test and not a comment so that a change to `_AMOUNT` — chasing the concept, most
    likely, since they live in the same file — cannot quietly take the figure down with it.
    """
    wrong = [
        (said, payment_words.amount_in(said))
        for _means, minor, _concept, said in CASES
        if payment_words.amount_in(said) != minor
    ]

    assert not wrong, f"the amount is no longer read from every phrasing: {wrong}"


def test_the_rate_only_goes_up(capsys) -> None:  # noqa: ANN001
    """Printed as well as asserted: the list is the useful output, the rate is the summary."""
    failures = [
        (said, payment_words.concept_in(said), concept)
        for _means, _minor, concept, said in CASES
        if payment_words.concept_in(said) != concept
    ]
    reached = len(CASES) - len(failures)
    with capsys.disabled():
        print(f"\n{reached}/{len(CASES)} payment instructions yield the slots they name")
        for said, got, want in failures:
            print(f"  {want!r} read as {got!r}\n      «{said}»")

    assert reached >= FLOOR, f"{reached}/{len(CASES)} read correctly, below the floor of {FLOOR}"
