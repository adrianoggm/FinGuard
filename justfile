# Monorepo tasks. Requires docker, uv and just.
# ponytail: `just` instead of Nx while there are few projects, and instead of make
# because make does not ship with Windows. Nx comes in if CI ever starts to hurt.

set shell := ["bash", "-uc"]
set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

# Stamp compose builds without shell-specific `NAME=value command` syntax. Exported just
# variables reach every recipe on Windows and Unix alike.
export FINGUARD_SHA := `git rev-parse --short HEAD`
export FINGUARD_REF := `git rev-parse --abbrev-ref HEAD`
export FINGUARD_ORCHESTRATOR_SOURCE_SHA256 := `python apps/orchestrator/scripts/source_identity.py orchestrator`
export FINGUARD_BANKING_SOURCE_SHA256 := `python apps/orchestrator/scripts/source_identity.py banking`

# List the available tasks
default:
    @just --list
# --- Startup ------------------------------------------------------------------
#
# The build recipes stamp the current commit into the image, so /version can say which build is
# answering. Running `docker compose` by hand still works and reports "unknown", which is honest.

# Just the database
db:
    docker compose up -d --wait postgres
# A single component plus whatever it needs to be healthy: just component banking-api
component name:
    docker compose up -d --wait --build {{ name }}
# Skeleton: database + backend APIs. The skin (web, mobile) stays out
back:
    docker compose --profile core up -d --wait --build --remove-orphans
# Canonical backend startup name used by evaluation preflight and operator runbooks.
# `back` remains an alias for people who already use it.
up: back
# Skeleton plus the real-time voice gateway on 8002. Fake speech providers by default: the
# measured local ones run on the host, where the GPU and the models are.
voice:
    docker compose --profile voice up -d --wait --build --remove-orphans
# Downloads the models into the volume, once: the Spanish Piper voice and the Whisper weights.
# Doing it here rather than at startup keeps a 1.5 GB download out of the healthcheck's window. Out of the image on purpose: a
# model is data, and an image that carries 63 MB of it is an image nobody wants to pull twice.
voice-models:
    docker compose run --rm --entrypoint python voice-gateway -c "import urllib.request as r, pathlib; base='https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx'; d=pathlib.Path('/models/piper'); d.mkdir(parents=True, exist_ok=True); [(_ := r.urlretrieve(base+suffix, d/('es_ES-davefx-medium.onnx'+suffix)), print('ok', suffix or '.onnx')) for suffix in ('', '.json')]"
    docker compose run --rm --entrypoint python voice-gateway -c "from faster_whisper import WhisperModel; WhisperModel('medium', device='cpu', compute_type='int8'); print('ok whisper medium')"
# Real speech both ways: Whisper in, Piper out. Needs `just voice-models` first.
voice-real:
    docker compose -f compose.yaml -f compose.voice-real.yaml --profile voice up -d --wait --build --remove-orphans
# The demo on Gemini: the hosted model, with the visual budget measured for it. Needs the key in
# apps/orchestrator/.env.
gemini:
    docker compose -f compose.yaml -f compose.gemini.yaml --profile core up -d --wait --build --remove-orphans
# The demo as recorded: Gemini, real speech both ways and the decisions in Grafana, in one start,
# so no recipe recreates the orchestrator without another's settings. Needs the Gemini key in
# apps/orchestrator/.env and `just voice-models` once. Grafana on 3001.
demo:
    docker compose -f compose.yaml -f compose.gemini.yaml -f compose.voice-real.yaml -f compose.otel.yaml --profile voice --profile observability up -d --wait --build --remove-orphans
# The orchestrator is told where the gateway is only here: without the profile there is no
# gateway, and a blank URL is how the capability says it does not exist. Exported as a parameter
# rather than written `NAME=value docker compose`, which is bash syntax and not PowerShell's.
# Skeleton plus the A2A gateway on 8003 and three simulated gyms on 9101-9103 (spec §12)
a2a $ORCHESTRATOR_A2A_GATEWAY_URL="http://a2a-gateway:8000":
    docker compose --profile core --profile a2a up -d --build
