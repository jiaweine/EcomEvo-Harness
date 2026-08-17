"""Compatibility alias for the modular API implementation.

``ecomevo.api.app`` remains the canonical import/monkeypatch surface while the
production implementation lives in smaller modules.
"""
from __future__ import annotations

import asyncio
import sys
from PIL import Image

from . import application as _application
from .upload_security import validate_raster as _validate_raster


async def _compat_emit(cid: str, event_type: str, payload: dict):
    """Append durably, then replace stale process-local wake data with the full event.

    WebSocket delivery still treats SQLite ``task_events`` as authoritative and drains
    by event id, so the queue payload is only a low-latency compatibility/wake hint.
    """
    event = _application.store.add_event(cid, event_type, payload)
    for queue in list(_application.queues.get(cid, [])):
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


# Preserve established monkeypatch/test hooks without moving upload logic back into
# the API module. PIL.Image is the same module object used by upload_security.
_application.Image = Image
_application._validate_raster = _validate_raster
_application.emit = _compat_emit
_application.job_worker.emit = _compat_emit

sys.modules[__name__] = _application
