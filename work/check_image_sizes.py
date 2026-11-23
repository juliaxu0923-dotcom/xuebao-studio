"""Verify Studio image dimensions without sending paid generation requests."""
import asyncio
import io
import json
import os
import sys
import tempfile
from functools import partial
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent / 'yingliu-studio'))


def checked_json(response):
    response.raise_for_status()
    return response.json()


def check_demo_result(client, job_id, dimensions):
    asyncio.run(main.execute_job(job_id))
    job = checked_json(client.get('/api/jobs/' + job_id))
    assert job['status'] == 'completed', job['error']
    response = client.get(job['result_url'])
    response.raise_for_status()
    with Image.open(io.BytesIO(response.content)) as result:
        assert result.size == dimensions, (result.size, dimensions)
    return job['result_url'], response.content


def check_canvas_autosync(client, url, source):
    files = {'file': ('result.png', source, 'image/png')}
    original = checked_json(client.post('/api/v1/files', files={
        **files, 'generated_image_url': (None, url),
    }))['data']
    response = client.get(original['url'])
    response.raise_for_status()
    assert response.content == source
    assert (original['width'], original['height']) == (3840, 2160)

    ordinary = checked_json(client.post('/api/v1/files', files=files))['data']
    assert (ordinary['width'], ordinary['height']) == (2560, 1440)


def submit_canvas(client, fields, multipart):
    fields = {'model': 'demo-image', 'prompt': 'canvas image size check', **fields}
    if multipart:
        return client.post('/api/v1/canvas/image-tasks',
                           files={name: (None, value) for name, value in fields.items()})
    return client.post('/api/v1/canvas/image-tasks', json={'request': fields})


def check_provider_request(job_id):
    job = main.one('SELECT * FROM jobs WHERE id=?', (job_id,))
    model = {
        'kind': 'image', 'protocol': 'sync_json', 'model_code': 'example-image',
        'api_url': 'https://example.test/v1/images/generations',
        'auth_mode': 'none', 'encrypted_key': '', 'result_path': 'data.0.url',
        'request_template': json.dumps({
            'model': '{{model}}', 'prompt': '{{prompt}}', 'aspect_ratio': '{{ratio}}',
            'size': '{{size}}', 'width': '{{width}}', 'height': '{{height}}',
            'resolution': '{{resolution}}',
        }),
    }
    result_url = 'https://example.test/result.png'

    def respond(request):
        assert str(request.url) == model['api_url']
        assert json.loads(request.content) == {
            'model': 'example-image', 'prompt': job['prompt'], 'aspect_ratio': '16:9',
            'size': '3840x2160', 'width': 3840, 'height': 2160, 'resolution': '4K',
        }
        return httpx.Response(200, json={'data': [{'url': result_url}]})

    mock_client = partial(httpx.AsyncClient, transport=httpx.MockTransport(respond))
    with patch.object(adapters, 'check_provider_url'), patch.object(adapters.httpx, 'AsyncClient', mock_client):
        result = asyncio.run(adapters.invoke_provider(model, job, None, None, lambda _: None))
    assert result == result_url


with tempfile.TemporaryDirectory(prefix='xuebao-image-sizes-') as folder:
    os.environ['APP_DATA_DIR'] = folder
    os.environ['ADMIN_USERNAME'] = 'admin'
    os.environ['ADMIN_PASSWORD'] = 'image-size-test-password'
    from backend import adapters, main

    with TestClient(main.app) as client, patch.object(main, 'launch_job'):
        client.post('/api/auth/login', json={
            'username': 'admin', 'password': 'image-size-test-password',
        }).raise_for_status()
        models = checked_json(client.get('/api/models?kind=image'))
        model_id = next(model['id'] for model in models if model['model_code'] == 'demo-image')

        for resolution, dimensions in [('1K', (576, 1024)), ('2K', (1152, 2048)), ('4K', (2304, 4096))]:
            job_id = checked_json(client.post('/api/jobs', json={
                'model_id': model_id, 'prompt': 'image resolution check',
                'ratio': '9:16', 'resolution': resolution,
            }))['id']
            check_demo_result(client, job_id, dimensions)

        for multipart in (False, True):
            for size, ratio, resolution in [('3840x2160', '16:9', '4K'), ('768x1024', '3:4', '1K'),
                                            ('1360x768', '16:9', '1K')]:
                task = checked_json(submit_canvas(client, {'size': size}, multipart))['data']
                job = checked_json(client.get('/api/jobs/' + task['id']))
                assert job['options']['size'] == size
                assert job['options']['ratio'] == ratio
                assert job['options']['resolution'] == resolution
                if size == '3840x2160' and not multipart:
                    check_provider_request(task['id'])
                    url, source = check_demo_result(client, task['id'], (3840, 2160))
                    check_canvas_autosync(client, url, source)

            task = checked_json(submit_canvas(client, {
                'aspect_ratio': '3:4', 'resolution': '2k',
            }, multipart))['data']
            job = checked_json(client.get('/api/jobs/' + task['id']))
            assert job['options']['ratio'] == '3:4'
            assert job['options']['resolution'] == '2K'

            for invalid_size in ('0x1024', '1024xbad', '8192x8192'):
                response = submit_canvas(client, {'size': invalid_size}, multipart)
                assert response.status_code == 400, response.text

print('Image sizes passed for the standalone page, canvas JSON and multipart submissions, '
      'canvas result sync, and provider templates.')
