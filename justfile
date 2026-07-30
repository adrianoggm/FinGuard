# Tareas del monorepo. Requiere docker, uv y just.
# ponytail: `just` en lugar de Nx mientras haya pocos proyectos, y en lugar de make
# porque make no viene en Windows. Nx entra si el CI llega a doler.

set shell := ["bash", "-uc"]

py_services := "apps/banking-api"

# Lista las tareas disponibles
default:
    @just --list

# Levanta el perfil core: PostgreSQL en 127.0.0.1:5442
up:
    docker compose --profile core up -d --wait

# Para el perfil core; los datos sobreviven en el volumen
down:
    docker compose --profile core down

# Consola psql sobre la base del banking
db-shell:
    docker exec -it finguard-postgres-1 psql -U finguard_banking -d finguard_banking

# Instala las dependencias de cada servicio Python
install:
    for s in {{ py_services }}; do echo "== $s"; (cd "$s" && uv sync); done

# banking-api con recarga automática en el puerto 8001
serve-banking:
    cd apps/banking-api && uv run uvicorn finguard_banking.bootstrap.main:app --reload --port 8001

lint:
    for s in {{ py_services }}; do (cd "$s" && uv run ruff check . && uv run ruff format --check .); done

# Aplica formato y arreglos automáticos
format:
    for s in {{ py_services }}; do (cd "$s" && uv run ruff format . && uv run ruff check --fix .); done

typecheck:
    for s in {{ py_services }}; do (cd "$s" && uv run mypy src); done

# Tests de todos los servicios. Los marcados como integration necesitan `just up`
test:
    for s in {{ py_services }}; do (cd "$s" && uv run pytest -q); done

# Sin base de datos: salta los tests de integración
test-unit:
    for s in {{ py_services }}; do (cd "$s" && uv run pytest -q -m "not integration"); done

# Lo que tiene que pasar antes de subir un commit
check: lint typecheck test
