from __future__ import annotations

from pathlib import Path


path = Path("ecomevo/runtime/engine.py")
text = path.read_text(encoding="utf-8")

replacements = [
    (
        "from .bundled_event_store import BundledEventStore\n",
        "from .grouped_restore_event_store import GroupedRestoreBundledEventStore\n",
    ),
    (
        "self.events = component('event.store', lambda: BundledEventStore(db_path))",
        "self.events = component('event.store', lambda: GroupedRestoreBundledEventStore(db_path))",
    ),
    (
        "        async def controller_restore(reference):\n"
        "            seq=reference.get('seq') if isinstance(reference,dict) else None\n"
        "            return self.events.restore_checkpoint(sid,seq)\n",
        "        async def controller_restore(reference):\n"
        "            seq=reference.get('seq') if isinstance(reference,dict) else None\n"
        "            restore_async=getattr(self.events,'restore_checkpoint_async',None)\n"
        "            if sink is None and seq is not None and callable(restore_async):\n"
        "                return await restore_async(sid,seq)\n"
        "            return self.events.restore_checkpoint(sid,seq)\n",
    ),
]

for old, new in replacements:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one engine patch target, found {count}: {old[:80]!r}")
    text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