# Frontend development server. Keeps the terminal attached so Ctrl+C stops Vite.
web:
    npm --prefix apps/web run dev
# Complete local application: healthy backend containers, then the frontend on port 3000.
app: back
    npm --prefix apps/web run dev
# Backwards-compatible name for the whole PoC.
poc: app
# Stops everything in the project; the data survives in the volume.
# --profile "*" is essential: without it, down ignores services that have a profile
down:
    docker compose --profile "*" down --remove-orphans
# Wipes the data too
reset:
    docker compose --profile "*" down --remove-orphans --volumes
# Container status
ps:
    docker compose --profile "*" ps --all
# Live logs: just logs, or just logs banking-api
logs service="":
    docker compose --profile "*" logs -f {{ service }}
# Builds the images without starting anything
build:
    docker compose --profile poc build
# --- Local development --------------------------------------------------------

# psql console on the banking database
db-shell:
    docker exec -it finguard-postgres-1 psql -U finguard_banking -d finguard_banking
# Applies both service-owned schemas.
migrate: migrate-banking migrate-orchestrator

# Applies only the ledger schema.
migrate-banking:
    uv run --directory apps/banking-api alembic upgrade head

# Applies only the assistant-owned schema in its one-shot container.
migrate-orchestrator:
    docker compose run --rm orchestrator-migrate
# Undoes the last migration
migrate-down:
    uv run --directory apps/banking-api alembic downgrade -1
# Creates an empty migration: just migration "contacts"
migration name:
    uv run --directory apps/banking-api alembic revision -m "{{ name }}"
# --- Development data ---------------------------------------------------------

# Loads the synthetic dataset. CAREFUL: it starts with a TRUNCATE of the ledger
seed file="0001_demo.sql":
    docker cp "apps/banking-api/seeds/{{ file }}" finguard-postgres-1:/tmp/finguard-seed.sql
    docker exec finguard-postgres-1 psql -U finguard_banking -d finguard_banking -v ON_ERROR_STOP=1 -q -f /tmp/finguard-seed.sql
    docker exec finguard-postgres-1 rm -f /tmp/finguard-seed.sql
    @{{ just_executable() }} seed-verify
# Checks that what is loaded adds up, and summarises the volume
seed-verify:
    docker cp apps/banking-api/seeds/verify.sql finguard-postgres-1:/tmp/finguard-seed-verify.sql
    docker exec finguard-postgres-1 psql -U finguard_banking -d finguard_banking -v ON_ERROR_STOP=1 -f /tmp/finguard-seed-verify.sql
    docker exec finguard-postgres-1 rm -f /tmp/finguard-seed-verify.sql
# Regenerates the dataset. Deterministic: change nothing and git diff stays empty
seed-generate:
    uv run --directory apps/banking-api python scripts/generate_seed.py
# Measures the execution accuracy of the query engine. Needs `just seed` first
evaluate:
    uv run --directory apps/banking-api python scripts/evaluate_queries.py
# Recomputes by hand the expected figures of the evaluation dataset
evaluate-expected:
    docker cp apps/banking-api/evaluation/expected.sql finguard-postgres-1:/tmp/finguard-expected.sql
    docker exec finguard-postgres-1 psql -U finguard_banking -d finguard_banking -v ON_ERROR_STOP=1 -f /tmp/finguard-expected.sql
    docker exec finguard-postgres-1 rm -f /tmp/finguard-expected.sql
# Installs the dependencies of every service
install:
    uv sync --project apps/banking-api
    uv sync --project apps/orchestrator
    uv sync --project apps/voice-gateway
    uv sync --project apps/a2a-gateway
    uv sync --project apps/simulated-gym-agent
    npm --prefix apps/web ci
# banking-api with autoreload. Needs port 8001 free: use `just db`, not `just back`
serve-banking:
    uv run --directory apps/banking-api uvicorn finguard_banking.bootstrap.main:app --reload --port 8001
