from __future__ import annotations

import math
import time
from typing import Any


BUCKET_SECONDS = 15
BLOCK_SECONDS = 15 * 60
SLOTS_PER_BLOCK = BLOCK_SECONDS // BUCKET_SECONDS
MAX_SURFACE_LENGTH = 64
RETENTION_DAYS = 90
RETENTION_SECONDS = RETENTION_DAYS * 24 * 60 * 60
PRUNE_INTERVAL_SECONDS = 24 * 60 * 60
_ALL_SLOTS_MASK = (1 << SLOTS_PER_BLOCK) - 1


class OperatorActivityLedger:
    """Server-clocked, de-duplicated foreground operator activity buckets.

    Clients never submit a duration or timestamp. Each accepted heartbeat marks only
    the server's current 15-second slot. Sixty slots are compacted into one 15-minute
    integer bitmask per tenant/user, so multiple tabs collapse onto the same slot and
    long-running telemetry does not create one row per heartbeat.

    Duplicate heartbeats for an already-recorded slot are write-suppressed. A rolling
    90-day server retention policy prunes expired telemetry at most once per process
    day; the retention decision is maintenance-only and never depends on client time.

    This is operational activity telemetry, not payroll/timekeeping evidence. The
    server rejects client-supplied duration, but an authenticated client signal cannot
    cryptographically prove physical human attention.
    """

    def __init__(self, store, *, ensure_schema: bool = True):
        self.store = store
        self._last_prune_at: float | None = None
        if ensure_schema:
            self._init_schema()

    def _init_schema(self) -> None:
        with self.store._conn() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS operator_activity_meta(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    measurement_started_at REAL NOT NULL,
                    bucket_seconds INTEGER NOT NULL
                );
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
                CREATE INDEX IF NOT EXISTS idx_operator_active_blocks_time
                    ON operator_active_blocks(block_start);
                """
            )
            db.execute(
                """
                INSERT OR IGNORE INTO operator_activity_meta(
                    singleton,measurement_started_at,bucket_seconds
                ) VALUES(1,?,?)
                """,
                (float(time.time()), BUCKET_SECONDS),
            )

    def instrumented(self) -> bool:
        """Read-only schema probe used by observability surfaces."""
        with self.store._conn() as db:
            rows = db.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type='table'
                  AND name IN ('operator_active_blocks','operator_activity_meta')
                """
            ).fetchall()
            names = {str(row["name"]) for row in rows}
            if names != {"operator_active_blocks", "operator_activity_meta"}:
                return False
            meta = db.execute(
                """
                SELECT measurement_started_at,bucket_seconds
                FROM operator_activity_meta
                WHERE singleton=1
                """
            ).fetchone()
        if not meta:
            return False
        try:
            started_at = float(meta["measurement_started_at"])
            bucket_seconds = int(meta["bucket_seconds"])
        except (TypeError, ValueError):
            return False
        return math.isfinite(started_at) and started_at >= 0 and bucket_seconds == BUCKET_SECONDS

    def measurement_started_at(self) -> float | None:
        """Return the durable server-side instrumentation start without mutating schema."""
        if not self.instrumented():
            return None
        with self.store._conn() as db:
            row = db.execute(
                """
                SELECT measurement_started_at
                FROM operator_activity_meta
                WHERE singleton=1
                """
            ).fetchone()
        return float(row["measurement_started_at"]) if row else None

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

    def _should_prune(self, observed_at: float) -> bool:
        previous = self._last_prune_at
        if previous is None:
            return True
        if observed_at <= previous:
            return False
        return observed_at - previous >= PRUNE_INTERVAL_SECONDS

    @staticmethod
    def _prune_expired(db, *, observed_at: float) -> None:
        cutoff = max(0.0, float(observed_at) - RETENTION_SECONDS)
        cutoff_block = int(cutoff // BLOCK_SECONDS) * BLOCK_SECONDS
        db.execute(
            "DELETE FROM operator_active_blocks WHERE block_start<?",
            (cutoff_block,),
        )

        offset = cutoff - cutoff_block
        keep_from_slot = min(
            SLOTS_PER_BLOCK,
            max(0, int(math.ceil(offset / BUCKET_SECONDS))),
        )
        if keep_from_slot <= 0:
            return

        expired_mask = (1 << keep_from_slot) - 1
        keep_mask = _ALL_SLOTS_MASK ^ expired_mask
        db.execute(
            """
            UPDATE operator_active_blocks
            SET active_slots=active_slots & ?
            WHERE block_start=?
            """,
            (keep_mask, cutoff_block),
        )
        db.execute(
            "DELETE FROM operator_active_blocks WHERE block_start=? AND active_slots=0",
            (cutoff_block,),
        )

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
        should_prune = self._should_prune(observed_at)

        with self.store._conn() as db:
            if should_prune:
                self._prune_expired(db, observed_at=observed_at)
            cursor = db.execute(
                """
                INSERT INTO operator_active_blocks(
                    tenant_id,user_id,block_start,active_slots,surface,updated_at
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(tenant_id,user_id,block_start) DO UPDATE SET
                    active_slots=operator_active_blocks.active_slots | excluded.active_slots,
                    surface=excluded.surface,
                    updated_at=excluded.updated_at
                WHERE (operator_active_blocks.active_slots & excluded.active_slots)=0
                """,
                (tenant, user, block_start, slot_mask, clean_surface, observed_at),
            )

        if should_prune:
            self._last_prune_at = observed_at
        new_bucket = int(cursor.rowcount or 0) > 0
        return {
            "recorded": True,
            "new_bucket": new_bucket,
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
        retention = {
            "days": RETENTION_DAYS,
            "seconds": RETENTION_SECONDS,
            "granularity_seconds": BUCKET_SECONDS,
            "server_enforced": True,
            "client_configurable": False,
        }
        requested_window_seconds = max(0.0, end - start)
        measurement_started_at = self.measurement_started_at()
        if measurement_started_at is None:
            return {
                "instrumented": False,
                "measurement_started_at": None,
                "window_fully_covered": False,
                "window_coverage_seconds": 0.0,
                "requested_window_seconds": requested_window_seconds,
                "window_coverage_rate": 0.0,
                "active_seconds": 0,
                "operator_hours": 0.0,
                "bucket_count": 0,
                "bucket_seconds": BUCKET_SECONDS,
                "active_users": 0,
                "surfaces": [],
                "daily_seconds": {},
                "retention": retention,
                "duplicate_bucket_write_suppressed": True,
                "definition": "operator active-time telemetry table is not installed",
                "limitation": limitation,
                "client_duration_accepted": False,
                "changes_authority": False,
            }

        coverage_since = max(start, measurement_started_at)
        window_fully_covered = measurement_started_at <= start
        window_coverage_seconds = max(0.0, end - coverage_since)
        if requested_window_seconds > 0:
            window_coverage_rate = min(1.0, window_coverage_seconds / requested_window_seconds)
        else:
            window_coverage_rate = 1.0 if window_fully_covered else 0.0

        start_block = int(max(0.0, coverage_since) // BLOCK_SECONDS) * BLOCK_SECONDS
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
                bucket_end = bucket_start + BUCKET_SECONDS
                if bucket_end <= coverage_since or bucket_start >= end:
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
            "measurement_started_at": measurement_started_at,
            "window_fully_covered": window_fully_covered,
            "window_coverage_seconds": round(window_coverage_seconds, 6),
            "requested_window_seconds": round(requested_window_seconds, 6),
            "window_coverage_rate": round(window_coverage_rate, 6),
            "active_seconds": active_seconds,
            "operator_hours": round(active_seconds / 3600.0, 6),
            "bucket_count": bucket_count,
            "bucket_seconds": BUCKET_SECONDS,
            "active_users": len(active_users),
            "surfaces": sorted(surfaces),
            "daily_seconds": dict(sorted(daily_seconds.items())),
            "retention": retention,
            "duplicate_bucket_write_suppressed": True,
            "definition": definition,
            "limitation": limitation,
            "client_duration_accepted": False,
            "changes_authority": False,
        }
