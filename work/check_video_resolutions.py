"""Verify video generation parameters in an isolated database without upstream generation."""
import asyncio
import base64
import io
import json
import os
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from imageio_ffmpeg import read_frames
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent / 'yingliu-studio'))

with tempfile.TemporaryDirectory() as folder:
    os.environ['APP_DATA_DIR'] = folder
    os.environ['ADMIN_PASSWORD'] = 'resolution-test-password'
    from fastapi.testclient import TestClient
    from backend import main, jimeng
    from backend import adapters
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
                    'duration': 7, 'ratio': '3:4', 'resolution': resolution,
                })
                response.raise_for_status()
                job = client.get('/api/jobs/' + response.json()['id']).json()
                assert job['options']['resolution'] == resolution
                assert (job['options']['ratio'], job['options']['duration']) == ('3:4', 7)
            for field, value in [('resolution_name', '1080p'), ('resolution_name', '1080'),
                                 ('resolution', '1080p'), ('resolution', '720p'),
                                 ('resolution_name', '480'), ('resolution', '480p')]:
                for multipart in (False, True):
                    payload = {'model': model['model_code'], 'prompt': 'canvas resolution test',
                               'seconds': '7', 'size': '21:9', field: value}
                    kwargs = {'files': {k: (None, v) for k, v in payload.items()}} if multipart else {'json': payload}
                    response = client.post('/api/v1/videos', **kwargs)
                    response.raise_for_status()
                    job = client.get('/api/jobs/' + response.json()['data']['id']).json()
                    assert job['options']['resolution'] == value.removesuffix('p') + 'p'
                    assert (job['options']['ratio'], job['options']['duration']) == ('21:9', 7)

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

        def gateway_payload(job):
            path, url = main.prepare_job_references(model, job)
            gateway = AsyncMock(side_effect=[
                {'items': [{'enabled': True, 'state': 'ok'}]},
                {'task_id': 'test-provider'},
                {'status': 'succeeded', 'data': [{'url': 'https://example.com/test.mp4'}]},
            ])
            with patch.object(jimeng, 'request_gateway', gateway), patch(
                'backend.video_delivery.store_video', AsyncMock(return_value='/api/media/test.mp4')
            ):
                asyncio.run(jimeng.generate(model, job, build_context(model, job, path, url), lambda _: None))
            return gateway.await_args_list[1].args[2]

        for resolution in ('480p', '720p', '1080p'):
            job = {'id': 'test', 'user_id': 1, 'prompt': 'test', 'provider_task_id': '',
                   'options_json': json.dumps({'ratio': '21:9', 'resolution': resolution, 'duration': 7})}
            payload = gateway_payload(job)
            assert (payload['ratio'], payload['resolution'], payload['duration']) == ('21:9', resolution, 7)

        def upload_picture(color):
            stream = io.BytesIO()
            with Image.new('RGB', (24, 24), color) as image:
                image.save(stream, 'PNG')
            response = client.post('/api/uploads', files={'file': ('frame.png', stream.getvalue(), 'image/png')})
            response.raise_for_status()
            asset = response.json()
            main_url = asset['url']
            encoded = 'data:image/jpeg;base64,' + base64.b64encode(client.get(main_url).content).decode()
            return 'http://testserver' + main_url, encoded

        start_url, start_image = upload_picture('red')
        end_url, end_image = upload_picture('blue')
        for fields, mode, images in (
            ({'last_frame_url': end_url, 'first_frame_url': start_url}, 'frames', [start_image, end_image]),
            ({'last_frame_url': end_url}, 'end_frame', [end_image]),
            ({'input_reference[]': start_url}, 'multi_image', [start_image]),
        ):
            request = {'model': model['model_code'], 'prompt': 'reference role test', **fields}
            for kwargs in ({'json': request}, {'files': [(key, (None, value)) for key, value in request.items()]}):
                response = client.post('/api/v1/videos', **kwargs)
                response.raise_for_status()
                job = main.one('SELECT * FROM jobs WHERE id=?', (response.json()['data']['id'],))
                payload = gateway_payload(job)
                assert payload['reference_mode'] == mode
                assert payload['image'] == images

        with main.db() as connection:
            connection.execute(
                "INSERT INTO models(name, model_code, kind, protocol, created_at) VALUES(?, ?, 'video', 'sync_json', ?)",
                ('Custom video test', 'custom-video-test', main.now()),
            )
        for field, filename, mime, content, token in (
            ('video_reference[]', 'reference.mp4', 'video/mp4', b'\x00\x00\x00\x18ftypisom\x00', 'reference_video_base64'),
            ('audio_reference[]', 'reference.wav', 'audio/wav', b'RIFF\x04\x00\x00\x00WAVE', 'reference_audio_base64'),
        ):
            asset = client.post('/api/uploads', files={'file': (filename, content, mime)}).json()
            response = client.post('/api/v1/videos', json={
                'model': 'custom-video-test', 'prompt': 'media reference test',
                field: ['http://testserver' + asset['url']],
            })
            response.raise_for_status()
            job = main.one('SELECT * FROM jobs WHERE id=?', (response.json()['data']['id'],))
            custom_model = main.one('SELECT * FROM models WHERE id=?', (job['model_id'],))
            path, url = main.prepare_job_references(custom_model, job)
            context = build_context(custom_model, job, path, url)
            assert context[token] == f'data:{mime};base64,' + base64.b64encode(content).decode()

        demo = asyncio.run(adapters.make_demo_video(
            'demo-video-parameters', 'test', '3:4', duration=7, resolution='720p'))
        with closing(read_frames(str(adapters.MEDIA / demo))) as frames:
            metadata = next(frames)
            assert metadata['size'] == (720, 960)
            assert metadata['duration'] == 7

        def respond(request):
            body = json.loads(request.content)
            assert (body['aspect_ratio'], body['duration'], body['resolution']) == ('21:9', 7, '1080p')
            return httpx.Response(200, json={'data': [{'url': 'https://example.test/video.mp4'}]})

        generic = {
            'kind': 'video', 'model_code': 'example-video', 'protocol': 'sync_json',
            'api_url': 'https://example.test/videos', 'auth_mode': 'none', 'encrypted_key': '',
            'request_template': json.dumps(adapters.DEFAULT_IMAGE_TEMPLATE), 'result_path': 'data.0.url',
        }
        job = {'kind': 'video', 'id': 'test', 'prompt': 'test', 'provider_task_id': '',
               'options_json': json.dumps({'ratio': '21:9', 'resolution': '1080p', 'duration': 7})}
        create_client = httpx.AsyncClient
        with patch.object(adapters, 'check_provider_url'), patch.object(adapters.httpx, 'AsyncClient',
                lambda **kw: create_client(transport=httpx.MockTransport(respond), **kw)):
            asyncio.run(adapters.invoke_provider(generic, job, None, None, lambda _: None))
    print('PASS: Studio video model, ratio, duration, resolutions, ordered references and actual demo dimensions. '
          'No upstream generation.')
