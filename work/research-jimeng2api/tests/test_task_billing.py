from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from app import api, db, repo, service
from app.upstream.client import JimengClient
from app.upstream.errors import JimengNetworkError


def billing_task(*, finished=True):
    raw_key = repo.create_key("billing test", quota=1000)
    key = repo.key_by_raw(raw_key)
    account_id = repo.add_account("s" * 64, "billing test")
    task = service.create_task(api_key_id=key["id"], model="seedream-5.5",
                               prompt="test", size="2048x2048", count=1)
    repo.mark_task_running(task["id"], account_id, forecast=118)
    if finished:
        repo.finish_task(task["id"], images=[], forecast=118)
    return task["id"], raw_key


def test_charge_reads_only_verified_task_amount_and_retries_delayed_posting(client, auth, monkeypatch):
    task_id, raw_key = billing_task()
    path = f"/v1/tasks/{task_id}/charge"
    calls = []

    def ledger(self, *, count, history_type, cursor):
        assert count == 20 and history_type == 2
        calls.append(cursor)
        if len(calls) == 1:
            return {"records": [{"amount": 118}, {"submit_id": "other", "amount": 77}]}
        if cursor == "0":
            return {"records": [], "has_more": True, "new_cursor": "20"}
        return {"records": [{"submit_id": task_id, "amount": "19"}]}

    monkeypatch.setattr(JimengClient, "credit_history", ledger)
    assert client.get(path, headers={"Authorization": f"Bearer {raw_key}"}).status_code == 403
    assert client.get(f"/v1/tasks/{uuid4()}/charge", headers=auth).status_code == 404
    pending = client.get(path, headers=auth)
    assert pending.json() == {"amount": None}
    assert pending.headers["Cache-Control"] == "no-store"
    assert repo.key_by_raw(raw_key)["used_credits"] == 118
    assert client.get(path, headers=auth).json() == {"amount": 19}
    assert client.get(path, headers=auth).json() == {"amount": 19}
    assert repo.key_by_raw(raw_key)["used_credits"] == 19
    assert calls == ["0", "0", "20"]


def test_lookup_resumes_beyond_first_sixty_receipts(monkeypatch):
    task_id, _raw_key = billing_task()
    calls = []

    def ledger(self, *, cursor, **kw):
        calls.append(cursor)
        if cursor == "80":
            return {"records": [{"submit_id": task_id, "amount": 19}]}
        return {"records": [], "has_more": True, "new_cursor": str(int(cursor) + 20)}

    monkeypatch.setattr(JimengClient, "credit_history", ledger)
    assert service.reconcile(task_id)["found"] is False
    assert repo.get_task_charge(task_id)["credit_history_cursor"] == "60"
    assert service.reconcile(task_id)["amount"] == 19
    assert calls == ["0", "20", "40", "0", "60", "80"]


@pytest.mark.parametrize("invalid", [None, True, -1, 1.5, "19.5", "9223372036854775808"])
def test_invalid_consume_record_does_not_settle_task(client, auth, monkeypatch, invalid):
    task_id, raw_key = billing_task()
    records = [{"submit_id": task_id, "amount": invalid}]
    monkeypatch.setattr(JimengClient, "credit_history", lambda self, **kw: {"records": records})
    path = f"/v1/tasks/{task_id}/charge"
    assert client.get(path, headers=auth).json() == {"amount": None}
    assert repo.key_by_raw(raw_key)["used_credits"] == 118
    records[0]["amount"] = 0
    assert client.get(path, headers=auth).json() == {"amount": 0}
    assert repo.key_by_raw(raw_key)["used_credits"] == 0


def test_contending_reconciliation_of_failed_task_applies_one_actual_debit(monkeypatch):
    task_id, raw_key = billing_task(finished=False)
    repo.mark_task_failed(task_id, "failed after submit", "upstream")
    requests = Barrier(2)

    def ledger(self, **kw):
        requests.wait(timeout=5)
        record = {"submit_id": task_id, "amount": 4}
        return {"records": [record, record.copy()]}

    monkeypatch.setattr(JimengClient, "credit_history", ledger)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.map(service.reconcile, (task_id, task_id))
    assert first["amount"] == second["amount"] == 4
    assert repo.key_by_raw(raw_key)["used_credits"] == 4


def test_no_dispatch_has_known_zero_and_unknown_dispatch_keeps_account(client, auth, monkeypatch):
    task = service.create_task(api_key_id=None, model="seedream-5.5", prompt="test",
                               size="2048x2048", count=1)
    service.submit_task(task)
    assert client.get(f"/v1/tasks/{task['id']}/charge", headers=auth).json() == {"amount": 0}

    account_id = repo.add_account("s" * 64, "billing test")
    repo.update_account(account_id, status="ok", credits=100)
    task = service.create_task(api_key_id=None, model="seedream-5.5", prompt="test",
                               size="2048x2048", count=1)

    def interrupted(selected_account_id, submitted, _uris):
        assert repo.get_task_charge(submitted["id"])["account_id"] == selected_account_id == account_id
        raise JimengNetworkError("connection lost")

    monkeypatch.setattr(service, "_submit", interrupted)
    monkeypatch.setattr(JimengClient, "credit_history", lambda self, **kw: {"records": []})
    service.submit_task(task)
    assert client.get(f"/v1/tasks/{task['id']}/charge", headers=auth).json() == {"amount": None}


def test_admin_idempotency_survives_racing_requests_and_executor_restart(client, auth, monkeypatch):
    key = uuid4()
    headers = {**auth, "Idempotency-Key": key.hex}
    monkeypatch.setattr(api, "executor_ready", lambda: True)
    create_task = repo.create_task
    requests = Barrier(2)

    def create_together(**kwargs):
        requests.wait(timeout=5)
        return create_task(**kwargs)

    monkeypatch.setattr(repo, "create_task", create_together)
    payload = {"model": "jimeng-video", "prompt": "test", "duration": 4}
    path = "/async/v1/images/generations"

    def submit(_):
        return client.post(path, json=payload, headers=headers)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.map(submit, range(2))
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json() == {"task_id": str(key)}
    assert db.scalar("SELECT COUNT(*) FROM tasks") == 1
    monkeypatch.setattr(api, "executor_ready", lambda: False)
    assert submit(None).json() == {"task_id": str(key)}
    other_key = repo.create_key("other admin", is_admin=True)
    assert client.post(path, json=payload, headers={
        **headers, "Authorization": f"Bearer {other_key}",
    }).status_code == 409
