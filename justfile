# Monorepo tasks. Requires docker, uv and just.
# ponytail: `just` instead of Nx while there are few projects, and instead of make
# because make does not ship with Windows. Nx comes in if CI ever starts to hurt.

set shell := ["bash", "-uc"]
set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

# Stamp compose builds without shell-specific `NAME=value command` syntax. Exported just
# variables reach every recipe on Windows and Unix alike.
export FINGUARD_SHA := `git rev-parse --short HEAD`
export FINGUARD_REF := `git rev-parse --abbrev-ref HEAD`

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
# Applies pending migrations. Only banking-api has a schema of its own so far
migrate:
    uv run --directory apps/banking-api alembic upgrade head
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
    npm --prefix apps/web ci
# banking-api with autoreload. Needs port 8001 free: use `just db`, not `just back`
serve-banking:
    uv run --directory apps/banking-api uvicorn finguard_banking.bootstrap.main:app --reload --port 8001
# orchestrator with autoreload. Needs port 8000 free
serve-orchestrator:
    uv run --directory apps/orchestrator uvicorn finguard_orchestrator.bootstrap.main:app --reload --port 8000
# The active LLM policy. The default is the compose operator token; pass another
# explicitly when running against a different local configuration.
llm-policy token="demo-token-operator":
    @uv run --directory apps/orchestrator python -c "import json, sys, urllib.request; request=urllib.request.Request('http://127.0.0.1:8000/admin/llm-policy', headers={'Authorization': 'Bearer ' + sys.argv[1]}); print(json.dumps(json.load(urllib.request.urlopen(request)), indent=2))" "{{ token }}"

# Ask one fresh financial question against the local stack.
ask question token="demo-token-laura-0001":
    @uv run --directory apps/orchestrator python -c "import json, sys, urllib.request; payload=json.dumps({'question': sys.argv[1]}, ensure_ascii=False).encode('utf-8'); request=urllib.request.Request('http://127.0.0.1:8000/v1/questions', data=payload, headers={'Authorization': 'Bearer ' + sys.argv[2], 'Content-Type': 'application/json; charset=utf-8'}, method='POST'); print(json.dumps(json.load(urllib.request.urlopen(request)), ensure_ascii=False, indent=2))" "{{ question }}" "{{ token }}"
# --- Quality ------------------------------------------------------------------

lint:
    uv run --directory apps/banking-api ruff check .
    uv run --directory apps/banking-api ruff format --check .
    uv run --directory apps/orchestrator ruff check .
    uv run --directory apps/orchestrator ruff format --check .
# Applies formatting and automatic fixes
format:
    uv run --directory apps/banking-api ruff format .
    uv run --directory apps/banking-api ruff check --fix .
    uv run --directory apps/orchestrator ruff format .
    uv run --directory apps/orchestrator ruff check --fix .
typecheck:
    uv run --directory apps/banking-api mypy src
    uv run --directory apps/orchestrator mypy src
# Tests for every service. The ones marked integration need `just db`
test:
    uv run --directory apps/banking-api pytest -q
    uv run --directory apps/orchestrator pytest -q
# Without a database: skips the integration tests
test-unit:
    uv run --directory apps/banking-api pytest -q -m "not integration"
    uv run --directory apps/orchestrator pytest -q -m "not integration"
# Accuracy of the query engine given a correct plan (phase 2)
# Accuracy of turning a Spanish sentence into a plan (phase 3). The model name must belong to
# the selected provider; hosted providers read their credentials from apps/orchestrator/.env.
evaluate-planning model="qwen2.5-coder:7b" provider="ollama" tpm="0":
    uv run --directory apps/orchestrator python scripts/evaluate_planning.py --provider {{ provider }} --model {{ model }} --tpm {{ tpm }}
# Local wording changes must produce local plan changes. Hosted providers need their credentials
# in apps/orchestrator/.env; tpm=8000 is what Groq's free tier publishes and the pace comes from it.
evaluate-metamorphic model="qwen3.5:9b" provider="ollama" tpm="0":
    uv run --directory apps/orchestrator python scripts/evaluate_planning.py --provider {{ provider }} --model {{ model }} --dataset metamorphic --tpm {{ tpm }}
# The whole path over HTTP: question in, figure and sentence out. Needs the compose stack
# up, because the point is to exercise the deployed service and not the handler.
# Properties rather than accuracy, so a non-zero exit is a bug and not a long tail.
evaluate-e2e token dataset="dev":
    uv run --directory apps/orchestrator python scripts/evaluate_e2e.py --token {{ token }} --dataset {{ dataset }}
# Several turns of one conversation, echoed back the way the client does it. The defects this
# catches live *between* sentences — a clarification that restarts, a follow-up that loses its
# window — and nothing single-turn can see any of them.
evaluate-conversations token:
    uv run --directory apps/orchestrator python scripts/evaluate_e2e.py --token {{ token }} --dataset conversations
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
