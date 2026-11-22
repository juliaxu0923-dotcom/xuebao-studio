"""Verify video resolution delivery in an isolated database, without upstream generation."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent / 'yingliu-studio'))

with tempfile.TemporaryDirectory() as folder:
    os.environ['APP_DATA_DIR'] = folder
    os.environ['ADMIN_PASSWORD'] = 'resolution-test-password'
    from fastapi.testclient import TestClient
    from backend import main, jimeng
    from backend.adapters import build_context

    with TestClient(main.app) as client, patch.object(main, 'launch_job'):
        client.post('/api/auth/login', json={
            'username': 'admin', 'password': 'resolution-test-password',
        }).raise_for_status()
        models = client.get('/api/models?kind=video').json()
        models = [m for m in models if m['model_code'].startswith('jimeng-video')]
        assert len(models) == 5
        for model in models:
            assert model['resolutions'] == ['480p', '720p', '1080p']
            for resolution in model['resolutions']:
                response = client.post('/api/jobs', json={
                    'model_id': model['id'], 'prompt': 'resolution test',
                    'duration': 5, 'resolution': resolution,
                })
                response.raise_for_status()
                job = client.get('/api/jobs/' + response.json()['id']).json()
                assert job['options']['resolution'] == resolution
            for field, value in [('resolution_name', '1080p'), ('resolution_name', '1080'),
                                 ('resolution', '1080p'), ('resolution', '720p'),
                                 ('resolution_name', '480'), ('resolution', '480p')]:
                for multipart in (False, True):
                    payload = {'model': model['model_code'], 'prompt': 'canvas resolution test',
                               'seconds': '5', field: value}
                    kwargs = {'files': {k: (None, v) for k, v in payload.items()}} if multipart else {'json': payload}
                    response = client.post('/api/v1/videos', **kwargs)
                    response.raise_for_status()
                    job = client.get('/api/jobs/' + response.json()['data']['id']).json()
                    assert job['options']['resolution'] == value.removesuffix('p') + 'p'

        points = client.get('/api/me').json()['points']
        count = len(client.get('/api/jobs').json())
        for resolution in ['2K', '4K']:
            response = client.post('/api/jobs', json={
                'model_id': models[0]['id'], 'prompt': 'invalid resolution',
                'duration': 5, 'resolution': resolution,
            })
            assert response.status_code == 400, response.text
            response = client.post('/api/v1/videos', json={
                'model': 'jimeng-video', 'prompt': 'invalid resolution',
                'seconds': 5, 'resolution_name': resolution,
            })
            assert response.status_code == 400, response.text
        assert client.get('/api/me').json()['points'] == points
        assert len(client.get('/api/jobs').json()) == count

        model = main.one('SELECT * FROM models WHERE id=?', (models[0]['id'],))
        for resolution in ['480p', '720p', '1080p']:
            job = {'id': 'test', 'prompt': 'test', 'provider_task_id': '',
                   'options_json': json.dumps({'resolution': resolution, 'duration': 5})}
            gateway = AsyncMock(side_effect=[
                {'items': [{'enabled': True, 'state': 'ok'}]},
                {'task_id': 'test-provider'},
                {'status': 'succeeded', 'data': [{'url': 'https://example.com/test.mp4'}]},
            ])
            with patch.object(jimeng, 'request_gateway', gateway), patch(
                'backend.video_delivery.store_video', AsyncMock(return_value='/api/media/test.mp4')
            ):
                asyncio.run(jimeng.generate(model, job, build_context(model, job), lambda _: None))
            assert gateway.await_args_list[1].args[2]['resolution'] == resolution
    print('PASS: all five models expose 480p/720p/1080p; page and JSON/multipart canvas requests retain selection; '
          'gateway payload preserves resolution; invalid resolutions rejected before charging. No upstream generation.')
