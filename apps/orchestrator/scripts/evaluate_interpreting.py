"""Measures how well a model reads a turn, against the rules that read it today.

    uv run python scripts/evaluate_interpreting.py --model qwen3.5:4b
    uv run python scripts/evaluate_interpreting.py --provider xai --model grok-4-fast

The number that decides whether the migration in ADR-0xx is worth having. The rules get 100 % by
definition — they are the reference — so this is not "is the model right", it is "where does the
model differ, and is the difference an improvement or a regression".

That is why the confusion table is the output and the headline rate is nearly decoration. "94 %"
tells you nothing you can act on; `ACCOUNT_BALANCE → FINANCIAL_QUERY ×31` tells you the model does
not think a bare account name is about the balance, which is one line of prompt or one reason to
stop. Read the pairs.

**Two of them are not like the others.** A turn the rules call PAYMENT and the model does not is a
payment that would have become a question — recoverable, the user repeats themselves. The reverse
is the model volunteering to move money, and `Promoted` refuses to let it, so a large number there
is a model this cannot be flipped to whatever the overall rate says. Both are reported apart from
the rest for that reason.

The corpus is replayed through the real service to rebuild the conversation state, because half of
what decides a turn is where the conversation was. That makes a run take minutes rather than
seconds, and it is the only version of this that measures the right thing.

It also means fewer turns are scored than the corpus has. A preview owns the next turn and a
half-built payment takes the reply that answers its question, both before anything is classified,
so those are questions no interpreter is ever asked. Scoring them anyway counted a model as having
invented 47 payments for reading "sí" and "20 euros" — in the middle of a Bizum — as being about
the Bizum.
"""

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Any

# A Windows console is cp1252 and this table has arrows in it. Said here rather than avoided in the
# output: the corpus is Spanish, so a report that cannot print an accent is a report of the wrong
# thing on the machine the corpus was written on.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

# The service logs a line per turn and httpx logs two, which is 2 600 lines under a table that is
# twenty. The run itself is unchanged — this only silences the transcript of it.
logging.disable(logging.INFO)

TESTS = Path(__file__).resolve().parents[1] / "tests"
sys.path.insert(0, str(TESTS))

# The corpus harness replays through the real application, and `create_app()` refuses to start
# without a credential table. `conftest` is what configures one for the suite; importing it here
# is how this script becomes the same kind of deployment, rather than the guard being softened.
# It has to happen before `episodes` reaches `bootstrap.main`.
import conftest  # noqa: E402, F401
from support import episodes  # noqa: E402

from finguard_orchestrator.bootstrap.config import get_settings  # noqa: E402
from finguard_orchestrator.domain.services.intent_router import Intent  # noqa: E402
from finguard_orchestrator.domain.services.turn_interpreters import Deterministic  # noqa: E402
from finguard_orchestrator.infrastructure.interpreting.llm_turn_interpreter import (  # noqa: E402
    LlmTurnInterpreter,
)
from finguard_orchestrator.infrastructure.llm.ollama_llm_provider import (  # noqa: E402
    OllamaLlmProvider,
)
from finguard_orchestrator.infrastructure.llm.xai_llm_provider import (  # noqa: E402
    XaiLlmProvider,
)


def _table(report: episodes.Agreement, limit: int) -> str:
    lines = [
        # Classified turns, not corpus turns. A preview and a half-built payment own their next turn
        # before anything is read, so those are not questions this interpreter was ever asked.
        f"turns classified {report.turns}",
        f"agreed           {report.agreed}  ({report.rate} %)",
        f"differed         {report.turns - report.agreed}",
        "",
    ]

    volunteered = sum(
        count for (rules, model), count in report.confusions.items() if model == Intent.PAYMENT
    )
    dropped = sum(
        count for (rules, model), count in report.confusions.items() if rules == Intent.PAYMENT
    )
    lines += [
        "about money",
        f"  payments the model invented   {volunteered}   (refused by Promoted)",
        f"  payments the model dropped    {dropped}   (refused by Promoted)",
        "",
        f"where they differ, worst {limit}",
    ]
    for pair, count in report.worst(limit):
        rules, model = pair
        lines.append(f"  {rules:<20} → {model:<20} {count}")
        # The sentences, indented under their pair. A count tells you a disagreement exists; these
        # tell you whether it is one rule misfiring on a phrasing or a model guessing at random,
        # and those are opposite conclusions from the same number.
        lines += [f"      {said}" for said in report.examples.get(pair, ())]
    if not report.confusions:
        lines.append("  (none)")
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="a model name the provider serves")
    parser.add_argument(
        "--provider",
        default="ollama",
        choices=["ollama", "xai"],
        help="ollama runs locally; xai needs ORCHESTRATOR_XAI_API_KEY and sends every corpus "
        "sentence over the internet",
    )
    parser.add_argument("--base-url", default=None, help="overrides the provider's own default")
    parser.add_argument(
        "--episodes",
        type=int,
        default=0,
        help="only the first N, for a quick look. 0 means all of them.",
    )
    parser.add_argument("--worst", type=int, default=12, help="how many confusion pairs to print")
    args = parser.parse_args()

    corpus = episodes.load()
    if args.episodes:
        corpus = corpus[: args.episodes]
        print(f"# only the first {len(corpus)} episodes — this is a sample, not a result")

    settings = get_settings()
    provider: Any
    if args.provider == "xai":
        if settings.xai_api_key is None:
            print("ORCHESTRATOR_XAI_API_KEY is not set — see .env.defaults", file=sys.stderr)
            return 2
        # Said out loud rather than assumed. The corpus is written to look like a person's bank
        # questions, and this sends every sentence in it to a third party.
        print(f"# {len(corpus)} episodes going to {args.provider}. They leave this machine.")
        provider = XaiLlmProvider(
            api_key=settings.xai_api_key.get_secret_value(),
            base_url=args.base_url or settings.xai_base_url,
            timeout_seconds=settings.xai_timeout_seconds,
        )
    else:
        provider = OllamaLlmProvider(
            base_url=args.base_url or settings.ollama_base_url,
            keep_alive=settings.ollama_keep_alive,
            timeout_seconds=settings.ollama_timeout_seconds,
            context_tokens=settings.ollama_context_tokens,
            # Fixed, like everywhere else. A classifier that answers differently on a rerun is
            # one whose measurement cannot be repeated, and a measurement nobody can repeat is
            # an opinion. The hosted provider offers no equivalent, which is a real difference
            # between the two rows of any table this produces.
            seed=settings.ollama_seed,
        )
    if not await provider.ping():
        print(f"{args.provider} did not answer", file=sys.stderr)
        return 2

    started = time.monotonic()
    report = await episodes.compare(
        corpus, LlmTurnInterpreter(provider, model=args.model), Deterministic()
    )
    elapsed = time.monotonic() - started

    said = sum(len(episode.turns) for episode in corpus)
    print(f"\n{args.model} vs the deterministic rules — {len(corpus)} episodes, {said} turns\n")
    print(_table(report, args.worst))
    per_turn = elapsed / report.turns if report.turns else 0
    print(f"\n{elapsed:.0f} s total, {per_turn * 1000:.0f} ms per turn")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
