"""Shared pytest fixtures."""

import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from labpipe.common.migrate import apply_migrations
from labpipe.common.vocab import load_vocabulary

ROOT = Path(__file__).resolve().parent.parent


@contextmanager
def throwaway_database():
    """Create an empty database, yield its URL, then drop it.

    Needs DATABASE_URL pointing at a Postgres server where the user may create
    databases. Tests that use it are skipped when DATABASE_URL is not set.
    """
    base_url = os.environ.get("DATABASE_URL")
    if not base_url:
        pytest.skip("DATABASE_URL not set; start Postgres with: docker compose up -d db")

    name = f"labpipe_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(base_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield psycopg.conninfo.make_conninfo(base_url, dbname=name)
    finally:
        with psycopg.connect(base_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def fresh_db_url():
    """An empty throwaway database for one test."""
    with throwaway_database() as url:
        yield url


@pytest.fixture(scope="module")
def module_db_url():
    """An empty throwaway database shared by every test in one module.

    For slow setups (the whole sample through both hops) that several read-only tests
    check from different angles. Tests that use it must not change its rows.
    """
    with throwaway_database() as url:
        yield url


@pytest.fixture
def staging_db(fresh_db_url):
    """An open connection to a fresh database with every migration applied."""
    with psycopg.connect(fresh_db_url) as conn:
        apply_migrations(conn, ROOT / "db" / "migrations")
        yield conn


@pytest.fixture
def omop_db(staging_db):
    """staging_db plus the committed vocabulary subset, ready for Hop 2."""
    load_vocabulary(staging_db, ROOT / "db" / "vocab_subset")
    return staging_db
