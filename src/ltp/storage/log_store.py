"""
SQLite-backed persistent store for CommitmentLog records.

Persists record data as JSON so that the in-memory MerkleLog can be
reconstructed on restart by replaying records in chain order.  Also
persists the log operator keypair (vk/sk) so the same signing identity
is used across restarts.

Thread-safe via a per-instance lock (same pattern as SQLiteShardStore).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Optional

__all__ = ["CommitmentLogStore"]


class CommitmentLogStore:
    """SQLite backend for CommitmentLog persistence.

    Stores:
      1. Ordered record entries as JSON (all CommitmentRecord fields)
      2. Operator keypair (vk, sk) for consistent MerkleLog signing

    On startup, CommitmentLog loads all records in chain order and replays
    them into a fresh MerkleLog to reconstruct the Merkle tree + STHs.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")  # crash-safe for append-only log
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS log_records (
                chain_index  INTEGER PRIMARY KEY,
                entity_id    TEXT    NOT NULL UNIQUE,
                leaf_index   INTEGER NOT NULL,
                record_json  TEXT    NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS log_operator (
                id  INTEGER PRIMARY KEY CHECK (id = 1),
                vk  BLOB NOT NULL,
                sk  BLOB NOT NULL
            )
        """)
        self._conn.commit()

    def store_operator_keypair(self, vk: bytes, sk: bytes) -> None:
        """Persist the log operator keypair (upsert)."""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO log_operator (id, vk, sk) VALUES (1, ?, ?)",
                (vk, sk),
            )
            self._conn.commit()

    def load_operator_keypair(self) -> Optional[tuple[bytes, bytes]]:
        """Load persisted operator keypair. Returns (vk, sk) or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT vk, sk FROM log_operator WHERE id = 1"
            ).fetchone()
        if row is None:
            return None
        return (row[0], row[1])

    def append_record(
        self,
        chain_index: int,
        entity_id: str,
        leaf_index: int,
        record_dict: dict,
    ) -> None:
        """Persist a new log record as JSON."""
        record_json = json.dumps(record_dict, sort_keys=True, separators=(",", ":"))
        with self._lock:
            self._conn.execute(
                "INSERT INTO log_records (chain_index, entity_id, leaf_index, record_json) "
                "VALUES (?, ?, ?, ?)",
                (chain_index, entity_id, leaf_index, record_json),
            )
            self._conn.commit()

    def load_all_records(self) -> list[tuple[str, int, dict]]:
        """Load all records ordered by chain_index.

        Returns list of (entity_id, leaf_index, record_dict).
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT entity_id, leaf_index, record_json "
                "FROM log_records ORDER BY chain_index"
            ).fetchall()
        return [(r[0], r[1], json.loads(r[2])) for r in rows]

    @property
    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM log_records").fetchone()
        return row[0]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __repr__(self) -> str:
        return f"CommitmentLogStore(db={self._db_path!r}, count={self.count})"
