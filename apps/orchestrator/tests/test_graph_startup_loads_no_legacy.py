"""Graph mode starts without the legacy conversation coordinator: a runtime check, not a reading.

The legacy runtime audit (docs/architecture/langgraph-legacy-runtime-audit.md) shows from imports
that graph composition never loads the old handler, readers or question controller, and says that
still needs a runtime assertion. This is it: `create_app()` in a fresh interpreter, graph on, then
ask `sys.modules` and the mounted routes. A fresh process because the suite itself imports the
legacy modules, so this process's `sys.modules` proves nothing.

Retiring a module from the legacy list below is only safe while this passes; a new import of one
of them from graph composition fails here before it reaches the deletion.
"""

import json
import os
import subprocess
import sys
from typing import Any

# Legacy-only per the audit's dependency inventory: the old coordinator, its lexical readers and
# rules, its planner and payment reader, and the controller that mounts them.
LEGACY = (
    "finguard_orchestrator.application.queries.ask_financial_question",
    "finguard_orchestrator.application.queries.readers",
    "finguard_orchestrator.domain.services.explicit_facts",
    "finguard_orchestrator.domain.services.explicit_lexicon",
    "finguard_orchestrator.domain.services.intent_lexicon",
    "finguard_orchestrator.domain.services.turn_interpreters",
    "finguard_orchestrator.infrastructure.controllers.llm_controller",
    "finguard_orchestrator.infrastructure.payments.llm_payment_reader",
    "finguard_orchestrator.infrastructure.planning.llm_query_planner",
)

_PROBE = """
import json, sys
from finguard_orchestrator.bootstrap.main import create_app
app = create_app()
legacy = json.loads(sys.argv[1])

def endpoints(routes):
    # FastAPI 0.141 keeps an included router whole (`_IncludedRouter`) instead of copying its
    # routes into the app, so `app.routes` alone never shows a controller's endpoints.
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from endpoints(inner.routes)
        elif getattr(route, "endpoint", None) is not None:
            yield route

mounted = list(endpoints(app.routes))
print(json.dumps({
    "loaded": sorted(name for name in legacy if name in sys.modules),
    "routes": sorted(route.path for route in mounted if route.endpoint.__module__ in legacy),
    "mounted": len(mounted),
}))
"""


def _started_in_graph_mode() -> dict[str, Any]:
    env = {
        **os.environ,
        "ORCHESTRATOR_CONVERSATION_ENGINE": "model",
        "ORCHESTRATOR_CONVERSATION_GRAPH": "on",
        # Only checked for presence: the pool is opened by the lifespan, which never runs here.
        "ORCHESTRATOR_DATABASE_URL": "postgresql+asyncpg://probe:probe@127.0.0.1:1/probe",
    }
    done = subprocess.run(
        [sys.executable, "-c", _PROBE, json.dumps(LEGACY)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_graph_mode_starts_without_loading_or_mounting_the_legacy_coordinator() -> None:
    started = _started_in_graph_mode()

    # The walk saw the app's routes; an empty legacy list from a blind walk proves nothing.
    assert started["mounted"] > 10
    assert started["loaded"] == []
    assert started["routes"] == []
