"""Database sessions and forward-only, checksummed migrations."""

import hashlib
import os
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]


def connect(env="DATABASE_URL"):
    dsn = os.environ.get(env)
    if not dsn:
        raise ValueError(f"Set {env}")
    return psycopg.connect(
        dsn, autocommit=True, connect_timeout=5,
        options="-c statement_timeout=30000 -c lock_timeout=5000",
    )


def migrate(conn):
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(-1)")
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migration (
            version text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now()
        )""")
        for path in sorted((ROOT / "migrations").glob("*.sql")):
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            existing = conn.execute("SELECT sha256 FROM schema_migration WHERE version=%s",
                                    (path.name,)).fetchone()
            if existing is None:
                conn.execute(data.decode())
                conn.execute("INSERT INTO schema_migration(version, sha256) VALUES (%s,%s)",
                             (path.name, digest))
            elif existing != (digest,):
                raise ValueError(f"Applied migration changed: {path.name}")
