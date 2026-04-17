"""SQLite cost ledger for LLM call accounting and budget enforcement.

Thread-safe: all writes go through a threading.Lock and SQLite WAL mode.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_calls (
    call_id     TEXT PRIMARY KEY,
    stage       TEXT NOT NULL,
    model_role  TEXT NOT NULL,
    model_id    TEXT NOT NULL,
    provider    TEXT NOT NULL,
    tokens_in   INTEGER NOT NULL,
    tokens_out  INTEGER NOT NULL,
    usd         REAL NOT NULL,
    latency_ms  INTEGER NOT NULL,
    chunk_uuids TEXT,
    success     INTEGER NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_stage ON llm_calls (stage);
CREATE INDEX IF NOT EXISTS idx_llm_calls_created_at ON llm_calls (created_at);

CREATE TABLE IF NOT EXISTS budget_events (
    event_id    TEXT PRIMARY KEY,
    event_type  TEXT NOT NULL,
    cost_so_far REAL NOT NULL,
    cost_cap    REAL NOT NULL,
    created_at  TEXT NOT NULL
);
"""


class CostLedger:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")

    @contextmanager
    def _connect(self) -> Iterable[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def record_call(
        self,
        *,
        stage: str,
        model_role: str,
        model_id: str,
        provider: str,
        tokens_in: int,
        tokens_out: int,
        usd: float,
        latency_ms: int,
        chunk_uuids: list[UUID] | None = None,
        success: bool = True,
        retry_count: int = 0,
    ) -> UUID:
        call_id = uuid4()
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO llm_calls
                (call_id, stage, model_role, model_id, provider, tokens_in, tokens_out,
                 usd, latency_ms, chunk_uuids, success, retry_count, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(call_id),
                    stage,
                    model_role,
                    model_id,
                    provider,
                    tokens_in,
                    tokens_out,
                    usd,
                    latency_ms,
                    json.dumps([str(u) for u in chunk_uuids]) if chunk_uuids else None,
                    1 if success else 0,
                    retry_count,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return call_id

    def total_usd(self) -> float:
        with self._connect() as conn:
            row = conn.execute("SELECT COALESCE(SUM(usd), 0.0) FROM llm_calls").fetchone()
        return float(row[0])

    def by_stage(self) -> dict[str, float]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT stage, COALESCE(SUM(usd), 0.0) FROM llm_calls GROUP BY stage"
            ).fetchall()
        return {r[0]: float(r[1]) for r in rows}

    def by_model_role(self) -> dict[str, float]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT model_role, COALESCE(SUM(usd), 0.0) FROM llm_calls GROUP BY model_role"
            ).fetchall()
        return {r[0]: float(r[1]) for r in rows}

    def total_tokens(self) -> tuple[int, int]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0) FROM llm_calls"
            ).fetchone()
        return int(row[0]), int(row[1])

    def record_budget_event(self, event_type: str, cost_so_far: float, cost_cap: float) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO budget_events
                (event_id, event_type, cost_so_far, cost_cap, created_at)
                VALUES (?,?,?,?,?)""",
                (
                    str(uuid4()),
                    event_type,
                    cost_so_far,
                    cost_cap,
                    datetime.now(UTC).isoformat(),
                ),
            )
