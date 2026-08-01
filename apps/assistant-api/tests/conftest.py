"""Test-suite environment, set before anything imports the application.

`create_app()` refuses to start without a credential table, and that guard is worth
keeping: a service that starts with nobody configured 401s every request and invites
whoever debugs it to loosen something. The test suite is a deployment like any other,
so it configures its own token rather than the guard being softened to accommodate it.

conftest.py is imported before test modules, which is what makes this work — the
settings object is built on first use and cached.
"""

import os

# A holder and an account that exist only here. Any test needing an authenticated call
# uses this token; nothing else in the repository knows it.
TEST_TOKEN = "assistant-test-token-0001"
TEST_HOLDER = "ES-TEST"
TEST_ACCOUNT = "11111111-1111-1111-1111-111111111111"

os.environ.setdefault("ASSISTANT_DEV_TOKENS", f'["{TEST_TOKEN}:{TEST_HOLDER}:{TEST_ACCOUNT}"]')
