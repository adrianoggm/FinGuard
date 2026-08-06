"""The `httpx2` dependency, checked instead of explained.

An automated security scanner flags it as a typosquat of `httpx` every time a new HTTP
client is added — six occasions so far, and counting a number here was itself a thing
that went stale. The answer used to live in a comment in `pyproject.toml`: prose nobody
executes, which ages badly and convinces no one. These tests assert the three facts that
comment was claiming.

The fact that settles it fastest is the first one, and there is a fourth worth knowing
that no test can hold: `import httpx` raises ModuleNotFoundError here, so the usual
suggested fix — "change it to `import httpx`" — does not make the code more standard, it
makes it not run.

They also earn their keep in the other direction: if starlette ever stops requiring
`httpx2`, the first test fails and tells us to move back to `httpx` deliberately rather
than discovering it through a deprecation warning.
"""

import importlib.metadata as metadata
import tomllib
from pathlib import Path

import starlette.testclient

LOCK = Path(__file__).parents[1] / "uv.lock"


def test_starlette_is_the_one_asking_for_httpx2() -> None:
    """The requirement is upstream's, not a choice of ours.

    This is the fact that settles the finding: we did not pick a package with a
    suspicious name, we installed what starlette declares.
    """
    requirements = metadata.requires("starlette") or []

    assert any(requirement.startswith("httpx2") for requirement in requirements), requirements


def test_the_testclient_really_imports_httpx2() -> None:
    """What the declaration promises is what the code does.

    `starlette.testclient` tries `import httpx2 as httpx` and only falls back to `httpx`
    when it is missing, so the name bound there says which package is actually in use.
    """
    assert starlette.testclient.httpx.__name__ == "httpx2"


def test_httpx2_is_pinned_by_hash_from_pypi() -> None:
    """Provenance, which is the part a name check cannot give.

    A hash in the lock plus `uv sync --frozen` means every install is byte-identical to
    what was audited. This fails if someone repoints the package at another index or
    drops the hashes.
    """
    packages = tomllib.loads(LOCK.read_text(encoding="utf-8"))["package"]
    httpx2 = next(package for package in packages if package["name"] == "httpx2")

    assert httpx2["source"] == {"registry": "https://pypi.org/simple"}
    assert httpx2["sdist"]["hash"].startswith("sha256:")
    assert httpx2["wheels"]
    assert all(wheel["hash"].startswith("sha256:") for wheel in httpx2["wheels"])
    assert all(
        wheel["url"].startswith("https://files.pythonhosted.org/") for wheel in httpx2["wheels"]
    )
