from __future__ import annotations

import math
import time
from typing import Any


BUCKET_SECONDS = 15
BLOCK_SECONDS = 15 * 60
SLOTS_PER_BLOCK = BLOCK_SECONDS // BUCKET_SECONDS
MAX_SURFACE_LENGTH = 64


class OperatorActivityLedger:
    """Server-clocked, de-duplicated foreground operator activity buckets.

    Clients never submit a duration or timestamp. Each accepted heartbeat marks only
    the server's current 15-second slot. Sixty slots are compacted into one 15-minute
    integer bitmask per tenant/user, so multiple tabs collapse onto the same slot and
    long-running telemetry does not create one row per heartbeat.

    This is operational activity telemetry, not payroll/timekeeping evidence. The
    server rejects client-supplied duration, but an authenticated client signal cannot
    cryptographically prove physical human attention.
    """

    def __init__(self, store, *, ensure_schema: bool = True):
        self.store = store
        if ensure_schema:
            self._init_schema()

    def _init_schema(self) -> None:
        with self.store._conn() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS operator_active_blocks(
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    block_start INTEGER NOT NULL,
                    active_slots INTEGER NOT NULL,
                    surface TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(tenant_id,user_id,block_start)
                );
                CREATE INDEX IF NOT EXISTS idx_operator_active_blocks_tenant_time
                    ON operator_active_blocks(tenant_id,block_start);
                """
            )

    def instrumented(self) -> bool:
        """Read-only schema probe used by observability surfaces."""
        with self.store._conn() as db:
            row = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='operator_active_blocks'"
            ).fetchone()
        return bool(row)

    @staticmethod
    def _surface(value: str) -> str:
        surface = str(value or "workbench").strip().lower()
        if not surface or len(surface) > MAX_SURFACE_LENGTH:
            raise ValueError("invalid operator activity surface")
        if not all(ch.isalnum() or ch in {"-", "_", "."} for ch in surface):
            raise ValueError("invalid operator activity surface")
        return surface

    @staticmethod
    def _slot(now: float) -> tuple[int, int, int]:
        timestamp = float(now)
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("invalid operator activity time")
        block_start = int(timestamp // BLOCK_SECONDS) * BLOCK_SECONDS
        slot_index = min(
            SLOTS_PER_BLOCK - 1,
            int((timestamp - block_start) // BUCKET_SECONDS),
        )
        slot_mask = 1 << slot_index
        return block_start, slot_index, slot_mask

    def record_heartbeat(
        self,
        *,
        tenant_id: str,
        user_id: str,
        surface: str = "workbench",
        now: float | None = None,
    ) -> dict[str, Any]:
        tenant = str(tenant_id).strip()
        user = str(user_id).strip()
        if not tenant or not user:
            raise ValueError("tenant_id and user_id are required")
        observed_at = float(time.time() if now is None else now)
        block_start, slot_index, slot_mask = self._slot(observed_at)
        clean_surface = self._surface(surface)
        with self.store._conn() as db:
            db.execute(
                """
                INSERT INTO operator_active_blocks(
                    tenant_id,user_id,block_start,active_slots,surface,updated_at
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(tenant_id,user_id,block_start) DO UPDATE SET
                    active_slots=operator_active_blocks.active_slots | excluded.active_slots,
                    surface=excluded.surface,
                    updated_at=excluded.updated_at
                """,
                (tenant, user, block_start, slot_mask, clean_surface, observed_at),
            )
        return {
            "recorded": True,
            "tenant_id": tenant,
            "user_id": user,
            "block_start": block_start,
            "slot_index": slot_index,
            "bucket_seconds": BUCKET_SECONDS,
            "observed_at": observed_at,
            "client_duration_accepted": False,
            "changes_authority": False,
        }

    def summarize(
        self,
        *,
        tenant_id: str,
        since: float,
        until: float,
    ) -> dict[str, Any]:
        start = float(since)
        end = float(until)
        if not math.isfinite(start) or not math.isfinite(end) or end < start:
            raise ValueError("invalid operator activity window")
        definition = (
            "server-observed 15-second foreground activity buckets; the workbench sends "
            "heartbeats only while visible, focused, and within a recent user-interaction lease; "
            "tenant+user+bucket is de-duplicated across tabs"
        )
        limitation = (
            "operational activity telemetry, not payroll/timekeeping evidence; server time and "
            "bucket duration are authoritative, but authenticated client activity signals do not "
            "cryptographically prove physical attention"
        )
        if not self.instrumented():
            return {
                "instrumented": False,
                "active_seconds": 0,
                "operator_hours": 0.0,
                "bucket_count": 0,
                "bucket_seconds": BUCKET_SECONDS,
                "active_users": 0,
                "surfaces": [],
                "daily_seconds": {},
                "definition": "operator active-time telemetry table is not installed",
                "limitation": limitation,
                "client_duration_accepted": False,
                "changes_authority": False,
            }

        start_block = int(max(0.0, start) // BLOCK_SECONDS) * BLOCK_SECONDS
        end_block = int(max(0.0, end) // BLOCK_SECONDS) * BLOCK_SECONDS
        with self.store._conn() as db:
            rows = db.execute(
                """
                SELECT user_id,block_start,active_slots,surface
                FROM operator_active_blocks
                WHERE tenant_id=? AND block_start>=? AND block_start<=?
                ORDER BY block_start,user_id
                """,
                (str(tenant_id), start_block, end_block),
            ).fetchall()

        bucket_count = 0
        active_users: set[str] = set()
        surfaces: set[str] = set()
        daily_seconds: dict[str, int] = {}
        for row in rows:
            mask = int(row["active_slots"] or 0)
            user_has_bucket = False
            for slot_index in range(SLOTS_PER_BLOCK):
                if not mask & (1 << slot_index):
                    continue
                bucket_start = int(row["block_start"]) + slot_index * BUCKET_SECONDS
                if bucket_start < start or bucket_start >= end:
                    continue
                bucket_count += 1
                user_has_bucket = True
                date = time.strftime("%Y-%m-%d", time.gmtime(bucket_start))
                daily_seconds[date] = daily_seconds.get(date, 0) + BUCKET_SECONDS
            if user_has_bucket:
                active_users.add(str(row["user_id"]))
                surfaces.add(str(row["surface"]))

        active_seconds = bucket_count * BUCKET_SECONDS
        return {
            "instrumented": True,
            "active_seconds": active_seconds,
            "operator_hours": round(active_seconds / 3600.0, 6),
            "bucket_count": bucket_count,
            "bucket_seconds": BUCKET_SECONDS,
            "active_users": len(active_users),
            "surfaces": sorted(surfaces),
            "daily_seconds": dict(sorted(daily_seconds.items())),
            "definition": definition,
            "limitation": limitation,
            "client_duration_accepted": False,
            "changes_authority": False,
        }
