"""Test-suite environment, set before anything imports the application.

`create_app()` refuses to start without a credential table, and that guard is worth
keeping: a service that starts with nobody configured 401s every request and invites
whoever debugs it to loosen something. The test suite is a deployment like any other,
so it configures its own token rather than the guard being softened to accommodate it.

conftest.py is imported before test modules, which is what makes this work — the
settings object is built on first use and cached.
"""

import os
from collections import Counter

from families import FAMILIES, UNMAPPED, family_of

# A holder and an account that exist only here. Any test needing an authenticated call
# uses this token; nothing else in the repository knows it.
TEST_TOKEN = "assistant-test-token-0001"
TEST_HOLDER = "ES-TEST"
TEST_ACCOUNT = "11111111-1111-1111-1111-111111111111"

os.environ.setdefault("ORCHESTRATOR_DEV_TOKENS", f'["{TEST_TOKEN}:{TEST_HOLDER}:{TEST_ACCOUNT}"]')


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Say which capability is limping, not only how many tests failed.

    `3070 passed` does not tell a reader what the product can do, and a red run does not
    say whether it was a window, a follow-up or a payment. This groups the run by the
    capability families of `evaluation/planning/GROUPS.md` -- the same vocabulary the
    planning report already uses -- so the two instruments can be read together.

    It costs nothing during the run: every test node is classified here, once, after the
    last one has finished. A green run spends one line, because a reader whose suite is
    green has no question to answer.
    """
    if (getattr(config.option, "quiet", 0) or 0) >= 2:
        return

    passed = Counter(family_of(r.nodeid) for r in terminalreporter.stats.get("passed", ()))
    failed = Counter(
        family_of(r.nodeid)
        for key in ("failed", "error")
        for r in terminalreporter.stats.get(key, ())
    )
    if not passed and not failed:
        return

    order = (*FAMILIES, UNMAPPED)
    mapped = sum(v for f, v in (passed + failed).items() if f != UNMAPPED)
    limping = sorted(failed, key=lambda f: (-failed[f], order.index(f) if f in order else 99))

    if not limping:
        green = sum(1 for f in FAMILIES if passed[f])
        terminalreporter.write_line(
            f"capabilities: {green}/{len(FAMILIES)} families green, "
            f"{mapped} tests mapped, {passed[UNMAPPED]} unmapped",
            green=True,
        )
        return

    terminalreporter.write_sep("-", f"capabilities limping ({len(limping)})", red=True)
    for family in limping:
        terminalreporter.write_line(
            f"  {family:<18} {failed[family]:>4} failed  {passed[family]:>5} passed"
        )
    quiet = sum(1 for f in FAMILIES if f not in failed and passed[f])
    terminalreporter.write_line(f"  ({quiet} other families green, {mapped} tests mapped)")
