"""Every migration's revision id fits `alembic_version.version_num`, a VARCHAR(32).

A longer id passes every test that does not run the migration and then fails the upgrade on every
database with StringDataRightTruncationError, after the migration's own changes ran. It happened
twice in October 2026 (banking 0020, orchestrator 0008) before anybody upgraded a real database.
"""

import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
ALEMBIC_VERSION_LENGTH = 32


def test_every_revision_id_fits_the_alembic_version_column() -> None:
    too_long = {}
    for path in sorted(VERSIONS.glob("*.py")):
        found = re.search(r'^revision\b[^=]*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.M)
        assert found, f"{path.name} declares no revision"
        if len(found.group(1)) > ALEMBIC_VERSION_LENGTH:
            too_long[path.name] = found.group(1)
    assert too_long == {}
