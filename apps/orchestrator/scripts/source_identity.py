"""Fingerprint the product and deployment files that define an evaluation build."""

import argparse
from hashlib import sha256
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[3]
SHARED_INPUTS = (
    "apps/orchestrator/scripts/source_identity.py",
    ".dockerignore",
    "justfile",
    "compose.yaml",
    "infrastructure/postgres/init",
)
INPUTS = {
    "orchestrator": (
        "apps/orchestrator/src",
        "apps/orchestrator/migrations",
        "apps/orchestrator/pyproject.toml",
        "apps/orchestrator/uv.lock",
        "apps/orchestrator/.env.defaults",
        "apps/orchestrator/config/llm.toml",
        "apps/orchestrator/Dockerfile",
    ),
    "banking": (
        "apps/banking-api/src",
        "apps/banking-api/pyproject.toml",
        "apps/banking-api/uv.lock",
        "apps/banking-api/Dockerfile",
    ),
}


def source_identity(service: str) -> str:
    if service not in INPUTS:
        raise ValueError(f"unknown service: {service}")
    digest = sha256()
    files = sorted(
        path
        for item in (*INPUTS[service], *SHARED_INPUTS)
        for root in (REPOSITORY / item,)
        for path in (root.rglob("*") if root.is_dir() else (root,))
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    )
    for path in files:
        relative = path.relative_to(REPOSITORY).as_posix().encode("utf-8")
        contents = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(contents).to_bytes(8, "big"))
        digest.update(contents)
    return digest.hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("service", choices=tuple(INPUTS))
    print(source_identity(parser.parse_args().service))
