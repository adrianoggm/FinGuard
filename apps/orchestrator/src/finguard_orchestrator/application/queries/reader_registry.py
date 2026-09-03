"""The deterministic readers as a table, and the order derived from it.

There are nineteen `@_recogniser` readers, each `f(query) -> plan | None`, and until A1 the only
thing that said which one gets a sentence was **where its call sat in a 7,975-line file**. Position
was load-bearing and nothing could read it: adding the twentieth meant choosing a line number, and
the one property anybody actually wanted — *two readers must not both claim a sentence* — was
discovered when a corpus turn went wrong.

This module is the machinery for saying it instead. It holds no readers and no Spanish: the table is
assembled in `readers/__init__`, out of rows each reader module declares beside the reader it names,
because a registry that had to import them would import the modules that import it.

**What a row declares, and why each field is here.**

`when` is a **precondition**: what must hold of the turn for the reader to be worth asking. It is a
*necessary* condition and never a sufficient one — the reader still decides. That asymmetry is the
whole safety argument and it is worth being explicit about:

- A precondition that is **too weak** costs a call that returns `None`. Nothing else.
- A precondition that is **too strong** silently steals a sentence from its reader, and that is
  caught by `test_no_precondition_refuses_a_sentence_its_reader_would_claim` — every reader against
  every sentence we have, rather than only the reader that happened to win.

So a precondition is allowed to be lazy and is not allowed to be clever. In particular it must
**reuse the vocabulary the reader already reads** — the same module-level pattern, the same
`explicit_facts` reading — and never restate it in a pattern of its own. Two definitions of "this
sentence asks for a frequency" is the defect this codebase keeps finding in its own Spanish
(`_a_comparison_owns_it` says the same thing about comparisons, and says it first).

`yields_to` names the readers that must be asked **before** this one, and it exists only for pairs
that can genuinely both claim. It is not a priority number and there is no default: an edge is a
measured fact about two readers, with the reason beside it.

**The order is derived, and what it is derived from is not specificity.** That was the obvious
answer and it is measurably wrong here — see `test_the_general_reader_wins_and_that_is_deliberate`.
Three of the five contested pairs are won by the *most general* reader in the table, because the
sentence is an ellipsis and the reader that keeps the question in view knows more about it than the
reader that would rebuild one from scratch. Ordering by specificity would flip twelve sentences.

What the order is actually derived from:

1. a topological sort of `yields_to`, which is the part that is load-bearing, and
2. for everything the sort leaves free, `stood_at` — where the reader sat in the cascade.

`stood_at` is **preservation, not design**, and it is only honest because of the rule beside it:
`test_the_tiebreak_is_never_the_thing_that_decides` fails if any pair whose relative order
`stood_at` decides is a pair that can both claim. Where that rule holds, the tiebreak provably
cannot change an answer, and keeping today's positions is then strictly safer than inventing new
ones — an unseen Spanish sentence behaves exactly as it does today. The day two readers start
overlapping without an edge, the rule says so and somebody writes the edge down.

**What this shape cannot express**, said here so nobody has to discover it:

- **It cannot prove disjointness.** `when` holding is not `read` claiming, so "these two readers
  never both claim" is evidence from 1,341 sentences and not a theorem. A DSL with decidable
  implication would buy the theorem and cost a language nobody wants to learn — which the design
  this implements refuses by name.
- **It cannot express a precondition over anything but the turn.** Preconditions are pure functions
  of one query; a reader that needed the ledger to know whether it applies has no row here.
- **It cannot rank two readers that both claim.** `yields_to` records *that* one wins, never *why*
  in a form a machine can check. The why is a comment, because it is an argument about Spanish.
"""

from __future__ import annotations

from collections.abc import Callable, Container, Iterator
from dataclasses import dataclass, field
from typing import Any, Final

# The per-turn scratch lives on the query object. That is not a trick for its own sake: a query is
# built once per turn and thrown away with it, so an attribute on it has *exactly* the lifetime the
# memo needs — no ContextVar to reset, nothing global holding a holder's sentence, and no scope to
# leak across concurrent turns.
#
# A ContextVar was the obvious alternative and is the wrong one here. `decisions.recording_turn`
# documents why at length: `_steps` is an async generator, generators have no context of their own,
# and a `set` inside one outlives it. This has no such frame to be wrong about.
#
# Invisible to everything that reads a query: `__eq__` and `__hash__` are generated from the
# declared fields, and `replace()` rebuilds from them — so a query derived mid-turn correctly
# starts cold rather than inheriting a reading of a different sentence.
_MEMO: Final = "_finguard_reader_memo"


