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


class FakeEngine:
    """A stand-in for BridgeLink's MLLP port, for tests that don't need the real engine.

    It listens on a free localhost port and answers each framed message by calling
    respond(message, n), where n counts messages received so far from 1. respond returns
    the ACK text to send back, or None to stay silent (the sender then times out).
    Every message received is kept in .received, in order.
    """

    def __init__(self, respond):
        import socket
        import threading

        self.respond = respond
        self.received: list[str] = []
        self.connections = 0
        self._server = socket.create_server(("127.0.0.1", 0))
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        import threading

        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return  # server closed
            self.connections += 1
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        from labpipe.common.mllp import MLLPError, frame, read_frame

        buffer = bytearray()
        with conn:
            while True:
                try:
                    message = read_frame(conn, buffer)
                except (MLLPError, OSError):
                    return
                self.received.append(message)
                ack = self.respond(message, len(self.received))
                if ack is not None:
                    conn.sendall(frame(ack))

    def close(self):
        self._server.close()


def ack_for(message: str, code: str = "AA", text: str = "") -> str:
    """An HL7 ACK answering message, the way BridgeLink builds one: MSA-2 echoes MSH-10."""
    control_id = message.split("\r")[0].split("|")[9]
    msa = f"MSA|{code}|{control_id}" + (f"|{text}" if text else "")
    msh = "MSH|^~\\&|ENGINE|HOSP|LIS|LAB|20260101000000||ACK^R01^ACK|1|P|2.5.1"
    return f"{msh}\r{msa}\r"


@pytest.fixture
def fake_engine():
    """Start a FakeEngine with a given responder: fake_engine(lambda msg, n: ack_for(msg))."""
    engines = []

    def start(respond=lambda message, n: ack_for(message)):
        engine = FakeEngine(respond)
        engines.append(engine)
        return engine

    yield start
    for engine in engines:
        engine.close()
