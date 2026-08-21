"""Compatibility alias for the modular API implementation.

``ecomevo.api.app`` remains the canonical import/monkeypatch surface while the
production implementation lives in smaller modules.
"""
from __future__ import annotations

import asyncio
import sys
from PIL import Image

from ecomevo.identity import IdentityMiddleware
from . import application as _application
from .upload_security import validate_raster as _validate_raster


async def _compat_emit(
    cid: str,
    event_type: str,
    payload: dict,
    job_id: str | None = None,
    worker_id: str | None = None,
):
    """Append durably, then replace stale process-local wake data with the full event.

    WebSocket delivery still treats SQLite ``task_events`` as authoritative and drains
    by event id, so the queue payload is only a low-latency compatibility/wake hint.
    """
    if job_id is not None or worker_id is not None:
        if not job_id or not worker_id:
            return None
        event = _application.store.add_job_event(job_id, worker_id, event_type, payload)
    else:
        event = _application.store.add_event(cid, event_type, payload)
    if not event:
        return None
    event_cid = str(event["conversation_id"])
    for queue in list(_application.queues.get(event_cid, [])):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
    return event


_application.Image = Image
_application._validate_raster = _validate_raster
_application.emit = _compat_emit
_application.job_worker.emit = _compat_emit

if not getattr(_application.app.state, "identity_middleware_installed", False):
    _application.app.add_middleware(IdentityMiddleware, store=_application.store)
    _application.app.state.identity_middleware_installed = True

sys.modules[__name__] = _application
