from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from . import store as _store


ConversationStore = _store.ConversationStore
_ORIGINAL_INIT = ConversationStore._init
_TERMINAL_ACTIONS = ('simulated', 'executed', 'rejected', 'failed')


def _decode_asset(row) -> dict[str, Any]:
    data = dict(row)
    data['meta'] = json.loads(data.pop('meta') or '{}')
    data['active'] = bool(data.get('active', 1))
    data['excluded_at'] = data.get('excluded_at')
    data['excluded_reason'] = data.get('excluded_reason') or ''
    return data


def _init_with_lifecycle(self) -> None:
    _ORIGINAL_INIT(self)
    with self._conn() as db:
        columns = {row['name'] for row in db.execute('PRAGMA table_info(assets)').fetchall()}
        if 'active' not in columns:
            db.execute('ALTER TABLE assets ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
        if 'excluded_at' not in columns:
            db.execute('ALTER TABLE assets ADD COLUMN excluded_at REAL')
        if 'excluded_reason' not in columns:
            db.execute("ALTER TABLE assets ADD COLUMN excluded_reason TEXT NOT NULL DEFAULT ''")
        db.execute('CREATE INDEX IF NOT EXISTS idx_assets_conversation_active ON assets(conversation_id,active,created_at)')


def _add_asset(self, cid, *, name, mime, path, size, meta):
    import uuid

    aid = f'asset-{uuid.uuid4().hex[:12]}'
    now = time.time()
    size = max(0, int(size or 0))
    with self._conn() as db:
        db.execute('BEGIN IMMEDIATE')
        if cid:
            exists = db.execute('SELECT 1 FROM conversations WHERE id=?', (cid,)).fetchone()
            if not exists:
                raise KeyError(cid)
            quota = db.execute(
                'SELECT COUNT(*) AS count,COALESCE(SUM(size),0) AS bytes FROM assets WHERE conversation_id=?',
                (cid,),
            ).fetchone()
            count = int(quota['count'] or 0)
            used = int(quota['bytes'] or 0)
            if count >= self.MAX_ASSETS_PER_CONVERSATION:
                from fastapi import HTTPException
                raise HTTPException(409, '单个任务最多保留 120 份资料，请新建任务或整理现有资料')
            if used + size > self.MAX_ASSET_BYTES_PER_CONVERSATION:
                from fastapi import HTTPException
                raise HTTPException(413, '当前任务资料总量已达到上限')
        db.execute(
            '''INSERT INTO assets(
                   id,conversation_id,name,mime,path,size,meta,created_at,
                   active,excluded_at,excluded_reason
               ) VALUES(?,?,?,?,?,?,?,?,1,NULL,'')''',
            (aid, cid, name, mime, path, size, json.dumps(meta, ensure_ascii=False, default=str), now),
        )
        if cid:
            db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now, cid))
    return self.get_asset(aid)


def _get_asset(self, aid):
    with self._conn() as db:
        row = db.execute('SELECT * FROM assets WHERE id=?', (aid,)).fetchone()
    if not row:
        raise KeyError(aid)
    return _decode_asset(row)


def _list_assets(self, cid, include_excluded: bool = True):
    sql = 'SELECT * FROM assets WHERE conversation_id=?'
    params: list[Any] = [cid]
    if not include_excluded:
        sql += ' AND active=1'
    sql += ' ORDER BY created_at'
    with self._conn() as db:
        rows = db.execute(sql, params).fetchall()
    return [_decode_asset(row) for row in rows]


def _has_active_turn(self, cid) -> bool:
    now = time.time()
    with self._conn() as db:
        row = db.execute(
            'SELECT 1 FROM turn_leases WHERE conversation_id=? AND expires_at>? LIMIT 1',
            (cid, now),
        ).fetchone()
    return bool(row)


def _assert_no_active_work(db, cid, now: float) -> None:
    if not cid:
        return
    lease = db.execute(
        'SELECT 1 FROM turn_leases WHERE conversation_id=? AND expires_at>? LIMIT 1',
        (cid, now),
    ).fetchone()
    if lease:
        raise HTTPException(409, '任务正在处理，结果返回后再调整资料')
    has_jobs = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversation_jobs'",
    ).fetchone()
    if has_jobs:
        active_job = db.execute(
            "SELECT 1 FROM conversation_jobs WHERE conversation_id=? AND status IN ('queued','running') LIMIT 1",
            (cid,),
        ).fetchone()
        if active_job:
            raise HTTPException(409, '任务正在处理，结果返回后再调整资料')


def _set_asset_active(self, aid, active: bool, reason: str = ''):
    now = time.time()
    with self._conn() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT conversation_id FROM assets WHERE id=?', (aid,)).fetchone()
        if not row:
            raise KeyError(aid)
        cid = row['conversation_id']
        _assert_no_active_work(db, cid, now)
        if active:
            db.execute(
                "UPDATE assets SET active=1,excluded_at=NULL,excluded_reason='' WHERE id=?",
                (aid,),
            )
        else:
            db.execute(
                'UPDATE assets SET active=0,excluded_at=?,excluded_reason=? WHERE id=?',
                (now, (reason or '用户从后续分析中排除')[:500], aid),
            )
        if cid:
            db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now, cid))
    return self.get_asset(aid)