# orchestrator with autoreload. Needs port 8000 free
serve-orchestrator:
    uv run --directory apps/orchestrator uvicorn finguard_orchestrator.bootstrap.main:app --reload --port 8000
# voice-gateway with autoreload on 8002. Fake providers unless VOICE_GATEWAY_* says otherwise;
# apps/voice-gateway/.env.defaults documents every setting.
serve-voice:
    uv run --directory apps/voice-gateway uvicorn finguard_voice_gateway.main:app --reload --port 8002
# It reads A2A_GATEWAY_* from the environment, and with A2A_GATEWAY_AGENTS blank it lists no
# agents; apps/a2a-gateway/.env.defaults documents every setting. An agent's id is its host, so
# gyms served on this machine need a loopback address each (see serve-gym):
#   A2A_GATEWAY_AGENTS=http://127.0.0.1:9101,http://127.0.0.2:9102,http://127.0.0.3:9103
#   A2A_GATEWAY_CREDENTIAL_127_0_0_1=dev-gym-norte-key
#   A2A_GATEWAY_CREDENTIAL_127_0_0_2=dev-gym-sur-token
# a2a-gateway with autoreload on 8003
serve-a2a:
    uv run --directory apps/a2a-gateway uvicorn finguard_a2a.main:app --reload --port 8003
# Its card names this host and port on both URLs, and it carries compose's development
# credentials for norte and sur. Only `style`, `port` and `host` are meant to be passed.
# One simulated gym with autoreload: just serve-gym sur 9102 127.0.0.2
serve-gym style="norte" port="9101" host="127.0.0.1" $GYM_STYLE=style $GYM_A2A_URL=("http://" + host + ":" + port) $GYM_PUBLIC_URL=GYM_A2A_URL $GYM_API_KEY="dev-gym-norte-key" $GYM_BEARER_TOKEN="dev-gym-sur-token":
    uv run --directory apps/simulated-gym-agent uvicorn finguard_simulated_gym.main:app --reload --host {{ host }} --port {{ port }}
# The active LLM policy. The default is the compose operator token; pass another
# explicitly when running against a different local configuration.
llm-policy token="demo-token-operator":
    @uv run --directory apps/orchestrator python -c "import json, sys, urllib.request; request=urllib.request.Request('http://127.0.0.1:8000/admin/llm-policy', headers={'Authorization': 'Bearer ' + sys.argv[1]}); print(json.dumps(json.load(urllib.request.urlopen(request)), indent=2))" "{{ token }}"

# Ask one fresh financial question against the local stack.
ask question token="demo-token-laura-0001":
    @uv run --directory apps/orchestrator python -c "import json, sys, urllib.request, uuid; payload=json.dumps({'question': sys.argv[1]}, ensure_ascii=False).encode('utf-8'); request=urllib.request.Request('http://127.0.0.1:8000/v1/questions', data=payload, headers={'Authorization': 'Bearer ' + sys.argv[2], 'Content-Type': 'application/json; charset=utf-8', 'Idempotency-Key': str(uuid.uuid4())}, method='POST'); print(json.dumps(json.load(urllib.request.urlopen(request)), ensure_ascii=False, indent=2))" "{{ question }}" "{{ token }}"
# --- Quality ------------------------------------------------------------------

lint:
    uv run --directory apps/banking-api ruff check .
    uv run --directory apps/banking-api ruff format --check .
    uv run --directory apps/orchestrator ruff check .
    uv run --directory apps/orchestrator ruff format --check .
    uv run --directory apps/voice-gateway ruff check .
    uv run --directory apps/voice-gateway ruff format --check .
    uv run --directory apps/a2a-gateway ruff check .
    uv run --directory apps/a2a-gateway ruff format --check .
    uv run --directory apps/simulated-gym-agent ruff check .
    uv run --directory apps/simulated-gym-agent ruff format --check .
