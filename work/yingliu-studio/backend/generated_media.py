"""Save compatible provider output in Studio's persistent media directory."""
import asyncio
import re
from contextlib import suppress
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx

from .adapters import MEDIA, check_provider_url

MEDIA_LIMITS = {'image': 100 * 1024 * 1024, 'video': 500 * 1024 * 1024}
CHUNK_BYTES = 256 * 1024
DOWNLOAD_SECONDS = 180
FILE_TYPES = {
    'png': ('image', 'image/png'),
    'jpg': ('image', 'image/jpeg'),
    'webp': ('image', 'image/webp'),
    'mp4': ('video', 'video/mp4'),
    'webm': ('video', 'video/webm'),
}


def media_extension(prefix):
    if prefix.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if prefix.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if prefix.startswith(b'RIFF') and prefix[8:12] == b'WEBP':
        return 'webp'
    if prefix[4:8] == b'ftyp':
        return 'mp4'
    if prefix.startswith(b'\x1a\x45\xdf\xa3') and b'\x42\x82\x84webm' in prefix[:128]:
        return 'webm'
    return None


async def preserve_generated_media(job_id: str, kind: str, url: str) -> str:
    """Return a local file URL when possible, retaining the original URL on failure."""
    if (kind not in MEDIA_LIMITS or not url.startswith(('http://', 'https://'))
            or not re.fullmatch(r'[a-f0-9-]{16,64}', job_id)):
        return url

    temporary = None
    limit = MEDIA_LIMITS[kind]
    try:
        async with asyncio.timeout(DOWNLOAD_SECONDS):
            await asyncio.to_thread(check_provider_url, url)
            async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=15), trust_env=False,
                                         follow_redirects=False, headers={'Accept-Encoding': 'identity'}) as client:
                async with client.stream('GET', url) as response:
                    if (response.status_code != 200
                            or response.headers.get('content-encoding', 'identity').lower() != 'identity'):
                        return url
                    content_type = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
                    accepted_types = {mime for media_kind, mime in FILE_TYPES.values() if media_kind == kind}
                    if content_type and content_type not in accepted_types | {'application/octet-stream'}:
                        return url
                    length = response.headers.get('content-length', '')
                    if length.isdecimal() and int(length) > limit:
                        return url

                    total = 0
                    extension = None
                    with NamedTemporaryFile(dir=MEDIA, prefix=f'.{job_id}-', suffix='.part', delete=False) as output:
                        temporary = Path(output.name)
                        async for chunk in response.aiter_raw(chunk_size=CHUNK_BYTES):
                            if total == 0:
                                extension = media_extension(chunk)
                                if extension is None or FILE_TYPES[extension][0] != kind:
                                    return url
                                if content_type not in ('', 'application/octet-stream', FILE_TYPES[extension][1]):
                                    return url
                            total += len(chunk)
                            if total > limit:
                                return url
                            output.write(chunk)
        if extension is None or (length.isdecimal() and total != int(length)):
            return url
        destination = MEDIA / f'{job_id}.{extension}'
        temporary.replace(destination)
        return '/api/media/' + destination.name
    except (httpx.HTTPError, OSError, TimeoutError, ValueError):
        return url
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
