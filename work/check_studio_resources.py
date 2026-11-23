"""Verify the Studio image limit and adaptive worker queue without upstream requests."""
import asyncio
import io
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent / 'yingliu-studio'))


def png(mode, size):
    source = io.BytesIO()
    with Image.new(mode, size) as image:
        image.save(source, format='PNG')
    source.seek(0)
    return source


async def wait_until(condition):
    async with asyncio.timeout(3):
        while not condition():
            await asyncio.sleep(0.001)


with tempfile.TemporaryDirectory(prefix='xuebao-resources-') as folder:
    os.environ['APP_DATA_DIR'] = folder
    os.environ['ADMIN_PASSWORD'] = 'resource-check-password'
    from backend import adapters, main
    from backend.resource_usage import ResourceUsage

    with main.prepare_upload_image(png('RGBA', (4096, 4096))) as image:
        assert image.mode == 'RGB' and image.size == (2560, 2560)
    try:
        main.prepare_upload_image(png('1', (4097, 4096)))
        raise AssertionError('Oversized image was accepted')
    except HTTPException as exc:
        assert exc.status_code == 413

    async def check_jobs():
        main.setup_db()
        model_id = main.one('SELECT id FROM models LIMIT 1')['id']
        user_id = main.one("SELECT id FROM users WHERE role = 'admin'")['id']
        timestamp = main.now()
        with main.db() as db:
            db.executemany(
                '''INSERT INTO jobs
                   (id, user_id, model_id, kind, prompt, options_json, status, created_at, updated_at)
                   VALUES (?, ?, ?, 'video', 'resource check', '{}', ?, ?, ?)''',
                [(job, user_id, model_id, status, timestamp, timestamp) for job, status in
                 [('2-queued', 'queued'), ('1-resumed', 'running'), ('3-queued', 'queued')]],
            )

        state = main.app.state
        started = []
        gates = {job: asyncio.Event() for job in ('1-resumed', '2-queued', '3-queued')}
        usage = ResourceUsage(cpu_percent=95, available_memory_bytes=512 * 1024 * 1024)

        async def execute(job):
            started.append(job)
            await gates[job].wait()

        with patch.object(main, 'execute_job', execute), \
                patch.object(main, 'RESOURCE_SAMPLE_SECONDS', 0.01), \
                patch.object(main.ResourceMonitor, 'sample', lambda _: usage):
            async with main.app.router.lifespan_context(main.app):
                main.launch_job('2-queued')
                await wait_until(lambda: started == ['1-resumed'])
                assert state.job_concurrency == 1

                usage = ResourceUsage(cpu_percent=25, available_memory_bytes=512 * 1024 * 1024)
                await wait_until(lambda: started == ['1-resumed', '2-queued'])
                assert state.job_concurrency == state.running_jobs == 2

                usage = ResourceUsage(cpu_percent=25, available_memory_bytes=192 * 1024 * 1024)
                await wait_until(lambda: state.job_concurrency == 1)
                assert state.running_jobs == 2
                gates['1-resumed'].set()
                await wait_until(lambda: state.running_jobs == 1)
                assert started == ['1-resumed', '2-queued']

                gates['2-queued'].set()
                await wait_until(lambda: started == ['1-resumed', '2-queued', '3-queued'])
            assert state.running_jobs == 0

        name = await adapters.make_demo_video('resource-check', 'demo check', '16:9')
        with (adapters.MEDIA / name).open('rb') as video:
            assert video.read(8)[4:] == b'ftyp'
        assert not (adapters.MEDIA / 'resource-check.part.mp4').exists()

    asyncio.run(check_jobs())
    print('Studio image, adaptive queue, and single-threaded demo video checks passed.')
