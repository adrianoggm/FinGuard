"""The rule that decides whether a generated sentence is allowed to become a test case.

A generated corpus nobody validated is a benchmark that measures the generator. This module is
the validation, and it is the load-bearing part of `generate_paraphrases.py` — everything else
there is plumbing around an HTTP call.

**The rule is fixed before generation and applied before the planner has answered anything.**
That is `drift.yaml`'s discipline, quoted from its own header — "a rule fixed before anything was
run and applied before the planner had answered anything: the time expression has to survive" —
and it is the only thing that stops a filter being tuned until the score looks good. Nothing here
imports the planner, the recognisers or the prompt, and nothing here has ever seen a score.

--- what it checks, and why it is not circular ---------------------------------------------------

The obvious way to check that a paraphrase still means the same thing is to run it through
`explicit_facts.read()` and compare. That is exactly wrong: the readers are what is under test, so
a filter built out of them would delete precisely the sentences the readers cannot see — the ones
worth measuring — and leave a corpus that scores 100 % by construction.

So this module carries **its own Spanish vocabulary**, written here, deliberately broader than any
single recogniser, and it never asks the system anything. What it produces is a `Surface`: the
meaning-bearing markers a sentence carries, in the dataset's own vocabulary.

The check is then one line of principle: **the paraphrase's surface must equal the base
sentence's.** Not "the paraphrase's surface must match the plan" — equality with the base is what
makes the rule per-case-free and what makes it symmetric, so a vague sentence must stay vague and
a dated one must keep its dates. A sentence that gains «el mes pasado», loses «entre el 1 y el 15
de junio», or turns a spending question into an income one has changed the question and cannot be
a test of robustness against rewording.

--- the three controls that stop this being decorative --------------------------------------------

1. **Admission.** Before a base is used, `admission_problem` checks that this module's reading of
   the base *sentence* agrees with the plan the *dataset* declares for it. If the vocabulary here
   cannot see the period the dataset says the sentence determines, the vocabulary is too narrow and
   the base is dropped rather than judged. A validator that cannot read the known-good sentences
   has no business rejecting the unknown ones.

2. **Discrimination.** `surface_conflict` is what the corpus test uses to show this rule has
   teeth: an accepted paraphrase of one base must be *rejected* against most other bases. A
   validator that accepts everything against everything validates nothing, and that is measurable
   rather than assertable.

3. **Recording.** Every rejection carries a reason string, and `generate_paraphrases.py` writes the
   rejected sentences into the corpus file beside the accepted ones. A corpus that loses its
   inconvenient cases is the same bias wearing a different hat.

--- the judgement call in the generator's own prompt ----------------------------------------------

`drift.yaml` says its generator was told "only what the customer wants -- never a word about
periods, categories, directions or what this planner can express". A paraphrase task cannot be that
blind: the model is shown the sentence. What it is still never shown is the prompt, the schema, the
enum values or which phrasings this system already handles. Telling a paraphraser "do not change
the period, the subject or the direction of the money" is the definition of the task, not a leak of
the vocabulary — and the near-duplicate rule below is what stops the model discharging it by
copying.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

# --- normalisation --------------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9]+")


def fold(text: str) -> str:
    """Lowercase, unaccented, single-spaced. Every pattern below is written against this.

    `noise_no_accents` and `noise_uppercase` are dataset cases, so a rule that only matched
    accented lowercase would drop two bases and never say why.
    """
    stripped = unicodedata.normalize("NFD", text.lower())
    without = "".join(ch for ch in stripped if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", without.replace("¿", " ").replace("?", " ")).strip()


def tokens(text: str) -> frozenset[str]:
    return frozenset(_WORD.findall(fold(text)))


def overlap(left: str, right: str) -> float:
    """Jaccard over folded word tokens. 1.0 is the same words in any order."""
    a, b = tokens(left), tokens(right)
    return len(a & b) / len(a | b) if a | b else 1.0


# --- the vocabulary -------------------------------------------------------------------------
#
# Written here rather than imported, for the reason in the module docstring. Deliberately broader
# than any recogniser: a marker this table misses becomes a rejection, and a rejection is only ever
# a case not measured — never a false pass. Being generous costs coverage; being narrow would cost
# honesty, because it would silently delete the hard sentences.

_NUMBER_WORDS = {
    "un": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
    "quince": 15, "veinte": 20, "treinta": 30, "sesenta": 60, "noventa": 90,
}  # fmt: skip

MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}  # fmt: skip

# A determinate relative period: the phrase names one window and a reader could resolve it.
_PERIODS: tuple[tuple[str, str], ...] = (
    ("THIS_MONTH", r"\beste mes\b|\bmes actual\b|\bmes en curso\b|\blo que va de mes\b"),
    ("THIS_MONTH", r"\bmes corriente\b|\bdel mes en curso\b|\ben lo que llevamos de mes\b"),
    ("THIS_MONTH", r"\bdesde (?:el )?(?:comienzo|inicio|principio|principios) de(?:l)? mes\b"),
    ("THIS_MONTH", r"\bdesde que (?:empezo|comenzo) el mes\b|\bmes hasta (?:ahora|la fecha|hoy)\b"),
    ("LAST_MONTH", r"\bmes pasado\b|\bmes anterior\b|\bpasado mes\b|\bmes previo\b"),
    ("LAST_MONTH", r"\bmes que (?:acaba de terminar|ha terminado|termino|paso)\b|\bmes recien"),
    ("LAST_MONTH", r"\bmes completo anterior\b|\bultimo mes completo\b|\bmes ya cerrado\b"),
    ("LAST_MONTH", r"\bmes precedente\b|\bmes de antes\b|\bmes previo al actual\b"),
    ("THIS_WEEK", r"\besta semana\b|\bsemana actual\b|\bsemana en curso\b|\blo que va de semana\b"),
    ("LAST_WEEK", r"\bsemana pasada\b|\bsemana anterior\b|\bpasada semana\b"),
    ("THIS_YEAR", r"\beste ano\b|\bano actual\b|\bano en curso\b|\blo que va de ano\b"),
    ("THIS_YEAR", r"\bdesde enero\b|\ben lo que llevamos de ano\b|\bdesde primeros de ano\b"),
    ("THIS_YEAR", r"\bdesde (?:el )?(?:principio|principios|comienzo|inicio) de(?:l)? ano\b"),
    ("TODAY", r"\bhoy\b|\bdia de hoy\b|\ben el dia de hoy\b"),
    ("YESTERDAY", r"\bayer\b"),
    ("LAST_N_DAYS:1", r"\bultimo dia\b|\bultimas 24 horas\b|\bultimas veinticuatro horas\b"),
)

# Time words that name no window at all. The `ambiguity` group is built out of them, so they are
# tracked rather than ignored: a vague base whose paraphrase drops them has been disambiguated,
# and a dated base whose paraphrase acquires one has been blurred. Both are meaning changes.
_VAGUE = (
    r"\bultimamente\b|\brecientemente\b|\bhace poco\b|\bestos dias\b|\bultimos tiempos\b"
    r"|\bultimo mes\b|\bultimos meses\b|\bultimos dias\b|\bmas o menos\b|\bultima semana\b"
    r"|\breciente\b|\bde momento\b|\bahora mismo\b|\bese mes\b|\baquel mes\b|\bpor ahora\b"
    r"|\bdesde navidad\b|\bdesde hace tiempo\b|\bultimo periodo\b|\bultimo tiempo\b"
)

_LAST_N_DAYS = re.compile(
    r"\b(?:ultimos|pasados|previos)\s+(\w+)\s+dias\b"
    r"|\b(\w+)\s+(?:ultimos|pasados)\s+dias\b"
    r"|\bprimeros\s+(\w+)\s+dias\b"
)
_LAST_N_MONTHS = re.compile(
    r"\b(?:ultimos|pasados|previos)\s+(\w+)\s+meses\b|\b(\w+)\s+(?:ultimos|pasados)\s+meses\b"
)
_LAST_N_WEEKS = re.compile(
    r"\b(?:ultimas|pasadas|previas)\s+(\w+)\s+semanas\b|\b(\w+)\s+(?:ultimas|pasadas)\s+semanas\b"
)

_DIRECTIONS: tuple[tuple[str, str], ...] = (
    (
        "EXPENSE",
        r"\bgast|\bgaste\b|\bpagu|\bpagad|\bpago\b|\bpagos\b|\bdesembols|\bcompr"
        r"|\bme he dejado\b|\bse me (?:ha ido|fue|va)\b|\bsalido de mi cuenta\b"
        r"|\bha salido\b|\bsalidas\b|\bcargos?\b|\bcobrar|\bme cobraron\b|\bconsum"
        # «¿Cuánto llevo este mes?» is a spending question with no spending verb in it. The
        # admission check named this one: three bases were unreadable without it.
        r"|\bllevo\b|\bllevamos\b|\bme he fundido\b|\bme he pulido\b|\bfactura",
    ),
    (
        "INCOME",
        r"\bingres|\bcobr(?:e|ado|o)\b|\bme (?:ha )?entr(?:ado|o)\b|\bentradas\b|\brecib"
        r"|\bme han pagado\b|\bnomina\b|\bha entrado\b|\bhe ganado\b|\bganancias\b"
        r"|\blo que me entra\b|\bme han ingresado\b|\bque me llega\b",
    ),
)

_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("GROCERIES", r"\bsuper\b|\bsupermercado|\bla compra\b|\bcompra del super\b|\bmercadona\b"),
    ("GROCERIES", r"\balimentacion\b|\bcomida del super\b|\bcompra semanal\b"),
    ("TRANSPORT_FUEL", r"\bgasolin|\bcombustible|\brepostaje|\bdiesel\b|\bgasoil\b|\bcarburante"),
    ("RESTAURANTS", r"\brestaurant|\bbares?\b|\bcomer fuera\b|\bcomiendo fuera\b|\bcafeteria"),
    ("RESTAURANTS", r"\bcenas? fuera\b|\bfuera de casa\b|\bhosteleria\b"),
    ("TRANSPORT_PUBLIC", r"\btransporte\b|\bmetro\b|\bautobus|\bbus\b|\bcercanias\b"),
    ("TRANSPORT_PUBLIC", r"\btren\b|\babono transporte\b|\bbillete de (?:metro|bus|tren)\b"),
    ("HEALTH", r"\bfarmaci|\bsalud\b|\bmedic|\bdentista\b|\bmedicament"),
    ("LEISURE", r"\bocio\b|\bdiversion\b|\btiempo libre\b|\bcine\b|\bespectaculos\b"),
    ("SUBSCRIPTIONS", r"\bsuscripcion|\bsubscripcion|\bcuotas mensuales\b|\bplataformas\b"),
    ("HOUSING_RENT", r"\balquiler\b|\brenta del piso\b|\bcasa alquilada\b|\bel piso\b"),
    ("SALARY", r"\bnomina\b|\bsueldo\b|\bsalario\b"),
)

_METRICS: tuple[tuple[str, str], ...] = (
    ("COUNT", r"\bcuantas veces\b|\bnumero de\b|\bcuantos (?:movimientos|cargos|pagos|gastos)\b"),
    ("COUNT", r"\bcantidad de (?:movimientos|operaciones|compras|veces)\b|\bfrecuencia\b"),
    ("AVERAGE", r"\bmedia\b|\bpromedio\b|\bde media\b|\bpor termino medio\b|\bmedio por\b"),
)

# What the question is *about*, where that is not a sum of movements at all. Added after reading
# the first corpus: «¿Cómo voy de dinero?» is a vague question this system refuses, and all twelve
# rewrites of it came back as «¿cuál es mi saldo?», which is a different question with a definite
# answer. Nothing else in the table could see that, because a balance names no period, no
# direction and no category.
_TOPICS: tuple[tuple[str, str], ...] = (
    ("BALANCE", r"\bsaldo\b|\bbalance\b|\bcuanto (?:dinero )?tengo\b|\bdinero disponible\b"),
    ("BALANCE", r"\bfondos\b|\bestado de (?:mi|la) cuenta\b|\bdisponible en (?:mi|la) cuenta\b"),
)

_GROUPINGS: tuple[tuple[str, str], ...] = (
    ("CATEGORY", r"\bpor categoria|\bdesglos|\bpor tipos?\b|\bpor conceptos?\b|\brepartid"),
    ("CATEGORY", r"\ben que (?:lo |me lo )?(?:he )?gast|\bdetallado por\b"),
    ("MERCHANT", r"\bpor comercios?\b|\bpor tiendas?\b|\bpor establecimientos?\b"),
    ("MERCHANT", r"\bque comercio\b|\bdonde (?:mas )?(?:he )?gast|\bque tienda\b|\bque negocio\b"),
)

# The failure mode `paraphrases/dispatch.yaml` recorded in prose: a model asked to rewrite a
# question about the speaker's money wrote a question to the assistant about the assistant's.
# Only the second person is rejected. A positive first-person *requirement* was tried and thrown
# away: it rejected «Quiero conocer el detalle de los gastos de junio», which is a perfectly good
# rewrite, and the failure it was meant to catch is second-person by definition.
_SECOND_PERSON = re.compile(r"\btu\b|\btus\b|\btuyo\b|\btuya\b|\bcontigo\b|\bvuestro\b")

# The same failure with a noun instead of a pronoun, found by reading the first corpus:
# «¿Cuál es el precio que debe pagar el cliente?» and «¿cuánto cobra el banco por el gimnasio?»
# are questions about somebody else's money, and no rule about periods or categories can see it.
_THIRD_PARTY = re.compile(
    r"\bel cliente\b|\bel usuario\b|\bel titular\b|\bla persona\b|\bel banco\b|\bla entidad\b"
    r"|\bel señor\b|\bla señora\b|\bel se[nñ]or\b|\bcobra el banco\b"
)

# A cheap wrong-language detector. `dispatch.yaml` records a batch where a local model answered in
# the wrong language. Two hits are required and every token here is English-only: the first version
# of this list contained `total`, which is also Spanish, and it rejected nine good sentences in the
# first thirty-two before a single one reached the planner.
_ENGLISH = re.compile(
    r"\b(?:the|how|spent|money|please|account|show|what|did|i|you|last|month|week|from)\b"
)

# Things a model emits when it is talking about the task instead of doing it.
_ARTEFACT = re.compile(r"[\[\]{}<>`\"]|\bvariant\b|\bplaceholder\b|_\d|\bjson\b|\bparafrasis\b")


# --- the surface a sentence carries -----------------------------------------------------------


@dataclass(frozen=True)
class Surface:
    """The meaning-bearing markers one sentence carries, in the dataset's own vocabulary.

    Frozen and comparable on purpose: the whole meaning-preservation rule is `==` between the
    base's surface and the candidate's.
    """

    periods: frozenset[str] = frozenset()
    vague: bool = False
    dates: frozenset[str] = frozenset()
    directions: frozenset[str] = frozenset()
    categories: frozenset[str] = frozenset()
    metrics: frozenset[str] = frozenset()
    groupings: frozenset[str] = frozenset()
    topics: frozenset[str] = frozenset()

    def differences(self, other: Surface) -> list[str]:
        out: list[str] = []
        for name in (
            "periods",
            "dates",
            "directions",
            "categories",
            "metrics",
            "groupings",
            "topics",
        ):
            mine = getattr(self, name)
            theirs = getattr(other, name)
            if mine != theirs:
                out.append(f"{name} {sorted(mine)} -> {sorted(theirs)}")
        if self.vague != other.vague:
            out.append(f"vague {self.vague} -> {other.vague}")
        return out


def _count(word: str) -> int | None:
    if word.isdigit():
        return int(word)
    return _NUMBER_WORDS.get(word)


def _n_periods(folded: str) -> tuple[set[str], str]:
    """Windows counted in days, weeks or months, and the sentence with those spans removed.

    The remainder is what the vagueness test then runs on, and the admission check is why: «en los
    dos últimos meses» is a determinate two-month window that happens to contain the letters of
    «últimos meses», and reading it as vague made three readable bases unreadable.
    """
    found: set[str] = set()
    rest = folded
    for pattern, unit, scale in (
        (_LAST_N_DAYS, "LAST_N_DAYS", 1),
        (_LAST_N_WEEKS, "LAST_N_DAYS", 7),
        (_LAST_N_MONTHS, "LAST_N_MONTHS", 1),
    ):
        for match in pattern.finditer(folded):
            word = next((g for g in match.groups() if g), None)
            n = _count(word) if word else None
            if n is not None:
                found.add(f"{unit}:{n * scale}")
                rest = rest.replace(match.group(0), " ")
    return found, rest


def _dates(folded: str) -> set[str]:
    """Absolute date tokens: a month name, a bare year, and any day number beside a month.

    The `period_absolute` family is where «entre el 10 y el 20 de mayo» was lost, so this is
    deliberately literal: the numbers and the month name have to come out the other side.
    """
    found = {name for name in MONTHS if re.search(rf"\b{name}\b", folded)}
    found |= {y for y in re.findall(r"\b(?:19|20)\d\d\b", folded)}
    if found:
        found |= {f"d{day}" for day in re.findall(r"\b([1-9]|[12]\d|3[01])\b", folded)}
    if re.search(r"\bprimer trimestre\b", folded):
        found.add("Q1")
    return found


def _matches(folded: str, table: tuple[tuple[str, str], ...]) -> frozenset[str]:
    return frozenset(value for value, pattern in table if re.search(pattern, folded))


def read_surface(sentence: str) -> Surface:
    """Everything this module claims to be able to see in a sentence.

    Vagueness is read *last*, on what is left after every determinate window has been cut out.
    «el último mes completo» and «en los dos últimos meses» both name one window and both contain
    the letters of a vague phrase; scoring them as vague made four readable bases unreadable.
    """
    folded = fold(sentence)
    counted, rest = _n_periods(folded)
    named = set()
    for value, pattern in _PERIODS:
        for match in re.finditer(pattern, rest):
            named.add(value)
            rest = rest.replace(match.group(0), " ")
    return Surface(
        periods=frozenset(named | counted),
        vague=bool(re.search(_VAGUE, rest)),
        dates=frozenset(_dates(folded)),
        directions=_matches(folded, _DIRECTIONS),
        categories=_matches(folded, _CATEGORIES),
        metrics=_matches(folded, _METRICS),
        groupings=_matches(folded, _GROUPINGS),
        topics=_matches(folded, _TOPICS),
    )


# --- admission: does this module's reading agree with what the dataset declares? ---------------


def declared(case: dict[str, Any]) -> Surface:
    """The surface the dataset's own plan says the sentence determines."""
    plan = case.get("plan") or {}
    periods: set[str] = set()
    period = plan.get("period")
    if isinstance(period, dict):
        name = str(period["period"])
        periods.add(f"{name}:{period['n']}" if period.get("n") is not None else name)
    metric = plan.get("metric")
    grouping = plan.get("groupBy")
    return Surface(
        periods=frozenset(periods),
        directions=frozenset({str(plan["direction"])} if plan.get("direction") in _NAMED else ()),
        categories=frozenset(str(c) for c in plan.get("categories", ())),
        metrics=frozenset({str(metric)} if metric in ("COUNT", "AVERAGE") else ()),
        groupings=frozenset({str(grouping)} if grouping in ("CATEGORY", "MERCHANT") else ()),
    )


