"""Persist provider video output before reporting successful delivery."""
import os
import httpx
from .adapters import MEDIA, check_provider_url


async def store_video(job_id, url):
    check_provider_url(url)
    destination = MEDIA / (job_id + '.mp4')
    temporary = destination.with_suffix('.part')
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=20), trust_env=False) as client:
            async with client.stream('GET', url) as response:
                response.raise_for_status()
                total = 0
                prefix = b''
                with temporary.open('wb') as output:
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > 500 * 1024 * 1024:
                            raise ValueError('视频文件超过 500MB')
                        prefix = (prefix + chunk)[:32]
                        output.write(chunk)
                if total < 32 or prefix[4:8] != b'ftyp':
                    raise ValueError('即梦返回的内容不是有效 MP4 视频')
        os.replace(temporary, destination)
        return '/api/media/' + destination.name
    finally:
        temporary.unlink(missing_ok=True)