def memo_of(query: object) -> dict[str, Any]:
    """The turn's scratch, created on first use."""
    cache = getattr(query, _MEMO, None)
    if cache is None:
        cache = {}
        # `object.__setattr__` because the query is a frozen dataclass. Frozen is a statement about
        # the *question*, which nothing here changes; this is a cache hanging off it.
        object.__setattr__(query, _MEMO, cache)
    return cache


@dataclass(frozen=True)
class Precondition:
    """What must hold of a turn for one reader to be worth asking.

    Necessary, never sufficient. `name` is what a failure message says, so it is a sentence about
    Spanish and not a function name: "the sentence names a betting merchant" tells the next reader
    why their turn went somewhere else, and `_gambling_precondition` does not.
    """

    name: str
    holds: Callable[[Any], bool]

    def __call__(self, query: object) -> bool:
        return self.holds(query)


@dataclass(frozen=True)
class Reader:
    """One deterministic reader, and everything the registry knows about it."""

    name: str
    read: Callable[[Any], Any]
    when: Precondition
    # Where this reader sat in the cascade A2 removed. Preservation, not design — see the module
    # docstring, and the rule that keeps it inert.
    #
    # **Frozen at 1..19, and A2 declined the invitation to renumber them.** The handover said
    # `stood_at` becomes a pure declaration once no source position is left to preserve, which is
    # true, and `test_the_tiebreak_is_never_the_thing_that_decides` does make renumbering safe for
    # any pair that cannot both claim. It is still the wrong thing to do here: these numbers are now
    # the only surviving record of the order 1,341 sentences were measured equivalent against, and
    # `test_the_registry_picks_what_the_cascade_picks` compares the derived order with a `CASCADE`
    # literal. Renumbering and re-running it would be comparing the table with itself — the green
    # would stop being evidence. Renumber when there is a reason to change an order, and measure it.
    stood_at: int
    # Readers that must be asked first, because both can claim the same sentence. Never a default:
    # an empty tuple is the claim that nothing here contends with it.
    yields_to: tuple[str, ...] = ()
    # Why it yields, in Spanish terms. Unused by the machinery on purpose: this is an argument, and
    # a machine that could check it would be the rule engine the design refuses.
    because: str = ""


@dataclass(frozen=True)
class Claim:
    """A reader that took the turn, and what it built."""

    reader: str
    plan: Any