def declared_continuation(case: dict[str, Any]) -> Surface:
    """Only the markers the last turn changes relative to its declared preceding plan."""
    wanted = declared(case)
    previous = declared({"plan": case.get("previous") or {}})
    changed: dict[str, Any] = {}
    for name in (
        "periods",
        "vague",
        "dates",
        "directions",
        "categories",
        "metrics",
        "groupings",
        "topics",
    ):
        before, after = getattr(previous, name), getattr(wanted, name)
        changed[name] = after if before != after else (False if name == "vague" else frozenset())
    return Surface(**changed)


_NAMED = ("EXPENSE", "INCOME")

# «¿Y en gasolina?», «¿Y este mes?», «¿Y cuántas veces?». A continuation carries most of its meaning
# in the turn before it, and the model writing these rewrites has never seen that turn.
_CONTINUATION = re.compile(r"^y[\s,]")


def admission_problem(case: dict[str, Any], *, preceding_question: str | None = None) -> str | None:
    """Why this base cannot be paraphrased under these rules, or None.

    This is control 1 from the module docstring, and it runs before a single sentence is
    generated. A base this vocabulary cannot read is dropped rather than judged, because a
    validator that misreads the known-good sentence will misjudge every rewrite of it.

    Cases whose expectation is not a plan declare nothing to agree with, so only the shape rules
    apply to them: `¿Cuánto?` and `...` are the sentences this exists to keep apart.

    A continuation is admitted only with a human-written preceding question. The absence of that
    question remains a hard rejection: reading `previous`, or rendering its enums into Spanish,
    would leak the planning schema into the supposedly independent generator.
    """
    question = str(case["question"])
    if len(_WORD.findall(fold(question))) < 2:
        return "the sentence carries fewer than two words to rewrite"
    if _CONTINUATION.match(fold(question)) and preceding_question is None:
        return "a continuation cannot be rewritten by somebody who has not seen the turn before it"
    if case.get("expect") != "plan":
        return None
    plan = case.get("plan") or {}
    want = declared_continuation(case) if preceding_question is not None else declared(case)
    saw = read_surface(question)
    if plan.get("dateRange") is not None:
        return None if saw.dates else "the vocabulary reads no absolute date in a dated case"
    problems = want.differences(saw)
    # A sentence may legitimately say more than its plan does — `noise_rambling` is a plan of two
    # fields inside a paragraph. What is not allowed is the vocabulary failing to see what the
    # dataset says the sentence determines.
    missing = [line for line in problems if "] -> []" in line or "vague False -> True" in line]
    return f"the vocabulary cannot read {'; '.join(missing)}" if missing else None


