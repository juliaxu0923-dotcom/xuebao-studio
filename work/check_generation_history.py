"""Check that Studio can redisplay older jobs and reopen locally saved provider output."""
import asyncio
import io
import os
import sys
import tempfile
from functools import partial
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent / 'yingliu-studio'))


def checked_json(response):
    response.raise_for_status()
    return response.json()


def login(client, username):
    client.post('/api/auth/login', json={'username': username, 'password': 'history-test-password'}).raise_for_status()
    return checked_json(client.get('/api/me'))['id']


class MediaResponse(httpx.AsyncByteStream):
    def __init__(self, content):
        self.content = content

    async def __aiter__(self):
        yield self.content


with tempfile.TemporaryDirectory(prefix='xuebao-history-') as folder:
    os.environ['APP_DATA_DIR'] = folder
    os.environ['ADMIN_USERNAME'] = 'admin'
    os.environ['ADMIN_PASSWORD'] = 'history-test-password'
    from backend import generated_media, main

    with TestClient(main.app) as client, patch.object(main, 'launch_job'):
        owner = login(client, 'admin')
        models = checked_json(client.get('/api/models'))
        image_model = next(model['id'] for model in models if model['model_code'] == 'demo-image')
        video_model = next(model['id'] for model in models if model['model_code'] == 'jimeng-video')
        timestamp = '2025-01-01T00:00:00+00:00'
        image_jobs = [f'{value:032x}' for value in range(111)]
        with main.db() as connection:
            guest = connection.execute('INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)',
                                       ('guest', main.hash_password('history-test-password'), timestamp)).lastrowid
            connection.executemany('''INSERT INTO jobs
                (id,user_id,model_id,kind,prompt,options_json,status,created_at,updated_at)
                VALUES(?,?,?,?,?,'{}','completed',?,?)''', [
                (job_id, owner, image_model, 'image', 'old image', timestamp, timestamp)
                for job_id in image_jobs
            ] + [('guest-job', guest, image_model, 'image', 'guest image', timestamp, timestamp),
                 ('owner-video', owner, video_model, 'video', 'old video', timestamp, timestamp)])

        assert len(checked_json(client.get('/api/jobs'))) == 100
        params = {'kind': 'image', 'limit': 100}
        latest = checked_json(client.get('/api/jobs', params=params))
        assert [job['id'] for job in latest] == image_jobs[-1:-101:-1]
        cursor = latest[-1]['id']
        client.delete('/api/jobs/' + cursor).raise_for_status()
        older = checked_json(client.get('/api/jobs', params={**params, 'before': cursor}))
        assert [job['id'] for job in older] == image_jobs[-101::-1]
        assert client.get('/api/jobs', params={'before': 'guest-job'}).status_code == 404
        assert client.get('/api/jobs', params={'kind': 'video', 'before': cursor}).status_code == 404

        project = {'id': 'history-canvas', 'title': 'History canvas', 'nodes': [{'id': 'node-one', 'type': 'video'}]}
        client.post('/api/v1/canvas/projects', json={'data': project}).raise_for_status()
        canvas_tasks = []
        for prompt in ('first', 'second'):
            task = checked_json(client.post('/api/v1/videos', json={'model': 'jimeng-video', 'prompt': prompt},
                headers={'X-Studio-Project-ID': project['id'], 'X-Video-Task-Source-ID': 'node-one'}))['data']
            canvas_tasks.append(task['id'])
        video_history = '/api/canvas/history-canvas/nodes/node-one/videos'
        assert [job['id'] for job in checked_json(client.get(video_history))] == canvas_tasks[::-1]

        image_bytes = io.BytesIO()
        with Image.new('RGB', (64, 32), 'navy') as picture:
            picture.save(image_bytes, format='PNG')

        def finish_job(model_id, upstream_url, content, mime, status=200):
            job_id = checked_json(client.post('/api/jobs', json={'model_id': model_id, 'prompt': 'archive test'}))['id']

            def respond(request):
                assert (request.method, str(request.url)) == ('GET', upstream_url)
                assert 'Authorization' not in request.headers
                return httpx.Response(status, headers={'Content-Type': mime}, stream=MediaResponse(content))

            transport = httpx.MockTransport(respond)
            with patch.object(main, 'invoke_provider', AsyncMock(return_value=upstream_url)), \
                 patch.object(generated_media, 'check_provider_url'), \
                 patch.object(generated_media.httpx, 'AsyncClient', partial(httpx.AsyncClient, transport=transport)):
                asyncio.run(main.execute_job(job_id))
            return checked_json(client.get('/api/jobs/' + job_id))

        result = finish_job(image_model, 'https://example.test/generation.png?token=temporary',
                            image_bytes.getvalue(), 'image/png')
        archived = result['id']
        media_url = f'/api/media/{archived}.png'
        assert (result['status'], result['result_url']) == ('completed', media_url)
        assert client.get(media_url).content == image_bytes.getvalue()
        video_bytes = b'\x00\x00\x00\x18ftypisom' + bytes(32)
        video = finish_job(video_model, 'https://example.test/generation.mp4', video_bytes, 'video/mp4')
        assert video['result_url'] == '/api/media/' + video['id'] + '.mp4'
        assert client.get(video['result_url']).content == video_bytes
        unreachable = 'https://example.test/unavailable.png'
        fallback = finish_job(image_model, unreachable, b'', 'image/png', status=404)
        assert (fallback['status'], fallback['result_url']) == ('completed', unreachable)

        login(client, 'guest')
        assert [job['id'] for job in checked_json(client.get('/api/jobs'))] == ['guest-job']
        assert client.get('/api/jobs', params={'before': archived}).status_code == 404
        assert client.get(video_history).status_code == 404
        assert client.get(media_url).status_code == 404

    with TestClient(main.app) as client, patch.object(main, 'launch_job'):
        login(client, 'admin')
        assert client.get(media_url).content == image_bytes.getvalue()
        assert [job['id'] for job in checked_json(client.get(video_history))] == canvas_tasks[::-1]

    print('Generation history passed across pagination, canvas reload, account changes, and server restart.')