@dataclass
class Registry:
    """The readers, and the order derived from what their rows declare.

    The order is worked out once, when the table is built, so a bad table fails at import and never
    at the sentence that happened to expose it: a name registered twice, an edge to a reader nobody
    registered, or two readers that each go first are all a table nobody can mean.
    """

    readers: tuple[Reader, ...]
    _by_name: dict[str, Reader] = field(init=False, repr=False)
    _order: tuple[Reader, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._by_name = {reader.name: reader for reader in self.readers}
        if len(self._by_name) != len(self.readers):
            raise ValueError("two rows registered under one name")
        for reader in self.readers:
            for earlier in reader.yields_to:
                if earlier not in self._by_name:
                    raise ValueError(f"{reader.name} yields to unregistered {earlier}")
        self._order = _sorted(self.readers)

    @property
    def order(self) -> tuple[str, ...]:
        """The derived order, as names, so a test can print it."""
        return tuple(reader.name for reader in self._order)

    def __getitem__(self, name: str) -> Reader:
        return self._by_name[name]

    def __iter__(self) -> Iterator[Reader]:
        return iter(self.readers)

    def worth_asking(self, name: str, query: object) -> bool:
        """Whether this reader's precondition holds of this turn, worked out once per turn.

        In the same memo the reader's own answer goes into, and for the same reason:
        `_direct_period_comparison` is consulted up to five times on one sentence, and a
        precondition that ran five times would trade one kind of waste for another.

        A reader with no row is worth asking. That is the fallback for a `@_recogniser` nobody
        registered — it behaves exactly as it did before this module existed, and
        `test_every_reader_is_in_the_registry` is what stops that being how a reader arrives.
        """
        reader = self._by_name.get(name)
        if reader is None:
            return True
        cache = memo_of(query)
        key = "when:" + name
        if key not in cache:
            cache[key] = reader.when(query)
        return bool(cache[key])

    def ask(self, name: str, query: object) -> Any:
        """Ask one reader, through the seam it is already asked through.

        A passthrough on purpose. The precondition gate and the memo live in `_recogniser` and not
        here, so that **every** call site gets them without being rewritten — the cascade's, the
        four readers that consult another one, and this. A registry that gated only its own callers
        would leave the repeats it exists to remove exactly where they are.
        """
        return self._by_name[name].read(query)

    def select(self, query: object, among: Container[str] | None = None) -> Claim | None:
        """The reader this turn belongs to, in the derived order.

        The whole point of the table, and since A2 the only way `_steps` asks a reader anything.

        **`among` narrows it to one run of readers, and it is not an optimisation.** The nineteen
        were never consulted as one block: the cascade interleaved them with branches that are not
        readers and can also answer the turn — a courtesy, a balance, a bare shop name — so a single
        `select()` over all nineteen at the top would hand `_average_plan` sentences that
        `Intent.COURTESY` answers today. The handler declares the runs, asks each one where the
        cascade asked its members, and this walks the derived order restricted to that run.

        Restricting is safe because the derived order is a *global* order and every edge in the
        table points backwards: a reader in a later run is asked after everything it yields to,
        whether or not the blocker is in the same run. `test_no_run_yields_to_a_later_one` is what
        says that rather than leaving it to be true by luck.

        An unknown name in `among` is not checked here — it would be a set operation on every turn
        to catch a typo once. `test_the_five_runs_are_the_nineteen` catches it at CI instead, which
        is where a mistake in a constant belongs.
        """
        for reader in self._order:
            if among is not None and reader.name not in among:
                continue
            answer = self.ask(reader.name, query)
            if answer is not None:
                return Claim(reader=reader.name, plan=answer)
        return None

    def claimants(self, query: object) -> tuple[str, ...]:
        """Every reader that would claim this sentence, in derived order.

        Order does not decide anything here, which is the point: a sentence with two names in this
        tuple is a sentence whose answer depends on an ordering, and `yields_to` has to say so.
        """
        return tuple(
            reader.name for reader in self._order if self.ask(reader.name, query) is not None
        )


def _sorted(readers: tuple[Reader, ...]) -> tuple[Reader, ...]:
    """Topological sort of `yields_to`, ties broken by where the reader stood.

    **One reader at a time, always the earliest-standing one whose blockers have all been asked.**
    Emitting a whole ready layer at once would be the usual Kahn shape and it is the wrong one
    here: it would push every blocked reader behind every unblocked one, moving `how_many_payees`
    from third to seventeenth over a single edge that only concerns `ratio_request`. Taking the
    minimum each round means an edge moves exactly the readers it is about, and everything else
    stays where it was — which, with every edge in this table pointing backwards, reproduces the
    cascade's own order exactly. That is not a coincidence to rely on; it is the property that
    makes the equivalence check something to *run* rather than something to negotiate with.

    A cycle is a table saying two readers each go first, which is not a thing anybody can mean, so
    it raises rather than picking one.
    """
    waiting = {reader.name: set(reader.yields_to) for reader in readers}
    by_name = {reader.name: reader for reader in readers}
    ordered: list[Reader] = []
    while waiting:
        ready = [name for name, blockers in waiting.items() if not blockers]
        if not ready:
            raise ValueError(f"readers yield to each other in a cycle: {sorted(waiting)}")
        name = min(ready, key=lambda candidate: by_name[candidate].stood_at)
        ordered.append(by_name[name])
        del waiting[name]
        for blockers in waiting.values():
            blockers.discard(name)
    return tuple(ordered)


def free_pairs(readers: tuple[Reader, ...]) -> tuple[tuple[str, str], ...]:
    """Every pair whose relative order nothing in the table decides.

    The complement of `yields_to`, transitively closed: if `a` yields to `b` and `b` to `c`, then
    `a` before `c` is decided even though no row says so. What is left is what `stood_at` breaks,
    and the rule beside it is that no such pair may ever both claim a sentence.
    """
    names = [reader.name for reader in readers]
    before: dict[str, set[str]] = {reader.name: set(reader.yields_to) for reader in readers}
    changed = True
    while changed:
        changed = False
        for name in names:
            grown = set(before[name])
            for earlier in before[name]:
                grown |= before[earlier]
            if grown != before[name]:
                before[name] = grown
                changed = True
    free = []
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            if right not in before[left] and left not in before[right]:
                free.append((left, right))
    return tuple(sorted(free))
