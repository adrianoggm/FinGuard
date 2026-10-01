"""Compute the voice §8 latency gate from two already-measured P95s.

The gate design describes (`docs/superpowers/specs/2026-09-03-llm-visual-planner-design.md`
and `apps/voice-gateway/evaluation/RESULTS.md` §8) is a comparison of two numbers of the *same
provider class, same machine*: a follow-up turn's P95 must not exceed the single turn's P95 by
more than a stated margin. This script does not run a model, drive the gateway, or read a file --
it takes the two P95s a person already measured (from a RESULTS.md row) as command-line numbers,
refuses to compare numbers from different provider classes, and prints pass/fail with the inputs
it used, so the gate has a computer rather than only markdown prose (M-9).

    uv run python scripts/latency_gate.py \\
        --provider-class real \\
        --single-turn-p95-ms 5076.250 \\
        --follow-up-p95-ms 5100.0 \\
        --margin-ms 250

Exit code 0 on pass, 1 on fail, 2 on a usage error (e.g. mismatched provider-class labels).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class GateResult:
    single_turn_p95_ms: float
    follow_up_p95_ms: float
    margin_ms: float
    provider_class: str

    @property
    def bound_ms(self) -> float:
        return self.single_turn_p95_ms + self.margin_ms

    @property
    def passed(self) -> bool:
        return self.follow_up_p95_ms <= self.bound_ms

    def report(self) -> str:
        verdict = "PASS" if self.passed else "FAIL"
        return (
            f"{verdict} ({self.provider_class} providers): "
            f"follow-up P95 {self.follow_up_p95_ms:.3f} ms "
            f"{'<=' if self.passed else '>'} "
            f"single-turn P95 {self.single_turn_p95_ms:.3f} ms "
            f"+ margin {self.margin_ms:.3f} ms "
            f"= bound {self.bound_ms:.3f} ms"
        )


def compute_gate(
    *,
    single_turn_p95_ms: float,
    follow_up_p95_ms: float,
    provider_class: str,
    follow_up_provider_class: str,
    margin_ms: float = 0.0,
) -> GateResult:
    """Refuses to compare two P95s measured under different provider classes -- that is the
    mistake this script exists to prevent (M-1: a real-provider number was once summed with a
    fake-provider one). Both rows must name the same class explicitly; there is no default that
    lets a caller skip stating it."""
    if provider_class != follow_up_provider_class:
        raise ValueError(
            f"provider class mismatch: single-turn={provider_class!r} "
            f"follow-up={follow_up_provider_class!r} -- these two P95s are not comparable"
        )
    if single_turn_p95_ms <= 0 or follow_up_p95_ms <= 0:
        raise ValueError("P95s must be positive milliseconds")
    if margin_ms < 0:
        raise ValueError("margin-ms must not be negative")
    return GateResult(
        single_turn_p95_ms=single_turn_p95_ms,
        follow_up_p95_ms=follow_up_p95_ms,
        margin_ms=margin_ms,
        provider_class=provider_class,
    )


def _self_check() -> None:
    """`--self-check`: no model, no file, no network -- just the arithmetic above, run against
    fixed numbers so a reviewer can confirm the gate means what this docstring says."""
    passing = compute_gate(
        single_turn_p95_ms=5076.250,
        follow_up_p95_ms=5100.0,
        provider_class="real",
        follow_up_provider_class="real",
        margin_ms=250.0,
    )
    assert passing.passed, passing.report()
    assert passing.bound_ms == 5326.250

    failing = compute_gate(
        single_turn_p95_ms=5076.250,
        follow_up_p95_ms=6000.0,
        provider_class="real",
        follow_up_provider_class="real",
        margin_ms=250.0,
    )
    assert not failing.passed, failing.report()

    try:
        compute_gate(
            single_turn_p95_ms=5076.250,
            follow_up_p95_ms=16.0,
            provider_class="real",
            follow_up_provider_class="fake",
        )
    except ValueError as exc:
        assert "provider class mismatch" in str(exc)
    else:
        raise AssertionError("mismatched provider classes must be refused, not compared")

    print("latency_gate self-check: OK (2 gate computations, 1 refused mismatch)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--single-turn-p95-ms", type=float, help="P95, single turn, from RESULTS")
    parser.add_argument("--follow-up-p95-ms", type=float, help="P95, follow-up turn, from RESULTS")
    parser.add_argument(
        "--provider-class",
        help="e.g. 'real' or 'fake' -- the single-turn row's provider class",
    )
    parser.add_argument(
        "--follow-up-provider-class",
        help="the follow-up row's provider class; defaults to --provider-class when omitted, "
        "so a caller who names only one class still gets the mismatch check for free once the "
        "two rows actually differ",
    )
    parser.add_argument(
        "--margin-ms",
        type=float,
        default=0.0,
        help="allowed extra P95 cost for the follow-up turn over the single turn (default 0: the "
        "presentation-only path must add nothing measurable)",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="run the built-in arithmetic check and exit; ignores every other argument",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.self_check:
        _self_check()
        return 0

    missing = [
        name
        for name, value in (
            ("--single-turn-p95-ms", args.single_turn_p95_ms),
            ("--follow-up-p95-ms", args.follow_up_p95_ms),
            ("--provider-class", args.provider_class),
        )
        if value is None
    ]
    if missing:
        parser.error(f"required unless --self-check: {', '.join(missing)}")

    try:
        result = compute_gate(
            single_turn_p95_ms=args.single_turn_p95_ms,
            follow_up_p95_ms=args.follow_up_p95_ms,
            provider_class=args.provider_class,
            follow_up_provider_class=args.follow_up_provider_class or args.provider_class,
            margin_ms=args.margin_ms,
        )
    except ValueError as exc:
        print(f"USAGE ERROR: {exc}", file=sys.stderr)
        return 2

    print(result.report())
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
