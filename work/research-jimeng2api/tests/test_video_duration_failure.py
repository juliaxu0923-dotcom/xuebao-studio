import pytest

from app import db, repo, service
from app.api import ASYNC_ERROR_CODES, async_error
from app.upstream import protocol
from app.upstream.client import JimengClient


@pytest.mark.parametrize('failure', [
    {'fail_code': '18060'},
    {'fail_msg': 'ParamGenerationDurationUnsupported'},
    {'fail_starling_key': 'reference_validation_error_generation_duration_unsupported'},
    {'fail_starling_message': '当前模型不支持所选生成时长，请选择模型支持的时长或使用智能时长后重试。'},
])
def test_duration_failure_fields_are_recognized(failure):
    result = protocol.parse_task('test', {'task': {'status': 30}, **failure})
    assert result['failed'] and result['fail_kind'] == 'duration'
    assert result['fail_message']


def test_other_failures_are_not_duration_errors():
    result = protocol.parse_task('test', {'task': {'status': 30},
                                         'fail_starling_message': '内容审核未通过'})
    assert result['fail_kind'] == 'upstream'
    assert async_error({'error_kind': 'upstream', 'error': result['fail_message']})['code'] == 'task_failed'


def test_legacy_message_and_structured_failure_have_same_public_result():
    extras = {'video': {'duration_s': 25}}
    legacy = async_error({'error_kind': 'upstream', 'error': '当前模型不支持所选生成时长', 'extras': extras})
    current = async_error({'error_kind': 'duration', 'extras': extras})
    assert legacy == current
    assert current['code'] == 'video_duration_unsupported'
    assert current['code'] in ASYNC_ERROR_CODES and '25 秒' in current['message']
    assert '稍后重试' not in current['message']
    assert '<script>' not in async_error({'error_kind': 'duration',
        'extras': {'video': {'duration_s': '<script>'}}})['message']


def test_poll_preserves_failure_reason_and_account_usable(monkeypatch):
    account_id = repo.add_account('duration-test-session')
    repo.update_account(account_id, status='ok', credits=1000)
    task = service.create_task(api_key_id=None, model='jimeng-video', prompt='test',
                               size='', count=1, duration=25)
    monkeypatch.setattr(service, '_submit', lambda aid, task, uris: task['id'])
    service.submit_task(repo.get_task(task['id']))
    monkeypatch.setattr(JimengClient, 'fetch_many', lambda self, ids: {ids[0]: protocol.parse_task(ids[0], {
        'task': {'status': 30}, 'fail_code': '18060', 'fail_msg': 'ParamGenerationDurationUnsupported',
        'fail_starling_message': '当前模型不支持所选生成时长'})})
    db.execute('UPDATE tasks SET next_poll_at=0 WHERE id=?', (task['id'],))
    service.poll_once()
    failed = repo.get_task(task['id'])
    assert failed['status'] == 'failed' and failed['error_kind'] == 'duration'
    assert failed['upstream_status'] == 30 and failed['attempts'] == 1
    assert failed['extras']['video']['duration_ms'] == 25000
    account = repo.get_account(account_id)
    assert account['state'] == 'ok' and not account['verification'].get('pending')
    assert account['generation_issue']['code'] == 'video_duration_unsupported'
    assert '25 秒' in account['generation_issue']['message']
    # Existing saved failures get the same admin diagnosis before any migration.
    db.execute("UPDATE tasks SET error_kind='upstream' WHERE id=?", (task['id'],))
    assert repo.get_account(account_id)['generation_issue']['code'] == 'video_duration_unsupported'
