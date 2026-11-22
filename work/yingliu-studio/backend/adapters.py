"""Generic media model provider adapters (not an API protocol converter for every vendor)."""
import asyncio
import base64
import io
import ipaddress
import json
import os
import re
import socket
import shutil
from pathlib import Path
from urllib.parse import urlparse
from html import escape
import hashlib
import httpx
from PIL import Image, ImageDraw, ImageFont
from .db import DATA, decrypt_secret

MEDIA = DATA / 'media'
MEDIA.mkdir(parents=True, exist_ok=True)
TOKENS = re.compile(r'\{\{\s*([\w.]+)\s*\}\}')


def read_path(value, path):
    path = (path or '').lstrip('$.')
    for key in path.split('.') if path else []:
        if isinstance(value, dict):
            value = value[key]
        elif isinstance(value, list):
            value = value[int(key)]
        else:
            raise KeyError(f'不能从响应读取字段 {path}')
    return value


def apply_template(source, context):
    """Expand known {{field}} placeholders in JSON; preserve numbers on full-match tokens."""
    if isinstance(source, dict):
        return {k: apply_template(v, context) for k, v in source.items()}
    if isinstance(source, list):
        return [apply_template(v, context) for v in source]
    if not isinstance(source, str):
        return source
    full = TOKENS.fullmatch(source)
    if full:
        return context.get(full.group(1), '')
    return TOKENS.sub(lambda m: str(context.get(m.group(1), '')), source)


