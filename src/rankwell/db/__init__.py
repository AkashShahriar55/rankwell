"""SQLite storage: one file, numbered migrations, WAL so the collector and the MCP server can share it."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Any

from .. import paths

Row = dict[str, Any]


class Database:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = str(path or paths.db_path())
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 30000")
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        migrate(self.conn)

    def close(self) -> None:
        self.conn.close()

    def all(self, sql: str, params: Any = ()) -> list[Row]:
        return [dict(r) for r in self.conn.execute(sql, params)]

    def one(self, sql: str, params: Any = ()) -> Row | None:
        r = self.conn.execute(sql, params).fetchone()
        return dict(r) if r else None

    def value(self, sql: str, params: Any = ()) -> Any:
        r = self.conn.execute(sql, params).fetchone()
        return r[0] if r else None

    def execute(self, sql: str, params: Any = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def upsert(self, table: str, rows: list[Row], keys: list[str]) -> int:
        """Insert rows; on a key conflict update every non-key column. Returns the number of rows."""
        if not rows:
            return 0
        cols = list(rows[0])
        updates = [c for c in cols if c not in keys]
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
        sql += f" ON CONFLICT ({', '.join(keys)}) DO " + (
            "UPDATE SET " + ", ".join(f"{c} = excluded.{c}" for c in updates) if updates else "NOTHING"
        )
        with self.tx():
            self.conn.executemany(sql, [[_cell(r.get(c)) for c in cols] for r in rows])
        return len(rows)

    @contextmanager
    def tx(self) -> Iterator[None]:
        """A transaction; nested calls join the outer one."""
        if self.conn.in_transaction:
            yield
            return
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def backup(self, dest: Path) -> Path:
        out = sqlite3.connect(str(dest))
        with out:
            self.conn.backup(out)
        out.close()
        return dest


def _cell(v: Any) -> Any:
    return json.dumps(v, ensure_ascii=False) if isinstance(v, list | dict) else v


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply pending migrations in order. Returns the names applied."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    files = sorted(
        (f for f in resources.files("rankwell.db.migrations").iterdir() if f.name.endswith(".sql")),
        key=lambda f: f.name,
    )
    applied = []
    for f in files:
        if f.name in done:
            continue
        try:
            conn.executescript("BEGIN;\n" + f.read_text() + "\nINSERT INTO schema_migrations VALUES ('"
                               + f.name + "', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));\nCOMMIT;")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied.append(f.name)
    return applied


_default: Database | None = None


def get() -> Database:
    """The process-wide database at the default path."""
    global _default
    if _default is None:
        _default = Database()
    return _default
