"""Ground fields backed by canonical catalogues; leave historical counterparties as free text.

Categories and accounts are closed catalogues. People in a financial query are ledger
counterparties, not saved payees, so banking matches them against movement data. Payment
preparation resolves recipients against the holder's saved contacts separately.
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
    """A name the catalogues do not hold exactly once, kept as written so it can be quoted back.
    `candidates` are the entries it could be, in catalogue order; empty when it matches none."""

    kind: Missing
    name: str
    candidates: tuple[str, ...] = ()


class _UnknownNameError(Exception):
    def __init__(self, unknown: Unknown) -> None:
        super().__init__(unknown.name)
        self.unknown = unknown


def ground(fields: PlanFields, grounds: Grounds) -> PlanFields | Unknown:
    """`fields` with every catalogue name replaced by the entry it names, or the first that names
    none. Fields with no catalogue are returned untouched."""
    accounts = [holding.display_name for holding in grounds.accounts]
    # Sorted, so the candidates of an ambiguous name come back in the same order on every run.
    categories = sorted(grounds.categories)
    try:
        return replace(
            fields,
            categories=_entries(fields.categories, categories, Missing.SUBJECT),
            excluded_categories=_entries(fields.excluded_categories, categories, Missing.SUBJECT),
            # People are ledger counterparties, not saved payees. The banking query matches their
            # free-text names against movements; only payment preparation resolves contacts.
            people=fields.people,
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
        candidates = _candidates(name, catalogue)
        if len(candidates) != 1:
            raise _UnknownNameError(Unknown(kind, name, candidates))
        found.append(candidates[0])
    # «hucha» and «Hucha» are one account, and a plan refuses the same account twice.
    return tuple(dict.fromkeys(found))


def _candidates(name: str, catalogue: Collection[str]) -> tuple[str, ...]:
    """The entries `name` is exactly, or else those it is a run of whole words of."""
    said = _words(name)
    if not said:
        return ()
    exact = tuple(entry for entry in catalogue if _words(entry) == said)
    return exact or tuple(entry for entry in catalogue if _runs_through(said, _words(entry)))


def _runs_through(said: tuple[str, ...], words: tuple[str, ...]) -> bool:
    """Whether `said` is a run of whole, consecutive words of `words`. Whole words only: the
    start of a word is often somebody else's whole name («Juan» and Juana, «Luis» and Luisa)."""
    return any(words[start : start + len(said)] == said for start in range(len(words)))


def _words(name: str) -> tuple[str, ...]:
    return tuple(folded(name).replace("_", " ").split())
