# Monorepo tasks. Requires docker, uv and just.
# ponytail: `just` instead of Nx while there are few projects, and instead of make
# because make does not ship with Windows. Nx comes in if CI ever starts to hurt.

set shell := ["bash", "-uc"]

py_services := "apps/banking-api apps/orchestrator"

# List the available tasks
default:
    @just --list

# --- Startup ------------------------------------------------------------------

# Just the database
db:
    docker compose up -d --wait postgres

# A single component plus whatever it needs to be healthy: just component banking-api
component name:
    docker compose up -d --wait --build {{ name }}

# Skeleton: database + backend APIs. The skin (web, mobile) stays out
back:
    docker compose --profile core up -d --wait --build

# The whole PoC: skeleton + skin. Today the same as `back`, until apps/web exists
poc:
    docker compose --profile poc up -d --wait --build

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
    cd apps/banking-api && uv run alembic upgrade head

# Undoes the last migration
migrate-down:
    cd apps/banking-api && uv run alembic downgrade -1

# Creates an empty migration: just migration "contacts"
migration name:
    cd apps/banking-api && uv run alembic revision -m "{{ name }}"

# --- Development data ---------------------------------------------------------

# Loads the synthetic dataset. CAREFUL: it starts with a TRUNCATE of the ledger
seed file="0001_demo.sql":
    docker exec -i finguard-postgres-1 psql -U finguard_banking -d finguard_banking \
        -v ON_ERROR_STOP=1 -q < apps/banking-api/seeds/{{ file }}
    @just seed-verify

# Checks that what is loaded adds up, and summarises the volume
seed-verify:
    docker exec -i finguard-postgres-1 psql -U finguard_banking -d finguard_banking \
        -v ON_ERROR_STOP=1 < apps/banking-api/seeds/verify.sql

# Regenerates the dataset. Deterministic: change nothing and git diff stays empty
seed-generate:
    cd apps/banking-api && uv run python scripts/generate_seed.py

# Measures the execution accuracy of the query engine. Needs `just seed` first
evaluate:
    cd apps/banking-api && uv run python scripts/evaluate_queries.py

# Recomputes by hand the expected figures of the evaluation dataset
evaluate-expected:
    docker exec -i finguard-postgres-1 psql -U finguard_banking -d finguard_banking \
        -v ON_ERROR_STOP=1 < apps/banking-api/evaluation/expected.sql

# Installs the dependencies of every Python service
install:
    for s in {{ py_services }}; do echo "== $s"; (cd "$s" && uv sync); done

# banking-api with autoreload. Needs port 8001 free: use `just db`, not `just back`
serve-banking:
    cd apps/banking-api && uv run uvicorn finguard_banking.bootstrap.main:app --reload --port 8001

# orchestrator with autoreload. Needs port 8000 free
serve-orchestrator:
    cd apps/orchestrator && uv run uvicorn finguard_orchestrator.bootstrap.main:app --reload --port 8000

# The active LLM policy
llm-policy:
    curl -sS http://127.0.0.1:8000/admin/llm-policy

# --- Quality ------------------------------------------------------------------

lint:
    for s in {{ py_services }}; do (cd "$s" && uv run ruff check . && uv run ruff format --check .); done

# Applies formatting and automatic fixes
format:
    for s in {{ py_services }}; do (cd "$s" && uv run ruff format . && uv run ruff check --fix .); done

typecheck:
    for s in {{ py_services }}; do (cd "$s" && uv run mypy src); done

# Tests for every service. The ones marked integration need `just db`
test:
    for s in {{ py_services }}; do (cd "$s" && uv run pytest -q); done

# Without a database: skips the integration tests
test-unit:
    for s in {{ py_services }}; do (cd "$s" && uv run pytest -q -m "not integration"); done

# Accuracy of the query engine given a correct plan (phase 2)
# Accuracy of turning a Spanish sentence into a plan (phase 3). Needs a local Ollama
# daemon; the model tag is the one you want to compare.
evaluate-planning model="qwen2.5-coder:7b":
    cd apps/orchestrator && uv run python scripts/evaluate_planning.py --model {{ model }}

# The whole path over HTTP: question in, figure and sentence out. Needs the compose stack
# up, because the point is to exercise the deployed service and not the handler.
# Properties rather than accuracy, so a non-zero exit is a bug and not a long tail.
evaluate-e2e token dataset="dev":
    cd apps/orchestrator && uv run python scripts/evaluate_e2e.py --token {{ token }} --dataset {{ dataset }}

# Several turns of one conversation, echoed back the way the client does it. The defects this
# catches live *between* sentences — a clarification that restarts, a follow-up that loses its
# window — and nothing single-turn can see any of them.
evaluate-conversations token:
    cd apps/orchestrator && uv run python scripts/evaluate_e2e.py --token {{ token }} --dataset conversations

# What has to pass before pushing a commit
check: lint typecheck test

# Installs the git hook. Run once per clone; uvx needs no global install
hooks:
    uvx pre-commit install

# Runs the hooks over the whole repository, not just what is staged
hooks-all:
    uvx pre-commit run --all-files
