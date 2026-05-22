"""Build apis.db from schema.sql and seed it from example_data.sql."""

from __future__ import annotations

import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("MCP_DB_PATH", os.path.join(HERE, "apis.db"))
SCHEMA_PATH = os.path.join(HERE, "schema.sql")
EXAMPLE_PATH = os.path.join(HERE, "example_data.sql")

MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS users (
    userName   TEXT PRIMARY KEY,
    acToken    TEXT,
    created_at datetime,
    updated_at datetime
);
CREATE TABLE IF NOT EXISTS user_cluster (
    userName    TEXT,
    clusterId   INTEGER,
    clusterName TEXT,
    homePath    TEXT,
    token       TEXT NOT NULL,
    created_at  datetime,
    updated_at  datetime,
    PRIMARY KEY (userName, clusterId)
);
CREATE TABLE IF NOT EXISTS cluster_url (
    clusterId   INTEGER PRIMARY KEY,
    clusterName TEXT,
    hpcUrls     TEXT,
    aiUrls      TEXT,
    efileUrls   TEXT,
    eshellUrls  TEXT
);
"""


def run_sql_file(conn: sqlite3.Connection, path: str) -> None:
    with open(path, "r", encoding="utf-8") as fh:
        conn.executescript(fh.read())


def migrate(db_path: str = DB_PATH) -> None:
    """Create new auth-related tables on an existing database."""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(MIGRATION_SQL)
        conn.commit()
    finally:
        conn.close()
    print(f"Migration complete on {db_path}")


def main(seed: bool = True) -> None:
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    try:
        run_sql_file(conn, SCHEMA_PATH)
        if seed and os.path.exists(EXAMPLE_PATH):
            run_sql_file(conn, EXAMPLE_PATH)
        conn.commit()
    finally:
        conn.close()

    print(f"Created database at {DB_PATH}")


if __name__ == "__main__":
    if "--migrate-only" in sys.argv:
        migrate()
    else:
        seed = "--no-seed" not in sys.argv
        main(seed=seed)
