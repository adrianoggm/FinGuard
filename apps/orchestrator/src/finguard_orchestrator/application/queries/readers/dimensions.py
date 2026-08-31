"""The nouns a question can be grouped or ranked by.

Categories, merchants, shops, accounts — the dimension vocabulary, spelled once. Three reader
groups read it and none of them owns it: the rankings ask which dimension to rank, `_average_plan`
asks whether a ranking is what the sentence really wanted, and `follow_up`'s reader asks whether an
ellipsis is changing the grouping.

`_GROUP_DIMENSION_NOUNS`' own comment already said why it is one name — "there is no reading of
this module where the two should differ" — about two patterns in one file. The same sentence is the
reason this is a module rather than a copy in each of the three.
"""

from __future__ import annotations

import re
from typing import Final

from finguard_orchestrator.domain.enums import (
    GroupBy,
)

# The nouns a breakdown can be asked for. Named once because `_GROUP_RANKING_COUNT` below spells
# the same list, and there is no reading of this module where the two should differ.
_GROUP_DIMENSION_NOUNS: Final = r"categorias?|comercios?|tiendas?|establecimientos?|cuentas?"


# A universally quantified noun is a *scope*, not a dimension: «en todas mis cuentas» says where to
# look and «todas las categorías» asks for a filter to disappear, and neither asks for one row each.
# Read as a dimension, "cuál fue mi mayor gasto de julio en todas mis cuentas" came back
# `group_by=ACCOUNT group_limit=5` and «La cuenta con mayor importe total … es: Cuenta corriente» —
# a ranking of accounts as the answer to a question about one charge. This recogniser runs ahead of
# the planner, so nothing downstream ever saw the superlative.
#
# The first branch exists only to be *skipped*: it consumes the scope phrase so that
# `_group_dimension_in` keeps looking for a noun that really is one. Requiring a grouping
# preposition in front of the noun instead — the shape `explicit_facts._ACCOUNT_BREAKDOWN` uses —
# cannot work here, because that pattern only reads breakdowns and this one also reads the
# rankings, which put a number or an article there: «top 3 comercios», «los tres comercios donde
# más gasté», «las cuentas con más gastos». Twelve of the sixteen ranking sentences the suite
# covers would have stopped being read. The determiner is no help either, since the scope phrase
# has one too. The quantifier is the whole difference.
_GROUP_RANKING_DIMENSION: Final = re.compile(
    rf"\btod[oa]s?\s+(?:mis|tus|sus|las|los|nuestr[oa]s)\s+(?:{_GROUP_DIMENSION_NOUNS})\b"
    rf"|\b(?P<dimension>{_GROUP_DIMENSION_NOUNS})\b"
)


def _group_dimension_in(folded: str) -> re.Match[str] | None:
    """The first noun the sentence uses as a dimension rather than as a scope.

    `finditer` rather than `search`, so a sentence that states the scope before the dimension —
    "en todas mis cuentas, top 3 comercios" — is read by its second half instead of stopping at
    the phrase the pattern above is there to discard.
    """
    for match in _GROUP_RANKING_DIMENSION.finditer(folded):
        if match.group("dimension") is not None:
            return match
    return None


def _named_group_dimension(folded: str) -> GroupBy | None:
    """Which breakdown the sentence names outright, if it names one.

    Shared by the two recognisers that need it, because they disagreed. `_group_ranking_plan` read
    "top 2 categorías por ticket medio" as a category ranking and `_average_plan` read the same
    sentence as a merchant one — it inherited the grouping of the two turns before, which were about
    comercios, since `explicit_facts` does not treat "top N categorías" as a breakdown request. Only
    the order of the cascade decided which of the two ran, and a corpus episode whose whole point is
    that "merchant/category must alter only the named dimension" was passing on that.
    """
    match = _group_dimension_in(folded)
    if match is None:
        return None
    word = match.group("dimension")
    if word.startswith("categor"):
        return GroupBy.CATEGORY
    if word.startswith(("comerc", "tienda", "establec")):
        return GroupBy.MERCHANT
    if word.startswith("cuenta"):
        return GroupBy.ACCOUNT
    return None


_GROUP_RANKING_SIGNAL: Final = re.compile(
    r"\b(?:top|ranking|mas\s+(?:gasto|importe|operaciones|movimientos|compras|pagos)|"
    r"mas\s+(?:gast|pag|compr)\w*|"
    r"mayor(?:es)?\s+(?:gasto|importe|ticket|media|promedio)|con\s+mas)\b"
)
