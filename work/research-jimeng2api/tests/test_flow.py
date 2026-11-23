"""任务生命周期：提交 → 轮询 → 终态。上游用桩替换，不产生任何真实请求。"""
from __future__ import annotations

import json
import sqlite3

from app import api, db, repo, service
from app.upstream.client import JimengClient


def _account() -> int:
    aid = repo.add_account("s" * 64, "流程测试")
    repo.update_account(aid, status="ok", credits=100)
    return aid


def test_submit_and_poll_success(monkeypatch):
    aid = _account()
    task = service.create_task(api_key_id=None, model="seedream-5.5",
                               prompt="猫", size="2048x2048", count=2)
    monkeypatch.setattr(service, "_submit", lambda account_id, task, uris: task["id"])
    service.submit_task(repo.get_task(task["id"]))
    t = repo.get_task(task["id"])
    assert t["status"] == "running" and t["account_id"] == aid
    assert t["forecast_credits"] == 2

    def fake_fetch(self, ids):
        return {ids[0]: {
            "submit_id": ids[0], "status": 50, "status_name": "success",
            "finished": True, "ok": True, "failed": False, "progress": "2/2",
            "model": "high_aes_general_v55", "cost": 2,
            "fail_message": "", "images": [
                {"url": "https://x/1.png", "width": 2048, "height": 2048,
                 "format": "png", "uri": "tos-cn-i-x/1", "item_id": "i1"},
                {"url": "https://x/2.png", "width": 2048, "height": 2048,
                 "format": "png", "uri": "tos-cn-i-x/2", "item_id": "i2"},
            ]}}

    monkeypatch.setattr(JimengClient, "fetch_many", fake_fetch)
    db.execute("UPDATE tasks SET next_poll_at = 0 WHERE id = ?", (task["id"],))
    service.poll_once()

    t = repo.get_task(task["id"])
    assert t["status"] == "succeeded"
    assert len(t["images"]) == 2
    assert t["finished_at"] is not None
    acc = repo.get_account(aid)
    assert acc["success_count"] == 1


def test_status_checks_do_not_read_reference_payloads(monkeypatch):
    account_id = _account()
    reference = "data:image/png;base64,dGVzdA=="
    task_id = repo.create_task(
        task_id="with-reference", api_key_id=None, mode="i2i",
        model="high_aes_general_v55", prompt="reference", negative_prompt="",
        size="2048x2048", width=2048, height=2048, resolution="2k", count=1,
        seed=None, image_uris=[reference], mask_uri=reference,
        extras={"requested_model": "seedream-5.5"},
    )["id"]
    repo.mark_task_running(task_id, account_id, forecast=1)
    db.execute("UPDATE tasks SET next_poll_at = 0 WHERE id = ?", (task_id,))
    images = [{"url": "https://cdn.example/result.png"}]
    monkeypatch.setattr(
        JimengClient, "fetch_many",
        lambda self, ids: {task_id: {"ok": True, "images": images}},
    )

    def deny_reference_reads(action, table, column, *_):
        if action == sqlite3.SQLITE_READ and table == "tasks" and column in {
            "image_uris", "mask_uri", "submit_draft",
        }:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection = db.conn()
    connection.set_authorizer(deny_reference_reads)
    admin = {"id": None, "is_admin": True}
    try:
        assert api.poll(task_id, admin).status_code == 202
        service.poll_once()
        response = api.poll(task_id, admin)
        assert response.status_code == 200
        assert json.loads(response.body)["data"] == images
        detail = api.task_detail(task_id, admin)
        assert detail["requested_model"] == "seedream-5.5"
        assert service.wait_task(task_id, 1)["images"] == images
    finally:
        connection.set_authorizer(None)

    stored = repo.get_task(task_id)
    assert stored["image_uris"] == [reference]
    assert stored["mask_uri"] == reference


def test_poll_failure_is_terminal(monkeypatch):
    aid = _account()
    task = service.create_task(api_key_id=None, model="seedream-5.5",
                               prompt="违规内容", size="2048x2048", count=1)
    monkeypatch.setattr(service, "_submit", lambda account_id, task, uris: task["id"])
    service.submit_task(repo.get_task(task["id"]))

    def fake_fetch(self, ids):
        return {ids[0]: {
            "submit_id": ids[0], "status": 30, "status_name": "generate_failed",
            "finished": True, "ok": False, "failed": True, "progress": "",
            "model": None, "cost": None, "fail_message": "内容审核未通过",
            "images": []}}

    monkeypatch.setattr(JimengClient, "fetch_many", fake_fetch)
    db.execute("UPDATE tasks SET next_poll_at = 0 WHERE id = ?", (task["id"],))
    service.poll_once()

    t = repo.get_task(task["id"])
    assert t["status"] == "failed" and "内容审核" in t["error"]
    assert repo.get_account(aid)["fail_count"] == 1


def test_submit_network_error_is_not_retried(monkeypatch):
    """网络错误后不换号重发：可能已经建了任务，重发 = 双倍计费。"""
    _account()
    task = service.create_task(api_key_id=None, model="seedream-5.5",
                               prompt="猫", size="2048x2048", count=1)
    from app.upstream.errors import JimengNetworkError

    calls = []

    def boom(account_id, task, uris):
        calls.append(account_id)
        raise JimengNetworkError("连接重置", kind="network")

    monkeypatch.setattr(service, "_submit", boom)
    service.submit_task(repo.get_task(task["id"]))
    t = repo.get_task(task["id"])
    assert t["status"] == "failed" and t["error_kind"] == "network"
    assert len(calls) == 1  # 只打了一次


def test_submit_rate_limit_switches_account(monkeypatch):
    from app.upstream.errors import JimengRateLimitError
    a1 = _account()
    a2 = repo.add_account("t" * 64, "二号")
    repo.update_account(a2, status="ok", credits=50)

    task = service.create_task(api_key_id=None, model="seedream-5.5",
                               prompt="猫", size="2048x2048", count=1)
    tried = []

    def limited(account_id, task, uris):
        tried.append(account_id)
        if len(tried) == 1:
            raise JimengRateLimitError("ret=1010", ret=1010)
        return task["id"]

    monkeypatch.setattr(service, "_submit", limited)
    service.submit_task(repo.get_task(task["id"]))
    t = repo.get_task(task["id"])
    assert t["status"] == "running"
    assert len(tried) == 2 and tried[0] != tried[1]
    first = repo.get_account(a1)
    assert first["cooldown_until"] and first["cooldown_until"] > db.now()
