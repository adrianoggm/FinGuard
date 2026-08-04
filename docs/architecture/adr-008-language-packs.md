# ADR-008 — A locale-independent financial core and language packs

**Status:** accepted, unimplemented
**Date:** 2026-08-04
**Supersedes:** nothing
**Numbering:** the review proposed ADR-006. That number is taken — vision.md §ADR-006 is
*A2A as an adapter* — and 001 to 007 are all in use, so this is 008.

## Context

Spanish is currently spread through the orchestrator rather than isolated in it. A grep for the
language finds it in six places that have nothing else in common:

| Where | What is Spanish about it |
|---|---|
| `intent_router` | "quién eres", "cuánto tengo", "a quién puedo" |
| `window_words` | "este mes", "el mes pasado", "los últimos N meses", "el año pasado" |
| `capability_guard` | "más … que", "el gasto más caro", the ordinals up to *décimo* |
| `explicit_facts` | "a qué personas", "en general", the rejection openers |
| `category_words` | the glossary that maps *cine* to `LEISURE` |
| `answer_writer`, `payment_writer`, `query_recovery`, `explainer` | every sentence a person reads, and every suggestion label |
| the planner and payment prompts | instructions, glossary and examples |
| the evaluation datasets | 235 planning cases and the conversational set |

None of that is a defect today. The product is Spanish, and the enums the core reasons about are
already language-free: `Direction.EXPENSE`, `Period.LAST_MONTH`, `Category.TRANSPORT_FUEL`,
`Action.RANK_MOVEMENTS`, `Slot.WINDOW`. What is missing is a boundary, so adding a second language
would mean editing eleven modules rather than writing one.

This ADR fixes the shape of that boundary before anybody needs it, and deliberately does **not**
build a second language.

## Decision

### 1. The financial core stays locale-independent

`FinancialPlan`, `TopMovements`, `Conversation`, the guard's rule names and the verifier's rule
names contain no natural language and never will. A plan produced from an English sentence and a
plan produced from the equivalent Spanish sentence are the same object.

### 2. Language knowledge lives behind one interface

```python
class LanguagePack(Protocol):
    locale: str

    intent_reader: IntentReader
    window_reader: WindowReader
    ordinal_reader: OrdinalReader
    category_lexicon: CategoryLexicon
    payment_reader: PaymentLanguageReader
    prompt_factory: PromptFactory
    messages: AssistantMessages
    formatter: LocaleFormatter
```

```
language/
├── contracts.py
├── registry.py
├── es_es/{intent,windows,ordinals,categories,payments,prompt,messages}.py
└── en_gb/…
```

Resolved once, from a registry keyed by locale, with a configured fallback:

```python
pack = language_registry.resolve(
    requested_locale=user.preferences.locale,
    fallback=settings.default_locale,
)
```

### 3. Explicitly rejected: a list whose *position* selects behaviour

```python
languages_support = ["SPANISH", "ENGLISH"]   # no
if language == "SPANISH":                    # no
    from ... import spanish_words
```

Both were considered and both are worse than what they replace. Positional configuration couples
meaning to order in a list somebody will reorder. Conditional imports make behaviour depend on
start-up, put a language branch in every module that reads a sentence, cannot express `es-ES`
against `es-MX`, and admit the state nobody wants: a Spanish prompt, an English parser and Spanish
answer text in one turn.

### 4. Five concepts, none inferred from another

```python
InteractionContext(
    input_language="es",             # interpret the sentence
    response_locale="es-ES",         # write the answer
    formatting_locale="es-ES",       # dates and digit grouping
    reporting_timezone="Atlantic/Canary",   # resolve "hoy"
    account_currency="EUR",          # financial semantics
)
```

These answer different questions and a real user separates them: writing in English, holding a
euro account, reading it in Canarian time. Two consequences are already live decisions rather than
future ones:

- **The reporting timezone belongs to the account, not the device.** Already true: it comes from
  the identity, and a browser header is at most an onboarding suggestion. A holder travelling to
  Madrid still wants "hoy" to mean their account's day.
- **Currency is financial data; locale is presentation.** `EUR` renders as `1.234,56 €` under
  `es-ES` and `€1,234.56` under `en-GB`, and it is the same money. `answer_writer.money` conflates
  the two today and says so in a comment pointing here.

### 5. Locale is chosen once per conversation, not detected per turn

Priority: explicit user preference → interface selection → the locale already fixed for this
conversation → detection of the first message → the deployment default.

Never re-detected per turn. "Sí", "15 euros", "Marta" and "Este" are the turns this conversation is
made of, and language detection over two words is a coin flip that would swap the assistant's
language mid-payment. Code-switching does not change it either: *Uber* in "¿cuánto gasté en Uber
last month?" is an entity, not a signal.

### 6. Prompts are authored per pack, not translated

Each pack supplies its own `system_rules`, `glossary`, `examples` and `clarification_examples`. The
structured output schema is identical across packs — that is the point of a locale-independent
core. A model may well plan better with English instructions and Spanish examples; that is a
measurement per model and locale, not an assumption.

### 7. Testing is one semantic matrix, not a suite per language

```yaml
scenario: fuel_last_month
expect_plan: {action: AGGREGATE, period: LAST_MONTH, direction: EXPENSE,
              categories: [TRANSPORT_FUEL]}
utterances:
  es-ES: ¿Cuánto gasté en gasolina el mes pasado?
  en-GB: How much did I spend on fuel last month?
```

The cross-language property is the one that matters:

```python
assert plan(es_ES) == plan(en_GB)
assert spanish.total_minor == english.total_minor
assert spanish.text != english.text
```

**Language may change the expression. It may not change the query or the figure.**

Plus per-language suites for what does not transfer — accents and misspellings, temporal forms,
ordinals, confirmations and negations, spoken amounts, proper nouns, regional variants — and
separate metrics per locale. One global accuracy number over two languages hides which one
regressed.

## Order of work

Nothing here is scheduled for the current pull request, and the ordering is the substantive part
of this decision:

1. Extract the language knowledge out of the core.
2. Define `LanguagePack`.
3. Move today's behaviour into `es-ES`, unchanged.
4. **Verify no result moves** — the planning evaluation and the conversational set produce
   identical output before and after.
5. Finish the Spanish semantic work: the typed query patch, `QueryContext` separated from the
   operation, counterparty grouping.
6. Stabilise the Spanish conversation.
7. Add `en-GB` as a second pack.
8. Run cross-language equivalence.

Steps 5 and 6 come before 7 deliberately. Spanish still has open semantic defects; adding a second
language first would duplicate every one of them into a second set of regular expressions, a second
prompt, a second batch of clarification questions and a second conversational failure mode.

## Consequences

**Good.** Adding a language becomes an implementation of an interface rather than a change to the
orchestrator. The core stays testable without any locale. The five conflated concepts become five
fields, and each one becomes answerable on its own.

**Costs.** A layer of indirection between a sentence and the code that reads it, paid before the
second language exists — accepted because the extraction is cheap now and expensive once the
knowledge is duplicated. And a discipline: any new Spanish string added before step 3 is one more
thing to move, so a `# ADR-008` comment goes on each one.

**Not decided here.** How an unsupported locale is refused, whether the interface language is
per-user or per-device, and whether formatting comes from CLDR or stays hand-rolled. Those are
worth deciding when a second pack is written, and deciding them now would be inventing constraints
from an imagined requirement.