def _contains_asset(value: Any, aid: str) -> bool:
    if isinstance(value, str):
        return value == aid
    if isinstance(value, list):
        return any(_contains_asset(item, aid) for item in value)
    if isinstance(value, dict):
        return any(_contains_asset(item, aid) for item in value.values())
    return False


def _audit_payload_rows(db, cid):
    rows = []
    rows.extend(db.execute('SELECT payload FROM messages WHERE conversation_id=?', (cid,)).fetchall())
    rows.extend(db.execute('SELECT payload FROM actions WHERE conversation_id=?', (cid,)).fetchall())
    rows.extend(db.execute(
        "SELECT payload FROM task_events WHERE conversation_id=? AND type NOT IN ('asset.scope_updated','asset.deleted')",
        (cid,),
    ).fetchall())
    return rows


def _asset_has_audit_references(self, aid) -> bool:
    asset = self.get_asset(aid)
    cid = asset.get('conversation_id')
    if not cid:
        return False
    with self._conn() as db:
        payload_rows = _audit_payload_rows(db, cid)
    for row in payload_rows:
        try:
            payload = json.loads(row['payload'] or '{}')
        except (TypeError, json.JSONDecodeError):
            continue
        if _contains_asset(payload, aid):
            return True
    return False


def _safe_cleanup_asset_files(self, asset: dict[str, Any]) -> None:
    root = self.asset_dir.resolve()
    candidates: set[Path] = set()
    path = Path(str(asset.get('path') or ''))
    if path:
        candidates.add(path)
    meta = asset.get('meta') or {}
    for frame in meta.get('keyframes') or []:
        candidates.add(Path(str(frame)))

    parent_dirs: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if not resolved.is_relative_to(root):
                continue
            if resolved.is_file():
                resolved.unlink(missing_ok=True)
                if resolved.parent != root:
                    parent_dirs.add(resolved.parent)
        except OSError:
            continue
    for parent in sorted(parent_dirs, key=lambda p: len(p.parts), reverse=True):
        try:
            if parent.is_relative_to(root) and parent != root and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass


def _delete_asset_if_unreferenced(self, aid):
    with self._conn() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM assets WHERE id=?', (aid,)).fetchone()
        if not row:
            raise KeyError(aid)
        asset = _decode_asset(row)
        cid = asset.get('conversation_id')
        if cid:
            _assert_no_active_work(db, cid, time.time())
            payload_rows = _audit_payload_rows(db, cid)
            for payload_row in payload_rows:
                try:
                    payload = json.loads(payload_row['payload'] or '{}')
                except (TypeError, json.JSONDecodeError):
                    continue
                if _contains_asset(payload, aid):
                    return None
        db.execute('DELETE FROM assets WHERE id=?', (aid,))
        if cid:
            db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (time.time(), cid))
    self._safe_cleanup_asset_files(asset)
    return asset


def _list_actions(self, cid, status=None, terminal_limit: int = 100):
    if status:
        with self._conn() as db:
            rows = db.execute(
                'SELECT * FROM actions WHERE conversation_id=? AND status=? ORDER BY created_at DESC',
                (cid, status),
            ).fetchall()
        return [self._decode_action(row) for row in rows]
    terminal = ','.join('?' for _ in _TERMINAL_ACTIONS)
    with self._conn() as db:
        # Only the fixed number of bound placeholders is formatted into these statements.
        active = db.execute(
            f'SELECT * FROM actions WHERE conversation_id=? AND status NOT IN ({terminal}) ORDER BY created_at DESC',  # nosec
            (cid, *_TERMINAL_ACTIONS),
        ).fetchall()
        recent = db.execute(
            f'SELECT * FROM actions WHERE conversation_id=? AND status IN ({terminal}) ORDER BY created_at DESC LIMIT ?',  # nosec
            (cid, *_TERMINAL_ACTIONS, max(1, int(terminal_limit))),
        ).fetchall()
    rows = sorted(
        [*active, *recent],
        key=lambda row: (float(row['created_at']), str(row['id'])),
        reverse=True,
    )
    return [self._decode_action(row) for row in rows]


# Patch the original class object in-place. This preserves compatibility for callers that import
# ConversationStore from ecomevo.product.store after the package initializer has run.
ConversationStore._init = _init_with_lifecycle
ConversationStore.add_asset = _add_asset
ConversationStore.get_asset = _get_asset
ConversationStore.list_assets = _list_assets
ConversationStore.has_active_turn = _has_active_turn
ConversationStore.set_asset_active = _set_asset_active
ConversationStore.asset_has_audit_references = _asset_has_audit_references
ConversationStore._safe_cleanup_asset_files = _safe_cleanup_asset_files
ConversationStore.delete_asset_if_unreferenced = _delete_asset_if_unreferenced
ConversationStore.list_actions = _list_actions