def check_provider_url(url):
    """Admin-supplied URLs: reject obvious SSRF destinations by default.

    Production environments should additionally use network egress firewall/DNS protections.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('API 地址必须是 http(s) URL，且不能包含账号密码')
    if parsed.scheme != 'https' and os.getenv('ALLOW_HTTP_PROVIDERS') != '1':
        raise ValueError('默认只允许 HTTPS 模型接口；内网测试可配置 ALLOW_HTTP_PROVIDERS=1')
    if os.getenv('ALLOW_PRIVATE_PROVIDER_URLS') == '1':
        return
    hostname = parsed.hostname.strip('[]').lower()
    if hostname in ('localhost',) or hostname.endswith(('.local', '.internal')):
        raise ValueError('不允许指向内网地址')
    try:
        addresses = socket.getaddrinfo(hostname, None)
        for item in addresses:
            ip = ipaddress.ip_address(item[4][0])
            if not ip.is_global:
                raise ValueError('不允许访问非公网 IP 地址')
    except socket.gaierror:
        raise ValueError('模型接口域名暂时无法解析')


def auth_headers(model):
    mode = model['auth_mode']
    key = decrypt_secret(model['encrypted_key'])
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
    if mode == 'bearer' and key:
        headers['Authorization'] = f'Bearer {key}'
    if mode == 'header' and key:
        header = model['auth_header']
        if not re.fullmatch(r'[A-Za-z0-9-]{1,64}', header):
            raise ValueError('不合法的 API Key Header')
        headers[header] = key
    return headers


def build_context(model, job, reference_path=None, reference_url=None):
    options = json.loads(job['options_json'])
    ratio = options.get('ratio', '9:16')
    duration = options.get('duration', 5)
    context = {
        'prompt': job['prompt'], 'model': model['model_code'],
        'ratio': ratio, 'duration': duration,
        'resolution': options.get('resolution', '720p'), 'n': options.get('image_count', 1),
        'reference_image': reference_url or '', 'reference_base64': '', 'reference_video_base64': '', 'reference_audio_base64': '',
    }
    if reference_path:
        p = Path(reference_path)
        if p.exists():
            mime = {'.png': 'image/png', '.webp': 'image/webp', '.mp4': 'video/mp4', '.webm': 'video/webm', '.mp3':'audio/mpeg', '.wav':'audio/wav'}.get(p.suffix.lower(), 'image/jpeg')
            value = f'data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}'
            context['reference_audio_base64' if mime.startswith('audio/') else 'reference_video_base64' if mime.startswith('video/') else 'reference_base64'] = value
    return context


def make_demo_image(job_id, prompt, ratio, is_video=False):
    sizes = {'9:16': (576, 1024), '16:9': (1024, 576), '1:1': (800, 800), '4:3': (960, 720)}
    width, height = sizes.get(ratio, (576, 1024))
    if is_video:
        width, height = (640, 360) if ratio == '16:9' else (360, 640)
    key = hashlib.sha256(prompt.encode()).digest()
    img = Image.new('RGB', (width, height))
    dr = ImageDraw.Draw(img)
    for y in range(height):
        t = y / max(1, height-1)
        dr.line([(0, y), (width, y)], fill=(
            int((21 + key[0]/8) * (1-t) + 10*t),
            int((22 + key[1]/10) * (1-t) + 22*t),
            int((54 + key[2]/5) * (1-t) + 80*t)))
    for i in range(9):
        x = int((i * 79 + key[3]) / 700 * width)
        y = int((i * 103 + key[4]) / 1000 * height)
        r = int(width * (0.04 + (i % 3) * 0.03))
        color = (50 + key[(i+5)%32]//3, 60+key[(i+6)%32]//4, 100+key[(i+7)%32]//3)
        dr.ellipse((x-r, y-r, x+r, y+r), outline=color, width=3)
    font = ImageFont.load_default()
    dr.rounded_rectangle((width*.08, height*.42, width*.92, height*.58), radius=14,
                         fill=(15, 20, 43), outline=(134, 116, 238), width=3)
    dr.text((width*.13, height*.46), 'DEMO PREVIEW', fill='white', font=font)
    dr.text((width*.13, height*.52), 'NOT AI GENERATED', fill=(202, 186, 250), font=font)
    filename = f'{job_id}.png'
    img.save(MEDIA / filename)
    return filename


async def make_demo_video(job_id, prompt, ratio):
    """Generate a short, explicitly watermarked animation for UI testing."""
    filename = make_demo_image(job_id, prompt, ratio, is_video=True)
    video_name = f'{job_id}.mp4'
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        from imageio_ffmpeg import get_ffmpeg_exe
        ffmpeg = get_ffmpeg_exe()
    cmd = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
           '-loop', '1', '-framerate', '12', '-i', str(MEDIA / filename),
           '-t', '3', '-vf', 'format=yuv420p', '-c:v', 'libx264', '-preset', 'ultrafast',
           '-movflags', '+faststart', str(MEDIA / video_name)]
    try:
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL,
                                                        stderr=asyncio.subprocess.PIPE)
        _, err = await asyncio.wait_for(process.communicate(), timeout=25)
        if process.returncode:
            raise RuntimeError(err.decode(errors='replace')[-400:])
        return video_name
    except (OSError, asyncio.TimeoutError) as exc:
        raise RuntimeError(f'演示视频需要 FFmpeg: {exc}') from exc


async def invoke_provider(model, job, reference_path, reference_url, set_provider_id):
    """Returns result URL or /api/media/filename. Async protocol polls for completion."""
    options = json.loads(job['options_json'])
    if model['protocol'] == 'demo':
        await asyncio.sleep(1)
        if job['kind'] == 'image':
            filename = make_demo_image(job['id'], job['prompt'], options.get('ratio', '9:16'))
        else:
            filename = await make_demo_video(job['id'], job['prompt'], options.get('ratio', '9:16'))
        return '/api/media/' + filename

    from .jimeng import generate, is_jimeng
    if is_jimeng(model):
        return await generate(model, job, build_context(model, job, reference_path, reference_url), set_provider_id)

    check_provider_url(model['api_url'])
    context = build_context(model, job, reference_path, reference_url)
    template = json.loads(model['request_template'])
    for field, label in (('reference_video_base64', '视频'), ('reference_audio_base64', '音频')):
        if context[field] and field not in TOKENS.findall(model['request_template']):
            raise ValueError(f'当前模型尚未配置{label}参考参数，请配置 {field}')
    payload = apply_template(template, context)
    headers = auth_headers(model)
    async with httpx.AsyncClient(timeout=httpx.Timeout(100.0, connect=15.0), follow_redirects=False) as client:
        provider_id = job['provider_task_id']
        if not provider_id:
            has_reference = reference_path or reference_url
            custom_reference = any(token.startswith('reference_') for token in TOKENS.findall(model['request_template']))
            image_edit = (has_reference and not custom_reference and model['model_code'].startswith('gpt-image')
                          and urlparse(model['api_url']).path.endswith('/images/generations'))
            if image_edit:
                if reference_path:
                    image_bytes = Path(reference_path).read_bytes()
                else:
                    check_provider_url(reference_url)
                    async with client.stream('GET', reference_url) as source:
                        source.raise_for_status()
                        chunks = []
                        total = 0
                        async for chunk in source.aiter_bytes():
                            total += len(chunk)
                            if total > 30 * 1024 * 1024:
                                raise ValueError('参考图片不能超过 30MB')
                            chunks.append(chunk)
                        image_bytes = b''.join(chunks)
                with Image.open(io.BytesIO(image_bytes)) as image:
                    image.thumbnail((2560, 2560))
                    buffer = io.BytesIO()
                    image.convert('RGB').save(buffer, format='PNG')
                edit_url = model['api_url'].replace('/images/generations', '/images/edits')
                edit_headers = {k: v for k, v in headers.items() if k.lower() != 'content-type'}
                resp = await client.post(edit_url, headers=edit_headers,
                                         data={k: str(v) for k, v in payload.items() if v is not None},
                                         files={'image': ('reference.png', buffer.getvalue(), 'image/png')})
            else:
                if has_reference and not custom_reference:
                    raise ValueError('当前模型尚未配置参考素材接口，请在模型配置中设置参考素材参数')
                resp = await client.post(model['api_url'], headers=headers, json=payload)
            resp.raise_for_status()
            body = resp.json()
            if model['protocol'] == 'sync_json':
                result = read_path(body, model['result_path'])
                return normalize_result(result)
            provider_id = str(read_path(body, model['task_id_path']))
            if not provider_id:
                raise ValueError('上游未返回任务 ID')
            set_provider_id(provider_id)
        if not model['poll_url']:
            raise ValueError('异步协议必须配置查询地址')
        for _ in range(180):
            await asyncio.sleep(4)
            url = model['poll_url'].replace('{{task_id}}', provider_id)
            check_provider_url(url)
            poll = await client.get(url, headers=headers)
            poll.raise_for_status()
            body = poll.json()
            state = str(read_path(body, model['status_path'])).lower()
            success = [v.strip().lower() for v in model['completed_status'].split(',')]
            failure = [v.strip().lower() for v in model['failed_status'].split(',')]
            if state in success:
                return normalize_result(read_path(body, model['result_path']))
            if state in failure:
                raise ValueError(f'上游任务失败：{json.dumps(body, ensure_ascii=False)[:500]}')
        raise TimeoutError('等待上游任务超过 12 分钟')


def normalize_result(result):
    if isinstance(result, list):
        if not result:
            raise ValueError('上游未返回媒体文件')
        result = result[0]
    if isinstance(result, dict):
        result = result.get('url') or result.get('image_url') or result.get('video_url')
    if not isinstance(result, str) or not result.startswith(('http://', 'https://')):
        raise ValueError('上游结果路径未返回 HTTP(S) 媒体 URL，请检查结果字段路径')
    return result
