from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one anchor, found {count}")
    target.write_text(text.replace(old, new, 1))


path = "ecomevo/runtime/bundled_event_store.py"

replace_once(
    path,
    "import asyncio\nimport json\n",
    "import asyncio\nimport copy\nimport json\n",
)

replace_once(
    path,
    "import weakref\nfrom dataclasses import dataclass, field\n",
    "import weakref\nfrom collections import OrderedDict\nfrom dataclasses import dataclass, field\n",
)

replace_once(
    path,
    "_CHECKPOINT_GROUP_LIMIT = 64\n_T = TypeVar(\"_T\")\n",
    "_CHECKPOINT_GROUP_LIMIT = 64\n_EVOLUTION_PATCH_CACHE_LIMIT = 256\n_T = TypeVar(\"_T\")\n",
)

replace_once(
    path,
    "        self._io_gates: weakref.WeakKeyDictionary[\n"
    "            asyncio.AbstractEventLoop, asyncio.Lock\n"
    "        ] = weakref.WeakKeyDictionary()\n"
    "        super().__init__(path)\n",
    "        self._io_gates: weakref.WeakKeyDictionary[\n"
    "            asyncio.AbstractEventLoop, asyncio.Lock\n"
    "        ] = weakref.WeakKeyDictionary()\n"
    "        self._patch_cache_lock = threading.RLock()\n"
    "        self._patch_positive_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()\n"
    "        super().__init__(path)\n",
)

replace_once(
    path,
    "    @staticmethod\n"
    "    def _existing_patch_payload(row, patch: EvolutionPatch) -> dict[str, Any]:\n",
    "    def _cached_patch_payload(self, fingerprint: str) -> dict[str, Any] | None:\n"
    "        with self._patch_cache_lock:\n"
    "            payload = self._patch_positive_cache.get(fingerprint)\n"
    "            if payload is None:\n"
    "                return None\n"
    "            self._patch_positive_cache.move_to_end(fingerprint)\n"
    "            return copy.deepcopy(payload)\n\n"
    "    def _remember_patch_payload(\n"
    "        self, fingerprint: str, payload: dict[str, Any]\n"
    "    ) -> None:\n"
    "        with self._patch_cache_lock:\n"
    "            self._patch_positive_cache[fingerprint] = copy.deepcopy(payload)\n"
    "            self._patch_positive_cache.move_to_end(fingerprint)\n"
    "            while len(self._patch_positive_cache) > _EVOLUTION_PATCH_CACHE_LIMIT:\n"
    "                self._patch_positive_cache.popitem(last=False)\n\n"
    "    @staticmethod\n"
    "    def _existing_patch_payload(row, patch: EvolutionPatch) -> dict[str, Any]:\n",
)

old_method = '''    def save_patch_if_novel(self, patch: EvolutionPatch) -> dict[str, Any] | None:
        """Return duplicate patches without reserving SQLite's writer slot.

        ``evolution_patches`` is append-only through the runtime contract and fingerprint
        uniqueness is enforced by SQLite. A read hit is therefore safe to return without
        ``BEGIN IMMEDIATE``. A miss rechecks under the original writer lock before INSERT
        so concurrent first observations retain the base EventStore novelty semantics.
        """
        fingerprint = self._patch_fingerprint(patch)
        with self._conn() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM evolution_patches WHERE fingerprint=? LIMIT 1",
                (fingerprint,),
            ).fetchone()
        if existing is not None:
            return self._existing_patch_payload(existing, patch)

        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload_json FROM evolution_patches WHERE fingerprint=? LIMIT 1",
                (fingerprint,),
            ).fetchone()
            if existing is not None:
                return self._existing_patch_payload(existing, patch)
            connection.execute(
                "INSERT INTO evolution_patches(patch_id,created_at,payload_json,fingerprint) "
                "VALUES(?,?,?,?)",
                (
                    patch.patch_id,
                    patch.created_at,
                    patch.model_dump_json(),
                    fingerprint,
                ),
            )
        return None
'''

new_method = '''    def save_patch_if_novel(self, patch: EvolutionPatch) -> dict[str, Any] | None:
        """Return known duplicates without repeated SQLite fingerprint lookups.

        The positive cache only contains fingerprints already confirmed durable by this
        instance. Misses always retain #74's WAL read plus writer-locked recheck, so a
        fingerprint appended later by another process remains discoverable. The cache is
        bounded and hit payloads are deep-copied to preserve fresh mutable return values.
        """
        fingerprint = self._patch_fingerprint(patch)
        cached = self._cached_patch_payload(fingerprint)
        if cached is not None:
            return cached

        with self._conn() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM evolution_patches WHERE fingerprint=? LIMIT 1",
                (fingerprint,),
            ).fetchone()
        if existing is not None:
            payload = self._existing_patch_payload(existing, patch)
            self._remember_patch_payload(fingerprint, payload)
            return payload

        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload_json FROM evolution_patches WHERE fingerprint=? LIMIT 1",
                (fingerprint,),
            ).fetchone()
            if existing is not None:
                payload = self._existing_patch_payload(existing, patch)
                self._remember_patch_payload(fingerprint, payload)
                return payload
            connection.execute(
                "INSERT INTO evolution_patches(patch_id,created_at,payload_json,fingerprint) "
                "VALUES(?,?,?,?)",
                (
                    patch.patch_id,
                    patch.created_at,
                    patch.model_dump_json(),
                    fingerprint,
                ),
            )

        # Populate only after the connection context has committed the novel INSERT.
        self._remember_patch_payload(
            fingerprint,
            patch.model_dump(mode="json"),
        )
        return None
'''

replace_once(path, old_method, new_method)
