import pytest

from app import db, repo, service
from app.api import ASYNC_ERROR_CODES, async_error
from app.upstream.errors import JimengError, raise_for_ret


def test_commerce_role_error_is_not_login_confirmation():
    with pytest.raises(JimengError) as result:
        raise_for_ret('5006', {'errmsg': 'credit miss role'})
    error = result.value
    assert error.kind == 'entitlement'
    assert not error.retryable and not error.account_fault
    assert async_error({'error_kind': error.kind})['code'] == 'model_permission_required'
    legacy = async_error({'error_kind': 'unknown', 'error': str(error)})
    assert legacy['code'] == 'model_permission_required'
    assert legacy['code'] in ASYNC_ERROR_CODES
    assert '会员权益' in legacy['message']


def test_unrelated_5006_is_not_misclassified():
    with pytest.raises(JimengError) as result:
        raise_for_ret(5006, {'errmsg': 'inviteLinkExpired'})
    assert result.value.kind == 'unknown'


def test_role_failure_is_terminal_without_pausing_account(monkeypatch):
    account_id = repo.add_account('test-session-entitlement')
    repo.update_account(account_id, status='ok', credits=40)
    task = service.create_task(api_key_id=None, model='jimeng-video',
                               prompt='test', size='', count=1, duration=5)
    calls = []

    def submit(account_id, task, uris):
        calls.append(account_id)
        raise_for_ret('5006', {'errmsg': 'credit miss role'})

    monkeypatch.setattr(service, '_submit', submit)
    service.submit_task(repo.get_task(task['id']))
    failed = repo.get_task(task['id'])
    assert failed['status'] == 'failed' and failed['error_kind'] == 'entitlement'
    assert calls == [account_id]
    account = repo.get_account(account_id)
    assert account['state'] == 'ok'
    assert not account['verification'].get('pending')
    assert account['generation_issue']['task_id'] == task['id']
    assert 'sessionid' not in account['generation_issue']

    # A successful login/balance probe must not erase the model-specific failure.
    repo.update_account(account_id, status='ok', last_error='', credits=40)
    assert repo.get_account(account_id)['generation_issue']
    db.execute("UPDATE tasks SET error_kind='unknown' WHERE id=?", (task['id'],))
    assert repo.get_account(account_id)['generation_issue']
    db.execute("UPDATE tasks SET status='succeeded' WHERE id=?", (task['id'],))
    assert repo.get_account(account_id)['generation_issue'] is None
