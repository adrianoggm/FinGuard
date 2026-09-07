# Signed Visual Context (Wave 4.2, Task 3b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The browser stops being a semantic authority over charts. Every visual context the conversation echoes carries a seal the server issued; a context whose seal does not verify for this holder is still drawn and navigated, but no restyle, no saved-metric source and no verifier decision is ever taken on it.

**Architecture:** Three seams, all existing. (1) `MetricTokenCodec` gains `seal_context(holder, semantic) -> str` and `verify_context(token, holder, semantic) -> bool`: HMAC-SHA256 with the same keys and rotation, prefix `c1.`, body `{v, k, h, g}` where `g` is the SHA-256 of the canonical JSON of the context's wire semantics — the token binds holder and content and stays short. (2) `VisualContext` gains `token: str | None` (travels; the seal to re-emit) and `trusted: bool = True` (turn-local, like `decision`); `VisualContextPayload` gains `token`; `conversation_mapper.to_domain(payload, *, verify=None)` marks each context `trusted` only through a verified seal and drops an unverified token; without a verifier it behaves as today (tests). (3) The two places a context is born — `llm_mapper._remember_visual_context` and `llm_controller._derived_context` — seal it with the holder in scope; `_conversation_of` (the one production entry from the wire) always verifies; `_visual_only_response` and `_saved_visual_source` refuse an untrusted context with `VISUAL_UNTRUSTED`. The web client echoes the token like every other opaque field.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pytest-asyncio (`asyncio_mode = "auto"`), mypy strict on `src`, ruff line-length 100, `uv`; `hmac`/`hashlib` (stdlib).

**Spec:** `docs/superpowers/specs/2026-09-05-continuous-visual-conversation-design.md` §4 (as amended in 4.2: the wire carries value-free shape facts; "the wire is not where authority lives: the verifier decides") — this plan makes the second half true by construction; §13. The argument: the second and third external reviews of `task/continuous-visual-conversation` (2026-09-06 23:16, 2026-09-07 10:03): a client could flip `comparison` and stack a comparison, or pair a valid `metric_source` with an unsigned profile and get a misleading chart signed and saved.

**Base:** `task/continuous-visual-conversation @ 405b9a8`. Suite at base, recorded — do not re-measure: `tests/test_[a-l]*.py` + `[m-s]` + `[t-z]` = **3890 passed / 3 skipped / 204 deselected**, corpus **202 passed at 0.0 %**.

## Global Constraints