# --- the rule ---------------------------------------------------------------------------------

#: A rewrite that shares this much of its words with the base is a copy, not a paraphrase, and
#: measures nothing. Chosen before generation and never moved: the honest lever on this number is
#: the generator's prompt.
NEAR_DUPLICATE = 0.85

#: The generated sentence has to stay a sentence somebody would type into a bank app.
MIN_CHARS, MAX_CHARS = 8, 200


def shape_problem(sentence: str) -> str | None:
    """Rules that need no base: is this Spanish, about my own money, and free of model debris?"""
    if not MIN_CHARS <= len(sentence.strip()) <= MAX_CHARS:
        return f"length {len(sentence.strip())} outside {MIN_CHARS}..{MAX_CHARS}"
    if "\n" in sentence.strip():
        return "more than one line"
    if _ARTEFACT.search(fold(sentence)) or _ARTEFACT.search(sentence):
        return "carries model debris rather than a sentence"
    folded = fold(sentence)
    if len(set(_ENGLISH.findall(folded))) >= 2:
        return "English words"
    if _SECOND_PERSON.search(folded):
        return "asks the assistant about the assistant"
    if _THIRD_PARTY.search(folded):
        return "asks about somebody else's money"
    return None


def surface_conflict(base_question: str, candidate: str) -> str | None:
    """The meaning rule on its own, exposed so the corpus test can measure its discrimination."""
    differences = read_surface(base_question).differences(read_surface(candidate))
    return "; ".join(differences) if differences else None


def rejection(base_question: str, candidate: str, accepted: tuple[str, ...]) -> str | None:
    """Why this generated sentence cannot join the corpus, or None if it may.

    Order is deliberate and the reasons are recorded verbatim in the corpus file, so the reject
    pile can be read by a Spanish speaker who was not here when it was written.
    """
    problem = shape_problem(candidate)
    if problem is not None:
        return problem
    if overlap(base_question, candidate) >= NEAR_DUPLICATE:
        return f"a copy of the base ({overlap(base_question, candidate):.2f} of its words)"
    for sibling in accepted:
        if overlap(sibling, candidate) >= NEAR_DUPLICATE:
            return f"a copy of «{sibling}»"
    conflict = surface_conflict(base_question, candidate)
    return f"changed the question: {conflict}" if conflict else None
