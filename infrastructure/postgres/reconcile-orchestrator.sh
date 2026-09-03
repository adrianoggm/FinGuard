#!/bin/sh
set -eu

# Init scripts run only when the PostgreSQL volume is first created. Older FinGuard volumes used
# the name finguard_assistant; create the current service identity non-destructively before Alembic
# connects. On a new/current volume both checks are no-ops.
if [ "$(psql -h postgres -U finguard -d postgres -tAc \
  "SELECT 1 FROM pg_roles WHERE rolname = 'finguard_orchestrator'")" != "1" ]; then
  psql -h postgres -U finguard -d postgres -v ON_ERROR_STOP=1 \
    -c "CREATE ROLE finguard_orchestrator LOGIN PASSWORD 'finguard_orchestrator'"
fi

if [ "$(psql -h postgres -U finguard -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname = 'finguard_orchestrator'")" != "1" ]; then
  createdb -h postgres -U finguard -O finguard_orchestrator finguard_orchestrator
  psql -h postgres -U finguard -d postgres -v ON_ERROR_STOP=1 \
    -c "REVOKE ALL ON DATABASE finguard_orchestrator FROM PUBLIC"
fi
