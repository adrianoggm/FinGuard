# Tareas del monorepo. Requiere docker, uv y just.
# ponytail: `just` en lugar de Nx mientras haya pocos proyectos, y en lugar de make
# porque make no viene en Windows. Nx entra si el CI llega a doler.

set shell := ["bash", "-uc"]

py_services := "apps/banking-api apps/assistant-api"

# Lista las tareas disponibles
default:
    @just --list

# --- Arranque -----------------------------------------------------------------

# Solo la base de datos
db:
    docker compose up -d --wait postgres

# Un componente suelto con lo que necesite para estar sano: just component banking-api
component name:
    docker compose up -d --wait --build {{ name }}

# Esqueleto: base de datos + APIs de backend. La piel (web, móvil) queda fuera
back:
    docker compose --profile core up -d --wait --build

# La PoC completa: esqueleto + piel. Hoy equivale a `back`, hasta que exista apps/web
poc:
    docker compose --profile poc up -d --wait --build

# Para todo lo levantado del proyecto; los datos sobreviven en el volumen.
# --profile "*" es imprescindible: sin él, down ignora los servicios con perfil
down:
    docker compose --profile "*" down --remove-orphans

# Borra también los datos
reset:
    docker compose --profile "*" down --remove-orphans --volumes

# Estado de los contenedores
ps:
    docker compose --profile "*" ps --all

# Logs en vivo: just logs, o just logs banking-api
logs service="":
    docker compose --profile "*" logs -f {{ service }}

# Construye las imágenes sin arrancar nada
build:
    docker compose --profile poc build

# --- Desarrollo local ---------------------------------------------------------

# Consola psql sobre la base del banking
db-shell:
    docker exec -it finguard-postgres-1 psql -U finguard_banking -d finguard_banking

# Aplica las migraciones pendientes. Solo banking-api tiene esquema propio por ahora
migrate:
    cd apps/banking-api && uv run alembic upgrade head

# Deshace la última migración
migrate-down:
    cd apps/banking-api && uv run alembic downgrade -1

# Crea una migración vacía: just migration "contactos"
migration name:
    cd apps/banking-api && uv run alembic revision -m "{{ name }}"

# Instala las dependencias de cada servicio Python
install:
    for s in {{ py_services }}; do echo "== $s"; (cd "$s" && uv sync); done

# banking-api con recarga automática. Necesita el 8001 libre: usa `just db`, no `just back`
serve-banking:
    cd apps/banking-api && uv run uvicorn finguard_banking.bootstrap.main:app --reload --port 8001

# assistant-api con recarga automática. Necesita el 8000 libre
serve-assistant:
    cd apps/assistant-api && uv run uvicorn finguard_assistant.bootstrap.main:app --reload --port 8000

# Política de LLM activa
llm-policy:
    curl -sS http://127.0.0.1:8000/admin/llm-policy

# --- Calidad ------------------------------------------------------------------

lint:
    for s in {{ py_services }}; do (cd "$s" && uv run ruff check . && uv run ruff format --check .); done

# Aplica formato y arreglos automáticos
format:
    for s in {{ py_services }}; do (cd "$s" && uv run ruff format . && uv run ruff check --fix .); done

typecheck:
    for s in {{ py_services }}; do (cd "$s" && uv run mypy src); done

# Tests de todos los servicios. Los marcados como integration necesitan `just db`
test:
    for s in {{ py_services }}; do (cd "$s" && uv run pytest -q); done

# Sin base de datos: salta los tests de integración
test-unit:
    for s in {{ py_services }}; do (cd "$s" && uv run pytest -q -m "not integration"); done

# Lo que tiene que pasar antes de subir un commit
check: lint typecheck test
