"""Names as the model wrote them, matched against the holder's own catalogues.

Pure lookups: a name is compared with a list of names, and the sentence never reaches this module.
A name matches when, with case, accents and underscores folded away, it is an entry exactly, or
its words are a run of whole words of exactly one entry («corriente» is «Cuenta corriente»,
«Solis» is «Marta Solis Vega», but «Juan» is not «Juana Pérez»).

A name that matches nothing, or more than one entry, comes back as `Unknown` rather than dropped
or guessed. A filter silently lost answers a wider question than the one asked, with a real
figure; a guessed one answers about somebody else.

Shops are not grounded: their names are ledger data with no catalogue here, so they travel as
written and banking matches them.
"""

from collections.abc import Collection
from dataclasses import dataclass, replace

from finguard_orchestrator.domain.engine.operations import PlanFields
from finguard_orchestrator.domain.engine.state import Missing
from finguard_orchestrator.domain.models.account_summary import Holding
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.services.spanish import folded


@dataclass(frozen=True)
class Grounds:
    categories: frozenset[str]
    accounts: tuple[Holding, ...]
    contacts: tuple[Contact, ...]


@dataclass(frozen=True)
class Unknown:
    """A name the catalogues do not hold exactly once, kept as written so it can be quoted back."""

    kind: Missing
    name: str


class _UnknownNameError(Exception):
    def __init__(self, unknown: Unknown) -> None:
        super().__init__(unknown.name)
        self.unknown = unknown


def ground(fields: PlanFields, grounds: Grounds) -> PlanFields | Unknown:
    """`fields` with every catalogue name replaced by the entry it names, or the first that names
    none. Fields with no catalogue are returned untouched."""
    accounts = [holding.display_name for holding in grounds.accounts]
    people = [contact.display_name for contact in grounds.contacts]
    try:
        return replace(
            fields,
            categories=_entries(fields.categories, grounds.categories, Missing.SUBJECT),
            excluded_categories=_entries(
                fields.excluded_categories, grounds.categories, Missing.SUBJECT
            ),
            people=_entries(fields.people, people, Missing.PERSON),
            accounts=_entries(fields.accounts, accounts, Missing.ACCOUNT),
        )
    except _UnknownNameError as not_found:
        return not_found.unknown


def _entries(
    names: tuple[str, ...] | None, catalogue: Collection[str], kind: Missing
) -> tuple[str, ...] | None:
    if names is None:
        return None
    found = []
    for name in names:
        entry = _the_one(name, catalogue)
        if entry is None:
            raise _UnknownNameError(Unknown(kind, name))
        found.append(entry)
    # «hucha» and «Hucha» are one account, and a plan refuses the same account twice.
    return tuple(dict.fromkeys(found))


def _the_one(name: str, catalogue: Collection[str]) -> str | None:
    said = _words(name)
    if not said:
        return None
    exact = [entry for entry in catalogue if _words(entry) == said]
    within = [entry for entry in catalogue if _runs_through(said, _words(entry))]
    candidates = exact or within
    return candidates[0] if len(candidates) == 1 else None


def _runs_through(said: tuple[str, ...], words: tuple[str, ...]) -> bool:
    """Whether `said` is a run of whole, consecutive words of `words`. Whole words only: the
    start of a word is often somebody else's whole name («Juan» and Juana, «Luis» and Luisa)."""
    return any(words[start : start + len(said)] == said for start in range(len(words)))


def _words(name: str) -> tuple[str, ...]:
    return tuple(folded(name).replace("_", " ").split())