# Applies formatting and automatic fixes
format:
    uv run --directory apps/banking-api ruff format .
    uv run --directory apps/banking-api ruff check --fix .
    uv run --directory apps/orchestrator ruff format .
    uv run --directory apps/orchestrator ruff check --fix .
    uv run --directory apps/voice-gateway ruff format .
    uv run --directory apps/voice-gateway ruff check --fix .
    uv run --directory apps/a2a-gateway ruff format .
    uv run --directory apps/a2a-gateway ruff check --fix .
    uv run --directory apps/simulated-gym-agent ruff format .
    uv run --directory apps/simulated-gym-agent ruff check --fix .
typecheck:
    uv run --directory apps/banking-api mypy src
    uv run --directory apps/orchestrator mypy src
    uv run --directory apps/voice-gateway mypy src
    uv run --directory apps/a2a-gateway mypy src
    uv run --directory apps/simulated-gym-agent mypy src
# Tests for every service. The ones marked integration need `just db`
test:
    uv run --directory apps/banking-api pytest -q
    uv run --directory apps/orchestrator pytest -q
    uv run --directory apps/voice-gateway pytest -q
    uv run --directory apps/a2a-gateway pytest -q
    uv run --directory apps/simulated-gym-agent pytest -q
# Without a database: skips the integration tests
test-unit:
    uv run --directory apps/banking-api pytest -q -m "not integration"
    uv run --directory apps/orchestrator pytest -q -m "not integration"
    uv run --directory apps/voice-gateway pytest -q -m "not integration"
    uv run --directory apps/a2a-gateway pytest -q -m "not integration"
    uv run --directory apps/simulated-gym-agent pytest -q -m "not integration"
# The visual planner in shadow against the annotated cases. Three runs with the GPU alone, then the
# minimum -- one run is not a number. P95 latency is the budget candidate for docs/next.md.
# `model` defaults to the shipped, measured planner (evaluation/planning/RESULTS.md), not to
# ORCHESTRATOR_VISUAL_PLANNER_MODEL's blank default, which would otherwise fall through to the
# baseline financial-planner model and evict whatever was warmed (M-3). Pass other flags via args;
# the script itself always runs `shadow` -- there is no `--mode`.
evaluate-visual model="qwen3.5:9b" *args:
    uv run --directory apps/orchestrator python scripts/evaluate_visual.py --model {{ model }} {{ args }}
# The reply normaliser against recorded gym replies, six of them hostile (spec §9): the real
# `LlmReplyNormaliser` and verifier, no gateway and no gym. Writes evaluation/a2a/RESULTS.md and
# last-run.json, and exits non-zero on a breach -- a verified value the reply does not state.
# Pass --provider and the like via args. The Ollama context is the service's own setting,
# ORCHESTRATOR_OLLAMA_CONTEXT_TOKENS: on an 8 GB GPU, `ollama stop qwen3.5:9b`, then
# `ORCHESTRATOR_OLLAMA_CONTEXT_TOKENS=2048 just evaluate-a2a`, and `ollama ps` should say 100% GPU.
evaluate-a2a model="qwen3.5:9b" *args:
    uv run --directory apps/orchestrator python scripts/evaluate_a2a.py --model {{ model }} {{ args }}
# Separate from lint/typecheck/test because those iterate over the Python services and npm
# answers all three at once. `build` runs tsc a second time on purpose: vite strips types
# without checking them, so the build is what proves the bundle it emits compiles.
# Types, tests and a build for the client
web-check:
    npm --prefix apps/web run typecheck
    npm --prefix apps/web test
    npm --prefix apps/web run build
# Since CI stopped running on every push, this is the whole verification a working branch gets,
# so it has to cover what CI covers — otherwise the change moved a gap rather than a cost.
# `test` includes the integration suite, so `just db` has to be up first.
# What has to pass before pushing a commit
check: lint typecheck test web-check

# Installs the git hook. Run once per clone; uvx needs no global install
hooks:
    uvx pre-commit install
# Runs the hooks over the whole repository, not just what is staged
hooks-all:
    uvx pre-commit run --all-files
