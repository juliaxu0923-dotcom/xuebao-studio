"""Keep requested video resolution unchanged through task creation and submission."""
import json

import pytest

from app import service
from app.upstream import video


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
