from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_inbox_frontend_is_separate_operator_surface():
    html = (ROOT / "frontend" / "inbox.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "inbox.js").read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "inbox.css").read_text(encoding="utf-8")

    assert "任务队列" in html
    assert "认领 ≠ 审批" in html
    assert "优先级 ≠ Runtime 路由" in html
    assert "协作 ≠ 审批" in html
    assert "review request" in html
    assert "handoff" in html
    assert "@mention" in html
    assert "/api/inbox" in js
    assert "/collaboration" in js
    assert "/comments" in js
    assert "/review-requests" in js
    assert "/handoffs" in js
    assert "esc(event.body)" in js
    assert "previousSelectedId !== state.selectedId" in js
    assert "/api/actions/" not in js
    assert "call_tool" not in js
    assert "mcp" not in js.lower()
    assert "@media(max-width:720px)" in css


def test_inbox_routes_do_not_expose_business_action_execution():
    source = (ROOT / "ecomevo" / "api" / "inbox_routes.py").read_text(encoding="utf-8")
    assert '"/api/inbox"' in source
    assert "claim_conversation" in source
    assert "update_queue_priority" in source
    assert "add_collaboration_comment" in source
    assert "request_task_review" in source
    assert "request_task_handoff" in source
    assert "resolve_task_handoff" in source
    assert "/api/actions/" not in source
    assert ".call_tool(" not in source
    assert "assignment_grants_approval" in source
    assert "priority_changes_runtime_routing" in source
    assert "handoff_grants_approval" not in source or "list_collaboration" in source


def test_queue_persists_only_collaboration_metadata_not_runtime_state():
    source = (ROOT / "ecomevo" / "product" / "queue_store.py").read_text(encoding="utf-8")
    assert "queue_priority" in source
    assert "owner_user_id" in source
    assert "owner_claimed_at" in source
    assert "queue_updated_at" in source
    assert "ALTER TABLE conversations ADD COLUMN queue_state" not in source
    assert "conversation_jobs" in source
    assert "turn_leases" in source
    assert "status='uncertain'" in source
    assert "evidence_sufficiency" in source


def test_collaboration_store_is_metadata_only_and_handoff_is_two_party():
    source = (ROOT / "ecomevo" / "product" / "queue_store.py").read_text(encoding="utf-8")
    assert "task_watchers" in source
    assert "task_collaboration_events" in source
    assert "review_requested" in source
    assert "handoff_requested" in source
    assert "handoff_accepted" in source
    assert "handoff_invalidated" in source
    assert "only the current owner can request handoff" in source
    assert "only the handoff target can respond" in source
    assert "UPDATE actions" not in source
    assert "DELETE FROM task_collaboration_events" not in source
    assert "UPDATE task_collaboration_events" not in source
    assert ".call_tool(" not in source
    assert "/api/actions/" not in source


def test_workbench_exposes_inbox_as_progressive_enhancement():
    source = (ROOT / "frontend" / "drawer-a11y.js").read_text(encoding="utf-8")
    assert "installInboxEntry" in source
    assert "taskInboxLink" in source
    assert "'/api/inbox/ui'" in source