- **Fail closed in production, unchanged in tests.** `_conversation_of` is the only production call of `to_domain` and always passes the verifier (a test greps `src` for `to_domain(` and asserts the call sites); `to_domain` without a verifier keeps today's behaviour so the hundreds of round-trip tests stay as they are.
- **Value-free.** The seal is a digest; no amount, label or sentence enters it or the token. The user's question never enters the context, the token, records or logs.
- **No new setting.** The seal reuses `ORCHESTRATOR_METRICS_SIGNING_KEYS`/`_ACTIVE` and the composition root's development-key fallback; when the fallback key is in use the app logs one warning at startup (Task 3). Key rotation invalidates seals the same way it invalidates metric tokens.
- **Refuse, never repair.** An untrusted context is drawn and navigated (it stays in `visual_contexts`) but every semantic use refuses: restyle → `VISUAL_UNTRUSTED` («No reconozco ese gráfico; pide uno nuevo y podré cambiarlo.»), saved-metric source → `None`, chips → none offered for it. Nothing re-derives a profile.
- **Older conversations** (no token) are untrusted until their next chart — stated in the docs, not called compatibility.
- `domain/` imports neither `application/` nor `infrastructure/` (the token is an opaque `str` on the domain object; `trusted` is a `bool`).
- Atomic commits, imperative present, no `Co-Authored-By`, no push. Never edit `Ceiling`, `Floor`, planning dataset cases or existing corpus episodes (episode 32 is this wave's own).
- Suite gate per commit: `uv run ruff check src tests scripts`, `uv run ruff format --check src tests scripts`, `uv run mypy src`, then FOUR foreground pytest calls each with `timeout: 600000` (`tests/test_[a-l]*.py --deselect tests/test_conversation_corpus.py`, `tests/test_[m-s]*.py`, `tests/test_[t-z]*.py`, `tests/test_conversation_corpus.py`).

---

## File Structure

| file | responsibility | task |
| --- | --- | --- |
| `infrastructure/security/metric_tokens.py` | `seal_context` / `verify_context` beside `issue` / `verify`; shared `_signed`/`_opened` helpers | 1 |
| `domain/models/chart.py` | `VisualContext.token`, `VisualContext.trusted` | 2 |
| `infrastructure/dto/llm_dto.py` | `VisualContextPayload.token` | 2 |
| `infrastructure/mappers/conversation_mapper.py` | `to_payload` emits `token`; `to_domain(payload, *, verify=None)` marks trust; `context_semantics(payload) -> dict` (the one definition of what a seal covers) | 2 |
| `infrastructure/mappers/llm_mapper.py` | `question_to_dto(..., holder_reference, metric_tokens)` seals the new context | 3 |
| `infrastructure/controllers/llm_controller.py` | `_conversation_of(request, who, metric_tokens)` verifies; `_derived_context(..., seal)`; `VISUAL_UNTRUSTED`; `_saved_visual_source` requires trust; `_chart_suggestions` gets nothing for an untrusted context | 3 |
| `bootstrap/main.py` | the development-key warning | 3 |
| `tests/support/episodes.py` | the corpus harness carries the seal across turns (whatever it needs so a sealed context stays trusted between echoed turns) | 4 |
| `evaluation/conversations/32-visual-continuation.yaml` | one turn: an echoed context with a tampered profile is refused (`route: VISUAL`, `banking_calls: 0`, outcome `VISUAL_UNTRUSTED`) — only if the harness can express a tampered echo; else the tamper test lives in `tests/test_conversation_corpus.py` | 4 |
| `apps/web/src/api.ts` | `token?: string | null` on the visual context type (opaque, echoed) | 3 |
| docs: design §4/§13, `docs/next.md`, `docs/conversational-capabilities.md`, this plan's "Landed" notes | | 5 |

---

### Task 1: The codec seals a context

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/infrastructure/security/metric_tokens.py`
- Test: `apps/orchestrator/tests/test_saved_metric_codec.py` (the codec's existing test file)

**Interfaces:**
- Produces: `MetricTokenCodec.seal_context(self, holder_reference: str, semantics: dict[str, object]) -> str` and `MetricTokenCodec.verify_context(self, token: str | None, holder_reference: str, semantics: dict[str, object]) -> bool`. `semantics` is any JSON-serialisable dict; the codec canonicalises it (`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`) and digests it with SHA-256; the token is `"c1." + b64url(body) + "." + b64url(hmac)` with body `{"v": 1, "k": <active key name>, "h": holder, "g": <hex digest>}`. `verify_context` returns `False` for `None`, a malformed token, a wrong prefix, an unknown key name, a bad signature, another holder or a different digest — never raises.
- Consumes: the existing key dict, `active`, and the HMAC/base64 helpers of `issue`/`verify` — extract `_sign(body: dict) -> str` (returns the two encoded parts joined) and `_open(token: str, prefix: str) -> dict | None` so `issue`/`verify` and the two new methods share them; `issue`/`verify`'s behaviour and `MetricTokenError` are unchanged (their tests pass untouched).

**Landed** (`0dd2caa`): exactly as specified — `seal_context`/`verify_context` on
`MetricTokenCodec`, `_sign`/`_open` shared with `issue`/`verify`, prefix `c1.`, body `{v, k, h, g}`
with `g` the SHA-256 hex digest of the canonical JSON; `verify_context` never raises, returning
`False` for `None`, a malformed token, a wrong prefix, an unknown key, a bad signature, another
holder or a changed digest.

- [ ] **Step 1: Failing tests** (append to `tests/test_saved_metric_codec.py`; build the codec the way the file already does)

```python
def test_a_sealed_context_verifies_for_its_holder_and_content() -> None:
    codec = MetricTokenCodec({"k1": b"x" * 32}, "k1")
    semantics = {"chart_id": "chart-1", "spec": ["bar"], "profile": {"comparison": True}}
    token = codec.seal_context("holder-a", semantics)
    assert token.startswith("c1.")
    assert codec.verify_context(token, "holder-a", semantics) is True


@pytest.mark.parametrize(
    "tamper",
    [
        lambda t, s: (t, "holder-b", s),                                        # another holder
        lambda t, s: (t, "holder-a", {**s, "profile": {"comparison": False}}),  # content changed
        lambda t, s: (t[:-2] + ("A" if t[-1] != "A" else "B") + t[-1], "holder-a", s),  # signature bit
        lambda t, s: ("m1." + t[3:], "holder-a", s),                            # wrong prefix
        lambda t, s: ("not a token", "holder-a", s),
        lambda t, s: (None, "holder-a", s),
    ],
    ids=["holder", "content", "signature", "prefix", "malformed", "missing"],
)
def test_a_seal_does_not_verify_when_anything_moves(tamper) -> None:
    codec = MetricTokenCodec({"k1": b"x" * 32}, "k1")
    semantics = {"chart_id": "chart-1", "spec": ["bar"], "profile": {"comparison": True}}
    token = codec.seal_context("holder-a", semantics)
    assert codec.verify_context(*tamper(token, semantics)) is False


def test_a_seal_survives_key_order_and_dies_with_key_rotation() -> None:
    codec = MetricTokenCodec({"k1": b"x" * 32}, "k1")
    token = codec.seal_context("h", {"b": 1, "a": [1, 2]})
    assert codec.verify_context(token, "h", {"a": [1, 2], "b": 1}) is True   # canonical JSON
    rotated = MetricTokenCodec({"k2": b"y" * 32}, "k2")
    assert rotated.verify_context(token, "h", {"a": [1, 2], "b": 1}) is False
```

- [ ] **Step 2: Run** `uv run pytest -q tests/test_saved_metric_codec.py` — FAIL (`AttributeError: seal_context`).
- [ ] **Step 3: Implement** the two methods and the two shared helpers; `issue`/`verify` rewired onto the helpers with no behavioural change (their existing tests are the guard). Module docstring: one paragraph on the seal — what it binds (holder + content digest), what it does not carry (no content, no expiry; rotation is the only staleness), and that `verify_context` never raises because an unverified context is a state, not an error.
- [ ] **Step 4: Run** the file, gates, the four suite calls.
- [ ] **Step 5: Commit** — `feat(security): the metric codec seals a visual context to its holder and its content`.

---

### Task 2: The context carries its seal and knows whether it is trusted

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/domain/models/chart.py` (`VisualContext`)
- Modify: `apps/orchestrator/src/finguard_orchestrator/infrastructure/dto/llm_dto.py` (`VisualContextPayload.token`)
- Modify: `apps/orchestrator/src/finguard_orchestrator/infrastructure/mappers/conversation_mapper.py` (`to_payload`, `to_domain`, `context_semantics`)
- Test: `apps/orchestrator/tests/test_visualization_stage1.py`

**Interfaces:**
- `VisualContext(..., derived_from=None, turn_id=None, token: str | None = None, trusted: bool = True)` — `token` travels (the seal to re-emit); `trusted` is turn-local and defaults `True` because a context made in this process is the server's own; only `to_domain` with a verifier sets it `False`. Docstring says exactly that, beside the existing turn-local note.
- `VisualContextPayload.token: str | None = Field(default=None, max_length=512, pattern=r"^c1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")`.
- `conversation_mapper.context_semantics(payload: VisualContextPayload) -> dict[str, object]` = `payload.model_dump(mode="json", exclude={"token"})` — the one definition of what a seal covers (the whole wire context minus the seal itself). `to_payload` builds each context's dict as today plus `"token": context.token`. The sealing function `seal_context(context: VisualContext, holder_reference: str, codec: MetricTokenCodec) -> VisualContext` lives in `conversation_mapper` too: it serialises the context exactly as `to_payload` does (factor the per-context dict into `_context_payload(context) -> dict` used by both), validates it through `VisualContextPayload.model_validate(...)`, and returns `replace(context, token=codec.seal_context(holder_reference, context_semantics(payload)))` — so the digest at creation and the digest at verification are computed over the same pydantic-normalised dict.
- `to_domain(payload, *, verify: Callable[[str | None, dict[str, object]], bool] | None = None)`: for each item, `trusted = verify(item.token, context_semantics(item)) if verify is not None else True`; the rebuilt `VisualContext` gets `token=item.token if trusted else None`, `trusted=trusted`. The caller binds the holder into `verify` (Task 3).

**Landed** (`3d0e78c`): exactly as specified — `VisualContext.token`/`.trusted`,
`VisualContextPayload.token`, `conversation_mapper.context_semantics`/`seal_context`, and
`to_domain(payload, *, verify=None)` marking trust per item.

- [ ] **Step 1: Failing tests**

```python
def test_a_sealed_context_round_trips_trusted_and_a_tampered_one_does_not() -> None:
    codec = MetricTokenCodec({"k1": b"x" * 32}, "k1")
    context = VisualContext("chart-1", BREAKDOWN_SPEC, BREAKDOWN_PROFILE)     # the file's fixtures
    sealed = seal_context(context, "holder-a", codec)
    assert sealed.token is not None and sealed.trusted is True

    verify = lambda token, semantics: codec.verify_context(token, "holder-a", semantics)
    echoed = to_domain(to_payload(Conversation(visual_contexts=(sealed,))), verify=verify)
    assert echoed.visual_contexts[0].trusted is True
    assert echoed.visual_contexts[0].token == sealed.token

    payload = to_payload(Conversation(visual_contexts=(sealed,)))
    payload.visual_contexts[0].profile.comparison = not payload.visual_contexts[0].profile.comparison
    tampered = to_domain(payload, verify=verify).visual_contexts[0]
    assert tampered.trusted is False and tampered.token is None

    other = to_domain(to_payload(Conversation(visual_contexts=(sealed,))),
                      verify=lambda t, s: codec.verify_context(t, "holder-b", s)).visual_contexts[0]
    assert other.trusted is False


def test_an_unsealed_echo_is_untrusted_only_when_someone_verifies() -> None:
    context = VisualContext("chart-1", BREAKDOWN_SPEC, BREAKDOWN_PROFILE)     # token None: an older client
    payload = to_payload(Conversation(visual_contexts=(context,)))
    assert to_domain(payload).visual_contexts[0].trusted is True             # tests and internal callers: as today
    assert to_domain(payload, verify=lambda t, s: False).visual_contexts[0].trusted is False


def test_the_seal_covers_the_whole_wire_context_but_not_itself() -> None:
    payload = to_payload(Conversation(visual_contexts=(VisualContext("chart-1", BREAKDOWN_SPEC, BREAKDOWN_PROFILE),)))
    semantics = context_semantics(payload.visual_contexts[0])
    assert "token" not in semantics
    assert set(semantics) == set(VisualContextPayload.model_fields) - {"token"}
```

(If the payload model is frozen, mutate through `model_copy(update=...)`; adapt to the file's fixture names.)

- [ ] **Step 2: Run** — FAIL (`seal_context`/`context_semantics` undefined; `trusted` unknown).
- [ ] **Step 3: Implement**; the existing wire tests (`test_the_wire_carries_derived_from_and_turn_id_and_nothing_server_side` — extend its allowed key set with `token`; `test_a_payload_with_none_of_the_new_keys_still_validates` — a payload without `token` validates) stay green; every other `to_domain(...)` call in tests is unchanged.
- [ ] **Step 4: Run** `tests/test_visualization_stage1.py tests/test_visualization_stage2_core.py tests/test_visual_context_producers.py`, gates, the four suite calls.
- [ ] **Step 5: Commit** — `feat(visual): a visual context carries the server's seal and knows whether it is trusted`.

---

### Task 3: Sealed where it is born, verified where it enters, refused where it is untrusted

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/infrastructure/mappers/llm_mapper.py` (`question_to_dto` → `_remember_visual_context(result, visualization, *, seal)`; `_chart_suggestions` offers nothing for an untrusted context)
- Modify: `apps/orchestrator/src/finguard_orchestrator/infrastructure/controllers/llm_controller.py` (`_conversation_of(request, who, metric_tokens)`; `_derived_context(..., seal)`; `_visual_only_response` refuses `not context.trusted` with `VISUAL_UNTRUSTED`; `_saved_visual_source` returns `None` for an untrusted context; both routes pass `who.holder_reference` and `metric_tokens` where the contexts are made)
- Modify: `apps/orchestrator/src/finguard_orchestrator/bootstrap/main.py` (one `logger.warning` when the development key is the codec's only key)
- Modify: `apps/web/src/api.ts` (`token?: string | null;` on the visual context type, comment "the server's seal; echoed, never read")
- Test: `apps/orchestrator/tests/test_visualization_stage2_core.py`, `tests/test_visual_context_producers.py`, `tests/test_llm_controller*.py` (whichever drives the routes with a `TestClient`)

**Interfaces:**
- `seal: Callable[[VisualContext], VisualContext] | None` — built once per request as `functools.partial(seal_context, holder_reference=who.holder_reference, codec=metric_tokens)` (keyword-bound) and passed to `question_to_dto(result, *, holder_reference, metric_tokens)` (which builds it itself from the two it already receives — no new parameter) and to `_visual_only_response(..., seal=...)` → `_derived_context(trusted, spec, chart_id, *, decision=None, seal=None)`; when `seal` is `None` (tests calling the mapper without a holder) the context is unsealed and stays `trusted=True` in-process.
- `_conversation_of(request: QuestionRequest, who: Caller, metric_tokens: MetricTokenCodec) -> Conversation` = `to_domain(request.conversation, verify=lambda token, semantics: metric_tokens.verify_context(token, who.holder_reference, semantics))` — both routes call it this way; a test greps `src/` for `to_domain(` and asserts the only production call is inside `_conversation_of` (module-level string assertion over the two files, the way `test_reader_registry.py` asserts structure).
- `VISUAL_UNTRUSTED`: `QuestionResponse(outcome="VISUAL_UNTRUSTED", text="No reconozco ese gráfico; pide uno nuevo y podré cambiarlo.", conversation=<unchanged>)`, returned before `_saved_visual_source`/merge/verify; zero ledger calls; no decision row (a `VISUAL_FOLLOW_UP` row is recorded upstream as today).
- The routes: `holder_reference=who.holder_reference` is passed to `_visual_only_response` unconditionally (today it is `None` unless a saved source exists — the save-candidate logic keeps its own `source is not None` guard).

**Landed** (`e6ae808`, fix round 1 `56fc066`, fix round 2 `daf8e0c`): sealed where born
(`question_to_dto`'s `holder_reference`/`metric_tokens`, `_derived_context`'s `seal`), verified
where it enters (`_conversation_of`; the grep-style test confirms it is the only production
`to_domain(` call site with `verify=`), refused where untrusted (`VISUAL_UNTRUSTED`, no saved
source, no chips); the development-key warning lives in `main.py`. `56fc066` corrected the first
cut, which had conflated "seal this chart" with "offer to save it" — a chart is now sealed
whether or not `offer_saving` is `True`; only the save candidate is gated by it. `daf8e0c` found
the same mistake one level up: `_compound_read_reply` was still calling `question_to_dto(result)`
with neither keyword, so a compound turn's own charts were born unsealed and refused their own
restyle one turn later — fixed, and pinned there by a seal-side allowlist test
(`test_the_seal_is_never_silently_skipped`) mirroring the verify-side one.

- [ ] **Step 1: Failing tests** — (a) `question_to_dto(result, holder_reference="h", metric_tokens=codec)` → the new context's `token` verifies with `codec.verify_context(token, "h", context_semantics(payload_context))`; without `holder_reference` → `token is None`, `trusted is True`. (b) `_visual_only_response` over a conversation whose context is `replace(context, trusted=False)` → `VISUAL_UNTRUSTED`, conversation unchanged, zero ledger calls, no `VISUAL_PLANNER` row. (c) a restyle through `_visual_only_response(..., seal=...)` yields a derived context whose `token` verifies for the holder. (d) the route-level test (the file that drives `/v1/questions` with a `TestClient` and a dev token): a first question that draws a chart → the response's `visual_contexts[0].token` is present; echoing the conversation with that token and asking «ordénalo de menor a mayor» → `VISUAL_UPDATED`; echoing it with `profile.comparison` flipped → `VISUAL_UNTRUSTED`; echoing without any token → `VISUAL_UNTRUSTED`. (e) the grep-test on `to_domain(` call sites. (f) `_saved_visual_source` returns `None` for an untrusted context even with a valid metric token.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement**; the composition-root warning (`main.py`: when `configured_metric_keys` is empty: `logger.warning("visual seals and metric tokens use the development key: set ORCHESTRATOR_METRICS_SIGNING_KEYS")`).
- [ ] **Step 4: Run** the target files, gates, the four suite calls. Expected: the corpus may go red on episode 32's restyle turn if the harness echoes conversations without the seal — that is Task 4's work; if it does, STOP after confirming the cause and report it (Task 4 begins from this commit).
- [ ] **Step 5: Commit** — `feat(visual): a context is sealed where it is born, verified where it enters, and refused where it is untrusted`.

---

### Task 4: The corpus carries the seal, and sees a tamper refused

**Files:**
- Modify: `apps/orchestrator/tests/support/episodes.py` (however the harness echoes the conversation between turns — if it goes through the routes with a holder, the seal already round-trips; if it bypasses `_conversation_of`, it must now use the production entry so a sealed context stays trusted)
- Modify: `apps/orchestrator/evaluation/conversations/32-visual-continuation.yaml` — add a `tamper:` turn key ONLY if the harness can express "echo the previous conversation with one profile flag flipped" cheaply (a `Turn.tamper: str | None` naming the flag); else add the tamper case to `tests/test_conversation_corpus.py` as a harness test over episode 32's conversation.
- Test: `tests/test_conversation_corpus.py`

**Landed** (`04f61f2`): the probe came back green — every turn already reaches `/v1/questions` and
echoes its own answer's conversation, so the seal round-tripped through the harness for free and
`episodes.py` needed only one line: `VISUAL_UNTRUSTED` added to `_VISUAL_OUTCOMES` beside
`VISUAL_UNCHANGED`, so the harness's `tool_of` recognises it as the `VISUAL` route instead of
silently counting it as a query. The tamper case is ad hoc Python against the real episode
(`test_conversation_corpus.py`'s own `_tampered_restyle`), not a `tamper:` key on the yaml episode
— appending a turn to `32-visual-continuation.yaml` would have moved two corpus-wide ratchets for
a reason neither prices in, so the plan's other option was taken instead. Corpus 202 → 204.

- [ ] Probe first: run `uv run pytest -q tests/test_conversation_corpus.py -k visual` at Task 3's head; if green, the harness already carries the seal — add only the tamper test. If red, fix the harness to enter through `_conversation_of` (never by disabling verification).
- [ ] The tamper test: after episode 32's turn 1, flip `comparison` (or `verified_total`) on the echoed context's profile, ask the restyle → `VISUAL_UNTRUSTED`, `banking_calls: 0`, the conversation's `visual_contexts` unchanged.
- [ ] Commit — `test(corpus): the corpus echoes sealed charts, and a tampered chart is refused without a call`.

---

### Task 5: The record on paper

- Design §4: the sentence "the wire is still not where authority lives: the verifier decides" gains "— and since Task 3b, a context is only a verifier's input if the server's seal on it verifies for this holder (`MetricTokenCodec.seal_context`), so a client can draw and navigate a chart it altered but cannot have a decision taken on it". §13 lists what is refused: an unsealed or altered visual context as a source of restyles or saved metrics.
- `docs/next.md` 4.2 paragraph: one dated paragraph after it — «**2026-09-07 — the visual context is sealed.** …» naming the seal, `VISUAL_UNTRUSTED`, the development-key warning, that older conversations are untrusted until their next chart, and what this does NOT do (no expiry; no cross-device trust; no server store).
- `docs/conversational-capabilities.md` row «Reformular el gráfico con palabras»: Estado gains «sobre un contexto sellado por el servidor (`c1.` token); un contexto alterado o sin sello se dibuja pero no se modifica».
- This plan: "Landed" notes per task.
- Commit — `docs(visual): the visual context is sealed; a chart the server did not seal is drawn but never decided on`.

---

## Self-review

- **Spec coverage:** §4's authority sentence becomes true by construction (Tasks 2-3); §13 gains the refusal (Task 5); the third external review's two scenarios — a flipped `comparison`, and a valid `metric_source` paired with an unsigned profile — are both refused at `_visual_only_response`/`_saved_visual_source` (Task 3, tests b and f).
- **Placeholders:** Task 3's route-level test names its file by pattern (`tests/test_llm_controller*.py`) because the controller's test file name is not known at planning time — the brief will name it after the pre-read. Task 4 is deliberately probe-first.
- **Type consistency:** `seal_context(context, holder_reference, codec) -> VisualContext` (mapper) vs `MetricTokenCodec.seal_context(holder_reference, semantics) -> str` (codec) — two names, two layers; the brief for Task 3 says which is which.
- **Pre-read risks at dispatch:** (1) whether `VisualContextPayload` is frozen (mutation in tests); (2) the harness's echo path (Task 4 probe); (3) the controller test file that drives the routes with a `TestClient` and how it authenticates; (4) `functools.partial` with keyword binding vs a small closure — either is fine.
