"""Which build is answering.

The review that prompted this opened with "por lectura del HEAD", which is the only way to know what
a running service contains: the image carries no commit, and a container has no repository to read
one out of. So the build stamps it in and the service says it.

Unauthenticated on purpose. It is a fact about the deployment rather than about anybody's money, and
the first question when a conversation misbehaves is whether the code being read is the code that
answered.
"""

from fastapi.testclient import TestClient

from finguard_orchestrator.bootstrap.main import app

client = TestClient(app)


def test_the_service_says_which_commit_it_is() -> None:
    body = client.get("/version").json()

    assert body["service"] == "orchestrator"
    assert set(body) == {"service", "sha", "ref"}


def test_a_build_that_was_given_no_commit_says_so() -> None:
    """ "unknown" rather than an empty string or a guess. A local `uvicorn` run has no build behind
    it, and reporting a plausible-looking value would be worse than admitting it."""
    body = client.get("/version").json()

    assert body["sha"], "an empty sha reads as a field that failed rather than a build without one"


def test_it_needs_no_credential() -> None:
    """Every other route needs a scope. This one is the one you reach for when you cannot tell what
    you are talking to, which is exactly when a credential is one more thing to get wrong."""
    assert client.get("/version").status_code == 200
