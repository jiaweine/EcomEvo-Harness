from __future__ import annotations

import asyncio
import json
import time

import skill_policy_cas_probe as base


class SingleConnectionCasSkills(base.CasSkills):
    """Diagnostic refinement: reuse one SQLite connection for read + guarded write."""

    def _persist_note_run_group_cas(self, batch) -> None:
        # Preserve the already-proven metadata-only group path unchanged.
        if len(batch) != 1 or all(request.metadata_only for request in batch):
            return super()._persist_note_run_group_cas(batch)

        request = batch[0]
        with self._conn() as connection:
            observed = connection.execute(
                "SELECT promotion_threshold,retirement_threshold,exploration,updates "
                "FROM evolution_policy WHERE domain=?",
                (request.domain,),
            ).fetchone()

            with self._cas_lock:
                self._cas_attempts += 1

            hook = self._before_cas
            self._before_cas = None
            if hook is not None:
                hook()

            with self._lock:
                connection.execute("BEGIN IMMEDIATE")
                if observed is None:
                    with self._cas_lock:
                        self._cas_fallbacks += 1
                    self._adapt_policy_in_transaction(
                        connection,
                        request.domain,
                        success=request.success,
                        skill_used=request.skill_used,
                        now=time.time(),
                    )
                    return

                promotion, retirement, exploration = self._next_values(
                    observed,
                    success=request.success,
                    skill_used=request.skill_used,
                )
                cursor = connection.execute(
                    "UPDATE evolution_policy SET promotion_threshold=?,retirement_threshold=?,"
                    "exploration=?,updates=updates+1,updated_at=? "
                    "WHERE domain=? AND updates=? AND promotion_threshold=? "
                    "AND retirement_threshold=? AND exploration=?",
                    (
                        promotion,
                        retirement,
                        exploration,
                        time.time(),
                        request.domain,
                        int(observed["updates"]),
                        float(observed["promotion_threshold"]),
                        float(observed["retirement_threshold"]),
                        float(observed["exploration"]),
                    ),
                )
                if cursor.rowcount == 1:
                    with self._cas_lock:
                        self._cas_successes += 1
                    return

                # The pre-write read went stale. Recompute from the latest row while
                # keeping this same writer transaction; never overwrite the new state.
                with self._cas_lock:
                    self._cas_fallbacks += 1
                self._adapt_policy_in_transaction(
                    connection,
                    request.domain,
                    success=request.success,
                    skill_used=request.skill_used,
                    now=time.time(),
                )


def main() -> int:
    base.CasSkills = SingleConnectionCasSkills
    result = asyncio.run(base.main_async())
    result["prototype"] = "single_connection_read_then_guarded_write"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
