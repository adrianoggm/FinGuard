"""Deterministic, grounded patches for a validated financial follow-up.

This module deliberately does not try to understand a new financial question.  It only applies
closed-vocabulary facts when the wording makes continuation (or correction) explicit.  Callers can
therefore put it in front of an LLM planner without risking an accidental widening of scope. The
one widening a patch makes is deliberate and argued where it happens (`plan_patch.divisible_by`): a
breakdown asked of a plan filtered to the one value it would divide.

Three readings need the answer the turn continues, and are made only when the caller hands it over
(`read`'s `base`; spec §3.4): a follow-up that turns the question round on the person it was about
(«¿cuánto le mandé a Marta?» → «¿y ella a mí?»), because who «ella» is lives in the base; a
breakdown said as the whole turn («mejor por categoría»), because a turn that is nothing but a
grouping is a patch only on something already answered; and the period before the one answered
(«¿y la anterior?», validation fix 1), because which unit «la anterior» steps back in is the base's.
A caller without a base reads exactly what it read before any of them existed.
"""

import re
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from typing import Final

from finguard_orchestrator.domain.enums import Direction, GroupBy
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.services import (
    account_words,
    category_words,
    explicit_facts,
    spanish,
    window_words,
)
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
from finguard_orchestrator.domain.value_objects.plan_patch import PatchField, PlanPatch


