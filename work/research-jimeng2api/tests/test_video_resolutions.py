"""Keep requested video resolution unchanged through task creation and submission."""
import json

import pytest

from app import db, repo, service
from app.upstream import video
from app.upstream.client import JimengClient


@pytest.mark.parametrize('model', list(video.MODELS))
@pytest.mark.parametrize('resolution', ['480p', '720p', '1080p'])
def test_resolution_reaches_upstream_unchanged(monkeypatch, model, resolution):
    from app import pool as pool_mod

    task = service.create_task(api_key_id=None, model=model, prompt='resolution test',
                               size='', count=1, duration=5, resolution=resolution, ratio='16:9')
    assert task['resolution'] == resolution
    assert not task['degradations']
    seen = {}

    class FakeClient:
        def submit_video(self, **kwargs):
            seen.update(kwargs)
            return 'resolution-test'

    class FakeRuntime:
        client = FakeClient()

    monkeypatch.setattr(pool_mod.pool, 'runtime', lambda account_id: FakeRuntime())
    service._submit(1, task, [])
    assert seen['resolution'] == resolution
    body = video.build_submit_body(video.get(model), submit_id='resolution-test', prompt='test',
                                   duration_ms=5000, resolution=seen['resolution'], aspect_ratio='16:9')
    draft = json.loads(body['draft_content'])
    params = draft['component_list'][0]['abilities']['gen_video']['text_to_video_params']
    assert params['video_gen_inputs'][0]['resolution'] == resolution


def test_default_resolution_remains_720p():
    task = service.create_task(api_key_id=None, model='jimeng-video', prompt='test', size='', count=1)
    assert task['resolution'] == '720p'


@pytest.mark.parametrize(('requested', 'has_full_hd', 'delivered'), [
    ('480p', True, '480p'), ('720p', True, '720p'), ('1080p', False, '720p'), ('', True, '1080p'),
])
def test_poll_delivers_requested_rendition_when_available(monkeypatch, requested, has_full_hd, delivered):
    task = service.create_task(api_key_id=None, model='jimeng-video', prompt='test',
                               size='', count=1, resolution=requested)
    task_id = task['id']
    account_id = repo.add_account('resolution-test-session')
    repo.mark_task_running(task_id, account_id, forecast=None)
    db.execute('UPDATE tasks SET next_poll_at=0 WHERE id=?', (task_id,))
    renditions = {
        '480p': {'video_url': 'https://cdn/480p.mp4', 'width': 854, 'height': 480},
        '720p': {'video_url': 'https://cdn/720p.mp4', 'width': 1280, 'height': 720},
    }
    if has_full_hd:
        renditions['1080p'] = {'video_url': 'https://cdn/1080p.mp4', 'width': 1920, 'height': 1080}

    def fake_post(self, path, body):
        assert body['submit_ids'] == [task_id]
        return {'data': {task_id: {
            'task': {'status': 50},
            'item_list': [{'video': {'transcoded_video': renditions}}],
        }}}

    monkeypatch.setattr(JimengClient, '_post', fake_post)
    service.poll_once()
    finished = repo.get_task(task_id)
    assert finished['status'] == 'succeeded'
    assert finished['images'][0]['resolution'] == delivered
    assert finished['images'][0]['url'] == f'https://cdn/{delivered}.mp4'
