from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException

from ecomevo.product.store import ConversationStore


def _add(store, cid, index, size=1):
    return store.add_asset(
        cid,
        name=f'a-{index}.txt',
        mime='text/plain',
        path=f'/tmp/a-{index}.txt',
        size=size,
        meta={'kind': 'text'},
    )


def test_concurrent_uploads_cannot_exceed_asset_count_cap(tmp_path):
    store = ConversationStore(tmp_path / 'product.db', tmp_path / 'assets')
    cid = store.create_conversation()['id']
    store.MAX_ASSETS_PER_CONVERSATION = 4

    for i in range(3):
        _add(store, cid, i)

    def attempt(i):
        try:
            _add(store, cid, i)
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(pool.map(attempt, [10, 11]))

    assert codes == [200, 409]
    assert len(store.list_assets(cid)) == 4


def test_concurrent_uploads_cannot_exceed_total_byte_cap(tmp_path):
    store = ConversationStore(tmp_path / 'product.db', tmp_path / 'assets')
    cid = store.create_conversation()['id']
    store.MAX_ASSET_BYTES_PER_CONVERSATION = 10
    _add(store, cid, 1, size=8)

    def attempt(i):
        try:
            _add(store, cid, i, size=2)
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(pool.map(attempt, [20, 21]))

    assert codes == [200, 413]
    assert sum(int(a['size']) for a in store.list_assets(cid)) == 10
