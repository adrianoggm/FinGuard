from typing import Any, Protocol, runtime_checkable

from finguard_orchestrator.domain.models.chart import Visualization


@runtime_checkable
class ChartRenderer(Protocol):
    """Turns a `Visualization` into the wire shape the client draws -- a Vega-Lite spec plus its
    rows, today, though nothing here says it always will be.

    F-5 (fix wave, A-12/Important 1): `application/metrics_executor.py` used to import
    `infrastructure.visualization.vega_lite.present` directly -- the only `application ->
    infrastructure` import in the service, and the one direction the layering exists to forbid
    (`domain/` already has the same rule; `application/` had a single, silent exception). This
    port is what `present` satisfies structurally (a plain function matches a one-method
    `Protocol` with no explicit registration needed) -- the executor asks for a `ChartRenderer`
    and the infrastructure module is wired in only where the executor is built
    (`bootstrap/main.py`), never imported by the layer that uses it.
    """

    def __call__(self, visualization: Visualization) -> dict[str, Any]: ...
