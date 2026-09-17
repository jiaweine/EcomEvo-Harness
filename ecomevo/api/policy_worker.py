from __future__ import annotations

import asyncio

from ecomevo.runtime.policy_context import bind_policy_scope, reset_policy_scope

from .durable_jobs import DurableConversationWorker


class PolicyAwareDurableConversationWorker(DurableConversationWorker):
    """Bind immutable conversation tenancy around durable analysis execution.

    HTTP request ContextVars are intentionally not trusted here because a queued job may
    be reclaimed by another process long after the request finished. The durable
    conversation row is the source of truth for tenant policy scope.
    """

    async def run_once(self, job_id: str | None = None) -> bool:
        job = await asyncio.to_thread(
            self.store.claim_job,
            self.worker_id,
            job_id=job_id,
            lease_seconds=self.lease_seconds,
        )
        if not job:
            return False

        scope: dict[str, str] = {}
        tenant_lookup = getattr(self.store, "conversation_tenant", None)
        if callable(tenant_lookup):
            tenant = await asyncio.to_thread(tenant_lookup, str(job["conversation_id"]))
            if tenant:
                scope["tenant"] = str(tenant)

        token = bind_policy_scope(scope)
        try:
            await self._execute(job)
        finally:
            reset_policy_scope(token)
        return True