class PatchStatus(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    APPLIED = "APPLIED"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class GroundedContinuation:
    status: PatchStatus
    patch: PlanPatch | None = None

    def __post_init__(self) -> None:
        if (self.status is PatchStatus.APPLIED) != (self.patch is not None):
            raise InvalidValueError("APPLIED requires a patch, and only APPLIED may carry one")

    @classmethod
    def not_applicable(cls) -> "GroundedContinuation":
        return cls(PatchStatus.NOT_APPLICABLE)

    @classmethod
    def ambiguous(cls) -> "GroundedContinuation":
        return cls(PatchStatus.AMBIGUOUS)

    @property
    def changed(self) -> frozenset[PatchField]:
        return self.patch.fields if self.patch else frozenset()


@dataclass(frozen=True)
class GroundedContinuationPatch:
    """Compatibility result for callers that still ask this module to apply a patch."""

    status: PatchStatus
    patch: PlanPatch | None = None
    plan: FinancialPlan | None = None

    @property
    def changed(self) -> frozenset[PatchField]:
        return self.patch.fields if self.patch else frozenset()


# Short anaphora and correction markers are the evidence that this is an ellipsis.  A complete
# question such as "cuanto gaste en 2025" has no such marker and must go to the normal planner.
_CONTINUATION = re.compile(
    r"^(?:y\b|e\b|pero\b|no\b|mejor\b|en realidad\b|que va\b|sin\b|excepto\b|salvo\b|quitando\b)"
    r"|\b(?:eso|esa|ese|estos|estas|aquello|aquella|lo mismo)\b"
)
_UNSUPPORTED_SCOPE = re.compile(
    r"\b(?:cuanto|cuanta|cuantos|cuantas|que|cual|donde|quien|quienes|gaste|gastado|pague|compras?)\b"
)
_LEXICAL_TOKEN = re.compile(r"[a-z0-9]+")
_WINDOW_PATCH_WORDS = {
    "abril",
    "a",
    "agosto",
    "ahora",
    "al",
    "ano",
    "anos",
    "anterior",
    "anteriores",
    "ayer",
    "categoria",
    "categorias",
    "comercio",
    "comercios",
    "completa",
    "completo",
    "cuantas",
    "cuantos",
    "cuenta",
    "cuentas",
    "de",
    "del",
    "desde",
    "dia",
    "dias",
    "diciembre",
    "durante",
    "el",
    "en",
    "enero",
    "entera",
    "entero",
    "entre",
    "esa",
    "ese",
    "esta",
    "este",
    "estos",
    "fin",
    "febrero",
    "grupo",
    "grupos",
    "hasta",
    "hoy",
    "la",
    "las",
    "llevamos",
    "lo",
    "los",
    "marzo",
    "mayo",
    "media",
    "me",
    "mejor",
    "mes",
    "meses",
    "movimiento",
    "movimientos",
    "no",
    "noviembre",
    "octubre",
    "operacion",
    "operaciones",
    "pasada",
    "pasado",
    "pasadas",
    "pasados",
    "periodo",
    "pero",
    "por",
    "promedio",
    "primera",
    "primer",
    "quincena",
    "referia",
    "realidad",
    "salvo",
    "segunda",
    "segundo",
    "semana",
    "semanas",
    "septiembre",
    "sin",
    "todo",
    "toda",
    "total",
    "trimestre",
    "ultimo",
    "ultima",
    "ultimos",
    "ultimas",
    "va",
    "y",
    "e",
    "excepto",
    "quitando",
    "junio",
    "julio",
    # Number words accepted by the window reader. Digits are accepted separately.
    "un",
    "una",
    "uno",
    "dos",
    "tres",
    "cuatro",
    "cinco",
    "seis",
    "siete",
    "ocho",
    "nueve",
    "diez",
    "once",
    "doce",
    "trece",
    "catorce",
    "quince",
    "veinte",
    "treinta",
}


def _has_unclaimed_window_token(
    folded: str,
    excluded_categories: tuple[str, ...],
    excluded_merchants: tuple[str, ...],
) -> bool:
    """Fail closed when a grounded window leaves an entity-like word unexplained."""
    claimed = set(_WINDOW_PATCH_WORDS)
    for category in excluded_categories:
        description = category_words.words_for(category) or ""
        claimed.update(_LEXICAL_TOKEN.findall(spanish.folded(description)))
        claimed.update(_LEXICAL_TOKEN.findall(spanish.folded(category_words.name_of(category))))
    for merchant in excluded_merchants:
        claimed.update(_LEXICAL_TOKEN.findall(spanish.folded(merchant)))
    return any(
        not token.isdigit() and token not in claimed for token in _LEXICAL_TOKEN.findall(folded)
    )


# The breakdowns a turn may ask of what is in view, in the words a person uses for them. «Por
# comercio» is the merchant breakdown `explicit_facts` already gives those words (`COUNTERPARTY`),
# so the two readings cannot disagree about it; «por categoría» is one `explicit_facts` does not
# state at all, because in a longer sentence the noun after «por» proves nothing on its own.
#
# «Por mes» in the singular is not here: it is also the divisor of an average («de media por mes»),
# and only `explicit_facts`, which reads the measure beside it, can tell the two apart.
_GROUPED_BY: Final = {
    "categoria": GroupBy.CATEGORY,
    "categorias": GroupBy.CATEGORY,
    "comercio": GroupBy.COUNTERPARTY,
    "comercios": GroupBy.COUNTERPARTY,
    "meses": GroupBy.MONTH,
}
_BY: Final = r"por\s+(?P<dimension>categorias?|comercios?|meses)"
_A_GROUPING: Final = re.compile(rf"\b{_BY}\b")

# A breakdown asked as the whole turn: «mejor por categoría», «¿y por comercio?», «agrupado por
# meses», a bare «por categoría». The whole turn on purpose — nothing but the grouping is said, so
# it cannot be a new question that happens to contain one.
_A_REGROUPING: Final = re.compile(
    rf"(?:(?:y|e|pero|ahora|mejor|vale|bueno)\s+)*"
    rf"(?:agrup(?:a|alo|ado|ada|ados|adas)\s+)?(?:lo\s+)?{_BY}"
)

# The question turned round on the person it was about. Each shape is the whole turn, so a sentence
# that says anything more — a window, another subject, «este año» — is not one of them, and a whole
# new question («¿cuánto me mandó Javi este año?») never reads as a patch. `who` is checked against
# the base afterwards (`_the_person`): the shape says the money turned round, the base says whose
# money it is.
#
# The verbs are the unambiguous ones, and «pasar» is one of them (validation fix 1, F4): «¿y ella a
# mí cuánto me ha pasado?» and «lo que ella me ha pasado a mí» were how people turned the money
# round on 2026-10-08, and asked the period instead. Said as the whole turn over an answer about a
# person, «me pasó» is money: what happened to the holder is a sentence these shapes do not take.
# «Me hizo» stays out — with no «bizum» beside it, nothing here could tell what was done.
#
# A correction may lead the turn — «no, lo que ella me ha pasado a mí» — and «a mí» may trail it,
# after the verb or after the name: «no, lo que me ha mandado carmen a mí». `_WHO` is lazy so that
# trailing «a mí» is never read as part of the name (it was, as a person called «carmen a mi»).
_OPENS: Final = r"(?:(?:y|e|pero|ahora|vale|bueno|no)\s+)*"
_WHAT: Final = r"(?:(?:lo\s+que|cuanto)\s+)?"
_WHO: Final = r"(?P<who>[a-z]+(?:\s+[a-z]+){0,2}?)"
_TO_ME_TOO: Final = r"(?:\s+a\s+mi)?"
_PARTICIPLES: Final = r"(?:mandado|enviado|pagado|pasado|dado|transferido|devuelto|ingresado)"
# Money that came from them: «me mandó», «me ha pagado», «me ha pasado».
_TO_ME: Final = (
    rf"me\s+(?:ha\s+{_PARTICIPLES}|mando|envio|pago|paso|dio|transfirio|devolvio|ingreso)"
)
# Money the holder sent them: «le mandé», «le he pagado», «le pasé», and «les mandé», which is
# money that went just the same (validation fix 3, fix round 2): after what Javi sent, «¿cuánto les
# mandé este año?» was not read as the money the other way, and ran Javi's.
_TO_THEM: Final = (
    rf"les?\s+(?:he\s+{_PARTICIPLES}|mande|envie|pague|pase|di|transferi|devolvi|ingrese)"
)
_TURNED_ROUND: Final = (
    # «¿y ella a mí?», «¿y Marta a mí?», and «¿y ella a mí cuánto me ha pasado?» saying it twice
    (Direction.INCOME, re.compile(rf"{_OPENS}{_WHO}\s+a\s+mi(?:\s+{_WHAT}{_TO_ME})?")),
    # «y lo que ella me mandó», «lo que ella me ha pasado a mí», and «y lo que me mandó» with the
    # person left unsaid
    (Direction.INCOME, re.compile(rf"{_OPENS}{_WHAT}(?:{_WHO}\s+)?{_TO_ME}{_TO_ME_TOO}")),
    # «y lo que me mandó ella», «¿y lo que me ha pagado Marta?», «lo que me ha mandado Carmen a mí»
    (Direction.INCOME, re.compile(rf"{_OPENS}{_WHAT}{_TO_ME}\s+{_WHO}{_TO_ME_TOO}")),
    # «y lo que recibí de ella», «lo que me llegó de Marta»
    (
        Direction.INCOME,
        re.compile(
            rf"{_OPENS}{_WHAT}(?:he\s+recibido|recibi|me\s+(?:ha\s+llegado|llego))\s+de\s+{_WHO}"
        ),
    ),
    # «¿y yo a ella?»
    (Direction.EXPENSE, re.compile(rf"{_OPENS}yo\s+a\s+{_WHO}")),
    # «y lo que le mandé», «y lo que yo le he pagado a ella»
    (
        Direction.EXPENSE,
        re.compile(rf"{_OPENS}{_WHAT}(?:yo\s+)?{_TO_THEM}(?:\s+yo)?(?:\s+a\s+{_WHO})?"),
    ),
    # «y lo que ella recibió de mí»
    (
        Direction.EXPENSE,
        re.compile(rf"{_OPENS}{_WHAT}{_WHO}\s+(?:ha\s+recibido|recibio)\s+de\s+mi"),
    ),
)
# «¿Y la anterior?», «y el anterior», «¿y la semana anterior?», «¿y el mes anterior?»: the period
# before the one in view, in its unit (validation fix 1, F6). The whole turn, like every shape here.
# With no unit said, the unit is the base's, and the article has to agree with it — «la» a week,
# «el» a month or a year — because «la anterior» after a month may be the previous question rather
# than a period. With the unit said, it has to be the base's own. The year said outright is not
# here: «¿y el año anterior?» after July is July a year back, which `window_words.a_year_earlier`
# reads.
_THE_ONE_BEFORE: Final = re.compile(
    r"(?:(?:y|e|pero|ahora|vale|bueno)\s+)*(?:(?P<article>la|el)\s+)?"
    r"(?:(?P<unit>semana|mes)\s+)?(?:anterior|previa|previo)"
)
_ARTICLE_OF: Final = {"semana": "la", "mes": "el", "ano": "el"}

# «Ella» and «él» (folded, «el») point at one person. Over a base about two, which one is a guess.
_SHE_OR_HE: Final = frozenset({"ella", "el"})
# A pause written inside a turn — «vale, mejor por categoría», «mejor, por categoría», «vale, ¿y
# ella a mí?» — leaves it the whole turn it was (M-4), so the shapes are read over its words alone.
_PAUSES: Final = re.compile(r"[,;:¿?¡!.]")


# Money between the holder and a person, said anywhere in a sentence rather than as the whole turn:
# «¿cuánto me mandó Javi este año?», «¿cuánto le mandé este año?». The verbs of `_TO_ME` and
# `_TO_THEM`; who, when said, is right after the verb, with or without «a».
_MONEY_TO_ME: Final = re.compile(rf"\b{_TO_ME}\b")
_MONEY_TO_THEM: Final = re.compile(rf"\b{_TO_THEM}\b")


def asks_about_another_person(sentence: str, base: FinancialPlan) -> bool:
    """Whether a question about money between the holder and somebody is about someone other than
    the base's person, or about the money the other way (validation fix 3, fix round 1, I-4).

    For the follow-up reader's window patch, which must not take such a question as «este año»
    said of the answer in view: after Marta, «¿cuánto me mandó Javi este año?» ran Marta's sending
    (P-7). Read as the turn-round shapes read it: «me mandó» is money that came, «le mandé» money
    that went, and the person named after the verb is matched against the base's (`_the_person`).
    So it is another person's when the money goes the other way from the base's, when the base is
    about nobody in particular, or when the name said is not the base's person. «¿Cuánto le mandé
    este año?» after Marta, or «¿cuánto me mandó este año?» after Javi's money, is still the base's
    person this year. False for any sentence that is not about money between people.
    """
    spoken = " ".join(_PAUSES.sub(" ", spanish.folded(sentence)).split())
    to_me = _MONEY_TO_ME.search(spoken)
    said = to_me or _MONEY_TO_THEM.search(spoken)
    if said is None:
        return False
    direction = Direction.INCOME if to_me is not None else Direction.EXPENSE
    if base.direction in {Direction.EXPENSE, Direction.INCOME} and direction is not base.direction:
        return True
    if not base.counterparties:
        return True
    after = spoken[said.end() :].split()
    if after[:1] == ["a"]:
        after = after[1:]
    who: list[str] = []
    for word in after[:3]:
        if word in _WINDOW_PATCH_WORDS or word in {"mi", "yo"}:
            break
        who.append(word)
    if not who:
        return False
    return not names_the_bases_person(" ".join(who), base.counterparties)


def speaks_of_money_between_people(sentence: str) -> bool:
    """Whether the sentence says money came from somebody or went to somebody — «me mandó», «le
    pagué» — anywhere in it, whoever it names (fix round 2).

    For the handler, which asks its contact directory who is named only when this says the turn is
    about a person's money at all: the directory is a call to the bank.
    """
    spoken = " ".join(_PAUSES.sub(" ", spanish.folded(sentence)).split())
    return _MONEY_TO_ME.search(spoken) is not None or _MONEY_TO_THEM.search(spoken) is not None


def names_the_bases_person(who: str, counterparties: tuple[str, ...]) -> bool:
    """Whether the name said is the base's person: as `_the_person` reads it (R33: every word
    said is a word of theirs, and only one of them has it), or the whole of the name the base has,
    said in full — «Javi Nuñez» after an answer about «Javi» (fix round 2).
    """
    said = spanish.folded(who)
    if _the_person(said, counterparties, answered_last=True) is not None:
        return True
    words = set(said.split())
    return any(set(spanish.folded(person).split()) <= words for person in counterparties)


def grouping_in(sentence: str) -> GroupBy | None:
    """The breakdown a sentence asks for in this module's grouping words, wherever it says it.

    For a reply that also does something else — «este año por comercio» answering «¿De qué
    periodo?» fills the window and asks for the breakdown — where `read` would only ever see a
    whole turn. Same words as `_A_REGROUPING`, so a breakdown a follow-up asks for and one a reply
    asks for are the same breakdown.
    """
    said = _A_GROUPING.search(spanish.folded(sentence))
    return _GROUPED_BY[said.group("dimension")] if said is not None else None


def names_the_one_before_without_its_unit(sentence: str) -> bool:
    """Whether the whole turn is «¿y la anterior?» or «y el anterior» — the period before the one
    in view, with no unit said (F6).

    Public because the handler answers these from the patch at once: no reader below reads them,
    and the shapes that do name their unit stay with the follow-up reader that answered them first.
    """
    spoken = " ".join(_PAUSES.sub(" ", spanish.folded(sentence)).split())
    before = _THE_ONE_BEFORE.fullmatch(spoken)
    return before is not None and before.group("unit") is None


def _the_one_before(
    base: FinancialPlan, spoken: str, *, today: date | None
) -> GroundedContinuation | None:
    """The period before the base's, when the turn asks for it in the base's own unit (F6).

    None when the turn is not that shape, or when the base's window is not a whole week, month or
    year that agrees with what the turn said: the turn is then read as it was before this reading.
    """
    said = _THE_ONE_BEFORE.fullmatch(spoken)
    window = base.period or base.date_range
    if said is None or today is None or window is None:
        return None
    unit = window_words.calendar_unit_of(window)
    if (
        unit is None
        or said.group("unit") not in (None, unit)
        or said.group("article") not in (None, _ARTICLE_OF[unit])
    ):
        return None
    before = window_words.window_before(window, unit, today)
    if before is None:
        return None
    return GroundedContinuation(PatchStatus.APPLIED, PlanPatch(window=before))


def _the_person(
    who: str | None, counterparties: tuple[str, ...], *, answered_last: bool
) -> str | None:
    """Which of the base's people `who` is, as the base spells them, or None when it is none.

    Unsaid, «ella» or «él»: the base's person only when it is about exactly one, and only when the
    base is the answer the conversation gave last (rulings R34/R35). A pronoun points at what was
    just said: after a contact list, or a Bizum to somebody else, «ella» is not the person of an
    answer two turns back, and reading it as her ran the wrong person's money. A name: the one
    person every word of it is a word of — «Marta» is «Marta Solis Vega», and «Marta Ruiz» is not
    «Marta», because a word the base does not have may be somebody else's surname. Javi is never
    Marta, whatever the shape around him says; and a name two of the base's people share is a
    guess about which, so it is neither.
    """
    if who is None or who in _SHE_OR_HE:
        return counterparties[0] if len(counterparties) == 1 and answered_last else None
    said = set(who.split())
    named = [person for person in counterparties if said <= set(spanish.folded(person).split())]
    return named[0] if len(named) == 1 else None


def _read_on(
    base: FinancialPlan, sentence: str, folded: str, *, today: date | None, answered_last: bool
) -> GroundedContinuation | None:
    """The two readings that need the base (module docstring), or None when the turn is neither.

    A turn shaped like a turn-round that is not about the base's person is `NOT_APPLICABLE`: it
    continues nothing here — «¿y lo que me mandó ella?» after a question about nobody in particular
    has no «ella» to find, and inventing one is the wrong figure this refuses. A direction the
    sentence states outright and the shape contradicts is `AMBIGUOUS` (ruling R24: a direction the
    turn states wins, so a reading that disagrees with it is no reading).
    """
    spoken = " ".join(_PAUSES.sub(" ", folded).split())
    regrouped = _A_REGROUPING.fullmatch(spoken)
    if regrouped is not None:
        return GroundedContinuation(
            PatchStatus.APPLIED, PlanPatch(group_by=_GROUPED_BY[regrouped.group("dimension")])
        )
    stepped_back = _the_one_before(base, spoken, today=today)
    if stepped_back is not None:
        return stepped_back
    for direction, shape in _TURNED_ROUND:
        turned = shape.fullmatch(spoken)
        if turned is None:
            continue
        person = _the_person(turned.group("who"), base.counterparties, answered_last=answered_last)
        if person is None:
            return GroundedContinuation.not_applicable()
        stated = explicit_facts.read(sentence, today=today).direction
        if stated is not None and stated is not direction:
            return GroundedContinuation.ambiguous()
        # Over an answer about several people, the one named is the one the money turned round on
        # (ruling R33); over one, the base already says who.
        narrowed = (person,) if len(base.counterparties) > 1 else ()
        return GroundedContinuation(
            PatchStatus.APPLIED, PlanPatch(direction=direction, counterparties=narrowed)
        )
    return None


def read(
    sentence: str,
    *,
    today: date | None = None,
    base: FinancialPlan | None = None,
    answered_last: bool = False,
) -> GroundedContinuation:
    """Read a closed-vocabulary continuation without consulting conversational state.

    ``AMBIGUOUS`` is returned for a continuation-shaped sentence whose recognized facts are
    insufficient or mixed with a fresh question. The caller decides whether a base exists.

    `base` is the answer the turn continues, for the two readings that need it (module docstring).
    Without one, nothing here reads either: `readers/follow_up.py` gates on whether this says
    AMBIGUOUS, and a status it relied on must not move under it.

    `answered_last` is whether `base` is the answer the conversation gave last, which only the
    caller can know (`turn_arbiter` reads it off the turn log). False unless said: a pronoun or a
    person left unsaid is resolved only against the answer just given (`_the_person`).
    """
    folded = spanish.folded(sentence).strip(" ¿?¡!.,;:")
    if base is not None:
        on_the_base = _read_on(base, sentence, folded, today=today, answered_last=answered_last)
        if on_the_base is not None:
            return on_the_base
    if not _CONTINUATION.search(folded):
        return GroundedContinuation.not_applicable()

    facts = explicit_facts.read(sentence, today=today)
    # Widening is deliberately owned by the normal merge path: enforce() is monotone and
    # cannot remove an inherited filter, so treating this as a grounded patch would silently
    # preserve a category the user explicitly asked to clear.
    if facts.widens:
        return GroundedContinuation.ambiguous()
    # A period correction may not smuggle in a new dimension. Those facts belong to the planner.
    if (
        facts.direction is not None
        or (facts.names_a_category and not facts.excluded_categories)
        or facts.asks_account_breakdown
        or facts.asks_comparison_reference
        or account_words.named_in(sentence)
        or account_words.explicitly_names_a_custom_account(sentence)
    ):
        return GroundedContinuation.ambiguous()
    if facts.window is not None and _has_unclaimed_window_token(
        folded, facts.excluded_categories, facts.excluded_merchants
    ):
        return GroundedContinuation.ambiguous()
    if facts.group_by is None and re.search(r"\bpor\s+meses\b", folded):
        facts = replace(facts, group_by=GroupBy.MONTH)
    # A bare metric question ("y cuántos movimientos") is still a closed-vocabulary
    # continuation.  Do not mistake its interrogative wording for an unsupported fresh scope.
    if (
        _UNSUPPORTED_SCOPE.search(folded)
        and facts.window is None
        and facts.group_by is None
        and facts.metric is None
    ):
        return GroundedContinuation.ambiguous()
    if not any(
        (
            facts.window,
            facts.metric,
            facts.group_by,
            facts.excluded_categories,
            facts.excluded_merchants,
        )
    ):
        return GroundedContinuation.ambiguous()
    patch = PlanPatch(
        window=facts.window,
        metric=facts.metric,
        group_by=facts.group_by,
        excluded_categories=facts.excluded_categories,
        excluded_merchants=facts.excluded_merchants,
    )
    return GroundedContinuation(PatchStatus.APPLIED, patch)


def apply(
    sentence: str,
    previous: FinancialPlan | None,
    *,
    answered_last: bool,
    today: date | None = None,
) -> GroundedContinuationPatch:
    """Compatibility wrapper around ``read`` and the immutable patch operation.

    `answered_last` is `read`'s (ruling R34): whether `previous` is the answer the conversation
    gave last. Required, with no default (final review, M-5): the wrapper used to say yes for every
    caller, so «ella» was resolved to the person of a plan an aside had already followed.
    """
    if previous is None:
        return GroundedContinuationPatch(PatchStatus.NOT_APPLICABLE)
    continuation = read(sentence, today=today, base=previous, answered_last=answered_last)
    plan = continuation.patch.apply_to(previous) if continuation.patch is not None else None
    return GroundedContinuationPatch(continuation.status, continuation.patch, plan)
