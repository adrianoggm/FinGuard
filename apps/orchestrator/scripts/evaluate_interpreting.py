"""Measures how well a model reads a turn, against the rules that read it today.

    uv run python scripts/evaluate_interpreting.py --model qwen3.5:4b
    uv run python scripts/evaluate_interpreting.py --provider groq --model openai/gpt-oss-120b
    uv run python scripts/evaluate_interpreting.py --provider cloudflare \
        --model @cf/qwen/qwen3-30b-a3b-fp8

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

**A hosted endpoint rate-limits, and the fallback hides it.** `LlmTurnInterpreter` answers
FINANCIAL_QUERY whenever a provider fails, which is right in production — a model outage must not
break a conversation — and turns a 429 into an opinion here. The first run against Groq produced a
plausible confusion table in which every row was an HTTP 429. The failures are now counted and
reported above the table, and `--tpm` paces the run.

Groq's free tier, for `openai/gpt-oss-120b`: 30 requests/minute, **8K tokens/minute**, 200K
tokens/day. One turn of this corpus costs about 750 tokens — roughly 500 of prompt and 224 of
reasoning the model does before answering — so the token limit binds first:

    8000 / 750   ->  ten turns a minute, not the thirty the request limit allows
    200000 / 750 ->  about 260 turns a day, so the full 818 does not fit in one

That first division is `--tpm`'s whole job. It used to be written here as `--rpm 10`, which is the
right answer to an arithmetic nobody re-does when the prompt grows.

Which means a full run is a three-day job on the free tier, and a 20-episode sample is the honest
unit of measurement. That sample was within 0.2 points of the full run for qwen3:8b.

None of that arithmetic transfers to `--provider cloudflare`: Workers AI meters in *neurons* per
day rather than tokens per minute, so what a turn of this corpus costs there is unmeasured and
no tokens-a-minute figure describes it. Watch the failure count above the table on the first run.

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
from finguard_orchestrator.infrastructure.llm.cloudflare_workers_ai import (  # noqa: E402
    cloudflare_workers_ai,
)
from finguard_orchestrator.infrastructure.llm.gemini import GeminiProvider  # noqa: E402
from finguard_orchestrator.infrastructure.llm.ollama_llm_provider import (  # noqa: E402
    OllamaLlmProvider,
)
from finguard_orchestrator.infrastructure.llm.openai_compatible_provider import (  # noqa: E402
    OpenAiCompatibleProvider,
)

# What one turn costs a tokens-a-minute budget: about 500 of prompt and 224 the model reasons before
# answering. Measured and not derived — the prompt lives inside `LlmTurnInterpreter` and this file
# never builds it — so this is the number to re-measure if that prompt grows. What `--tpm` buys is
# that the division stops being done by hand: the header used to carry `8000 / 750 -> --rpm 10`, and
# a remembered 10 is right only for the prompt it was worked out against.
TURN_TOKENS = 750


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
        choices=["ollama", "groq", "gemini", "cloudflare"],
        help="ollama runs locally; hosted providers need credentials in .env and send every "
        "corpus sentence over the internet",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        # For cloudflare this is the API root before /accounts/{id}/…, because the account id is
        # configuration and the URL is assembled from both. Overriding the assembled URL instead
        # would let a run silently measure a different account than the one configured.
        help="overrides the provider's own default; for cloudflare, the API root",
    )
    parser.add_argument(
        "--episodes",
        type=int,
        default=0,
        help="only the first N, for a quick look. 0 means all of them.",
    )
    parser.add_argument("--worst", type=int, default=12, help="how many confusion pairs to print")
    parser.add_argument(
        "--tpm",
        type=int,
        default=0,
        help="the provider's tokens-a-minute limit; the pace comes from it. 0 means as fast as "
        "it goes. Groq's free tier publishes 8000",
    )
    args = parser.parse_args()

    corpus = episodes.load()
    if args.episodes:
        corpus = corpus[: args.episodes]
        print(f"# only the first {len(corpus)} episodes — this is a sample, not a result")

    settings = get_settings()
    provider: Any
    if args.provider == "groq":
        if settings.groq_api_key is None:
            print("ORCHESTRATOR_GROQ_API_KEY is not set — see .env.defaults", file=sys.stderr)
            return 2
        provider = OpenAiCompatibleProvider(
            name=args.provider,
            api_key=settings.groq_api_key.get_secret_value(),
            base_url=args.base_url or settings.groq_base_url,
            timeout_seconds=settings.groq_timeout_seconds,
            reasoning_headroom=settings.groq_reasoning_headroom,
        )
    elif args.provider == "gemini":
        if settings.gemini_api_key is None:
            print("ORCHESTRATOR_GEMINI_API_KEY is not set - see .env.defaults", file=sys.stderr)
            return 2
        provider = GeminiProvider(
            api_key=settings.gemini_api_key.get_secret_value(),
            base_url=args.base_url or settings.gemini_base_url,
            timeout_seconds=settings.gemini_timeout_seconds,
        )
    elif args.provider == "cloudflare":
        # Both halves, the same rule the composition root applies: the account id is not a
        # secret and is just as required, because it is the path every request is posted to.
        if settings.cloudflare_api_token is None or settings.cloudflare_account_id is None:
            print(
                "ORCHESTRATOR_CLOUDFLARE_API_TOKEN and ORCHESTRATOR_CLOUDFLARE_ACCOUNT_ID must "
                "both be set — see .env.defaults",
                file=sys.stderr,
            )
            return 2
        provider = cloudflare_workers_ai(
            api_token=settings.cloudflare_api_token.get_secret_value(),
            account_id=settings.cloudflare_account_id,
            api_base=args.base_url or settings.cloudflare_api_base,
            timeout_seconds=settings.cloudflare_timeout_seconds,
            reasoning_headroom=settings.cloudflare_reasoning_headroom,
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
    if args.provider != "ollama":
        # Said out loud rather than assumed, and after the provider is built so it is only said
        # when a run is actually going to happen. The corpus is written to look like a person's
        # bank questions, and this sends every sentence in it to a third party.
        print(f"# {len(corpus)} episodes going to {args.provider}. They leave this machine.")
    if not await provider.ping():
        print(f"{args.provider} did not answer", file=sys.stderr)
        return 2

    interpreter = LlmTurnInterpreter(
        provider,
        model=args.model,
        pace_seconds=60.0 * TURN_TOKENS / args.tpm if args.tpm else 0.0,
    )
    started = time.monotonic()
    report = await episodes.compare(corpus, interpreter, Deterministic())
    elapsed = time.monotonic() - started

    # Before the table, and loudly, because the fallback is silent by design: every failed call
    # was scored as FINANCIAL_QUERY. The first run against Groq produced a plausible-looking
    # confusion table in which every row was an HTTP 429, and nothing in the output said so.
    lost = interpreter.unavailable + interpreter.unreadable
    if lost:
        share = 100 * lost / report.turns if report.turns else 0
        print(
            f"\n!! {lost} of {report.turns} turns ({share:.0f} %) never reached the model: "
            f"{interpreter.unavailable} provider failures, {interpreter.unreadable} unreadable "
            "answers. Each was scored as FINANCIAL_QUERY, so the table below is not a "
            "measurement of this model.",
            file=sys.stderr,
        )
        if share > 5:
            print(
                "   Rate limited? See --tpm; on Groq's free tier 8000 is the number.",
                file=sys.stderr,
            )

    said = sum(len(episode.turns) for episode in corpus)
    print(
        f"\n{args.provider}/{args.model} vs the deterministic rules "
        f"— {len(corpus)} episodes, {said} turns\n"
    )
    print(_table(report, args.worst))
    per_turn = elapsed / report.turns if report.turns else 0
    print(f"\n{elapsed:.0f} s total, {per_turn * 1000:.0f} ms per turn")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
