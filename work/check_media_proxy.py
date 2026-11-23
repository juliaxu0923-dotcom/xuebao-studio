"""Verify the canvas media proxy through its ASGI endpoint without an upstream server."""

import asyncio
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from starlette.requests import ClientDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parent / 'yingliu-studio'))


class UpstreamBody(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.started = False
        self.closed = False

    async def __aiter__(self):
        self.started = True
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


async def check_media_proxy(folder):
    from backend import infinite_canvas as canvas

    provider = 'https://media.example'
    contents = b'abcd' * 40
    app = FastAPI()
    canvas.register(app, {
        'whoami': lambda: {'id': 1},
        'one': lambda sql, args: {'id': 1} if args[-1].startswith(provider + '/') else None,
        'allrows': lambda *args: [],
        'check_provider_url': lambda url: None,
        'MEDIA': folder,
    })
    make_client, make_spool = httpx.AsyncClient, canvas.SpooledTemporaryFile
    upstream_bodies, spools = [], []

    def upstream(request):
        assert request.headers['Accept-Encoding'] == 'identity'
        headers = {'content-type': 'video/mp4; ignored=1'}
        chunks = [contents[:80], contents[80:]]
        if request.url.path == '/known-oversize':
            headers['content-length'] = str(len(contents) + 1)
        elif request.url.path == '/unknown-oversize':
            chunks.append(b'!')
        body = UpstreamBody(chunks)
        upstream_bodies.append(body)
        return httpx.Response(200, headers=headers, stream=body)

    def upstream_client(**kwargs):
        assert not kwargs['follow_redirects']
        return make_client(transport=httpx.MockTransport(upstream), **kwargs)

    def spool_file(**kwargs):
        spool = make_spool(**kwargs)
        spools.append(spool)
        return spool

    async with make_client(transport=httpx.ASGITransport(app=app), base_url='http://studio') as client:
        with (patch.object(canvas.httpx, 'AsyncClient', upstream_client),
              patch.object(canvas, 'SpooledTemporaryFile', spool_file),
              patch.object(canvas, 'MEDIA_PROXY_CHUNK_BYTES', 32),
              patch.object(canvas, 'MEDIA_PROXY_MAX_BYTES', len(contents))):
            for path, status, started in (
                ('/ok', 200, True), ('/known-oversize', 413, False), ('/unknown-oversize', 413, True),
            ):
                response = await client.get('/api/proxy-image', params={'url': provider + path})
                assert response.status_code == status, response.text
                assert upstream_bodies[-1].started is started
                assert upstream_bodies[-1].closed and spools[-1].closed
                if status == 200:
                    assert response.content == contents
                    assert response.headers['content-type'] == 'video/mp4'
                    assert response.headers['content-length'] == str(len(contents))
                    assert spools[-1]._rolled

            unowned = await client.get('/api/proxy-image', params={'url': 'https://unowned.example/file'})
            assert unowned.status_code == 404 and len(upstream_bodies) == 3

            endpoint = next(route.endpoint for route in app.routes if route.path == '/api/proxy-image')
            response = await endpoint(provider + '/ok', {'id': 1})

            async def receive():
                return {'type': 'http.disconnect'}

            async def send(message):
                if message['type'] == 'http.response.body':
                    raise OSError('client disconnected')

            try:
                await response({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send)
            except (ClientDisconnect, OSError):
                pass
            assert spools[-1].closed

    assert not list(folder.iterdir())


if __name__ == '__main__':
    with TemporaryDirectory(prefix='xuebao-media-proxy-') as directory:
        os.environ['APP_DATA_DIR'] = directory
        folder = Path(directory) / 'media'
        folder.mkdir()
        asyncio.run(check_media_proxy(folder))
    print('Canvas media proxy checks passed.')
