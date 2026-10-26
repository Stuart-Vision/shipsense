import os
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from generate_samples import generate  # noqa: E402


@pytest.fixture(scope="session")
def sample_files(tmp_path_factory) -> dict[str, Path]:
    me, gen = generate(tmp_path_factory.mktemp("samples"))
    return {"ME-1": me, "GEN-1": gen}


@pytest.fixture
def conn():
    """A clean PostgreSQL schema per test. Skipped when TEST_DATABASE_URL isn't set."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set; skipping database tests")
    with psycopg.connect(url) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        c.commit()
        from shipsense.pipeline import init_schema
        init_schema(c)
        yield c
