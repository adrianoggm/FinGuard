"""Resource budgets shared by graph execution and the HTTP provider boundary."""

from finguard_orchestrator.domain.errors import DomainError

# Total model-generation units available to one conversational turn. A hosted provider request,
# retry or failover attempt consumes one unit; a local provider completion consumes one unit.
MAX_MODEL_CALLS = 3
MAX_BANK_EXECUTIONS = 3


class ReadBudgetExhaustedError(DomainError):
    """The turn reached its cap on banking calls; failed attempts count toward it."""

    code = "read_budget_exhausted"
