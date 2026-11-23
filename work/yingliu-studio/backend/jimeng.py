"""Local jimeng2api bridge; gateway credentials never reach the browser."""
import asyncio
import json
import re
import secrets
from datetime import datetime, timezone

import httpx
from fastapi import Depends, HTTPException, Request, Response

from .db import DATA, db, now, encrypt_secret, decrypt_secret

BASE = 'http://127.0.0.1:8300'
GENERATE_URL = BASE + '/async/v1/images/generations'
JIMENG_VIDEO_RESOLUTIONS = ('480p', '720p', '1080p')
VIDEO_RATIOS = ('16:9', '9:16', '1:1', '4:3', '3:4', '21:9')
JIMENG_VIDEO_MODELS = (
    {'code': 'jimeng-video', 'name': '即梦 Seedance 2.0 mini', 'min': 4, 'max': 15},
    {'code': 'jimeng-video-fast', 'name': '即梦 Seedance 2.0 Fast', 'min': 4, 'max': 15},
    {'code': 'jimeng-video-pro', 'name': '即梦 Seedance 2.0', 'min': 4, 'max': 15},
    {'code': 'jimeng-video-25', 'name': '即梦 Seedance 2.5', 'min': 4, 'max': 30},
    {'code': 'jimeng-video-25-flash', 'name': '即梦 Seedance 2.5 Flash', 'min': 4, 'max': 15},
)
JIMENG_VIDEO_BY_CODE = {item['code']: item for item in JIMENG_VIDEO_MODELS}


def is_jimeng(model):
    return model['protocol'] == 'async_json' and model['api_url'] == GENERATE_URL


def jimeng_capabilities(model):
    if not is_jimeng(model):
        return None
    capability = JIMENG_VIDEO_BY_CODE.get(model['model_code'])
    if not capability:
        raise ValueError('即梦模型代号不受支持，请重新同步即梦模型')
    return {
        'duration_min': capability['min'],
        'duration_max': capability['max'],
        'duration_options': list(range(capability['min'], capability['max'] + 1)),
        'resolutions': list(JIMENG_VIDEO_RESOLUTIONS),
    }


def validate_jimeng_duration(model, duration):
    capability = jimeng_capabilities(model)
    if capability is None:
        return
    try:
        seconds = float(duration)
    except (TypeError, ValueError):
        raise ValueError('必须输入视频秒数')
    low, high = capability['duration_min'], capability['duration_max']
    if not seconds.is_integer():
        raise ValueError(f'该即梦模型支持 {low}–{high} 秒整数时长')
    if seconds < low or seconds > high:
        raise ValueError(f'当前即梦模型支持 {low}–{high} 秒视频')


def gateway_key():
    path = DATA / '.jimeng-gateway-key'
    if not path.exists():
        with path.open('x', encoding='utf-8') as f:
            f.write(encrypt_secret('sk-' + secrets.token_hex(24)))
    return decrypt_secret(path.read_text(encoding='utf-8'))


def validate_jimeng_resolution(model, resolution):
    capability = jimeng_capabilities(model)
    if capability and resolution not in capability['resolutions']:
        raise ValueError('当前即梦模型支持的分辨率：' + '、'.join(capability['resolutions']))


async def request_gateway(method, path, payload=None):
    try:
        async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
            response = await client.request(method, BASE + path, json=payload,
                headers={'Authorization': 'Bearer ' + gateway_key()})
    except httpx.RequestError as exc:
        raise HTTPException(503, '即梦号池服务未连接，请检查本地服务是否启动') from exc
    if response.is_error:
        try:
            error = response.json().get('error', {})
            message = error.get('message', '即梦服务请求失败') if isinstance(error, dict) else str(error)
        except ValueError:
            message = '即梦服务请求失败'
        raise HTTPException(response.status_code, message)
    return response.json()


def install_models():
    installed = []
    with db() as c:
        for item in JIMENG_VIDEO_MODELS:
            existing = c.execute('SELECT id,deleted FROM models WHERE api_url=? AND model_code=? ORDER BY id DESC LIMIT 1',
                                  (GENERATE_URL, item['code'])).fetchone()
            if existing:
                if not existing['deleted']:
                    c.execute('UPDATE models SET name=? WHERE id=?', (item['name'], existing['id']))
                    installed.append(existing['id'])
                continue
            cur = c.execute('''INSERT INTO models
                (name,model_code,kind,protocol,api_url,poll_url,auth_mode,auth_header,encrypted_key,
                request_template,result_path,task_id_path,status_path,completed_status,failed_status,
                cost,enabled,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (item['name'], item['code'], 'video', 'async_json', GENERATE_URL,
                 GENERATE_URL + '/{{task_id}}', 'none', '', '',
                 '{}', 'data.0.url', 'task_id', 'status', 'succeeded', 'failure', 10, 1, now()))
            installed.append(cur.lastrowid)
    return {'ids': installed, 'count': len(installed)}


def install_model():
    return install_models()


def _message_time(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    return str(value or now())


def _sync_admin_messages(accounts):
    with db() as c:
        for account in accounts:
            account_id = int(account['id'])
            account_name = str(account.get('name') or f'账号 {account_id}')
            verification = account.get('verification') or {}
            if verification.get('pending'):
                event_id = str(verification.get('id') or verification.get('created_at') or 'pending')
                event_at = _message_time(verification.get('created_at'))
                c.execute('''INSERT INTO admin_messages
                    (source_key,kind,account_id,account_name,title,body,event_at,created_at)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET
                    account_name=excluded.account_name,title=excluded.title,body=excluded.body,event_at=excluded.event_at''',
                    (f'verification:{account_id}:{event_id}', 'verification', account_id,
                     account_name, '账号验证', str(verification.get('message') or '即梦账号有待处理的验证事件。'),
                     event_at, now()))
            issue = account.get('generation_issue') or {}
            if issue:
                code = str(issue.get('code') or 'generation_error')
                event_id = str(issue.get('task_id') or issue.get('created_at') or issue.get('message') or 'latest')
                title = {'video_duration_unsupported': '视频时长',
                         'model_permission_required': '模型权益'}.get(code, '生成异常')
                details = [str(issue.get('message') or '即梦视频生成发生异常。')]
                if issue.get('model'):
                    details.append('模型：' + str(issue['model']))
                if issue.get('task_id'):
                    details.append('任务：' + str(issue['task_id']))
                event_at = _message_time(issue.get('created_at'))
                c.execute('''INSERT INTO admin_messages
                    (source_key,kind,account_id,account_name,title,body,event_at,created_at)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET
                    account_name=excluded.account_name,title=excluded.title,body=excluded.body,event_at=excluded.event_at''',
                    (f'generation:{account_id}:{code}:{event_id}', 'generation', account_id,
                     account_name, title, '\n'.join(details),
                     event_at, now()))


async def generate(model, job, context, set_provider_id):
    duration = context['duration']
    validate_jimeng_duration(model, duration)
    validate_jimeng_resolution(model, context['resolution'])
    if context['reference_video_base64'] or context.get('reference_audio_base64'):
        raise ValueError('当前即梦号池接口仅支持参考图片，请切换支持视频或音频参考的模型')
    task_id = job['provider_task_id']
    if not task_id:
        accounts = await request_gateway('GET', '/panel/api/accounts')
        if not any(a['enabled'] and a['state'] not in ('invalid', 'quota', 'confirmation') for a in accounts['items']):
            if any(a.get('verification', {}).get('pending') for a in accounts['items']):
                raise ValueError('即梦缺少生成内容使用确认（4010），号池已暂停接单。请管理员在号池的“生成诊断”中阅读并提交素材使用确认，无需到官网寻找登录验证码。')
            raise ValueError('即梦号池没有可用账号，请管理员先登录账号')
        payload = {k: context[k] for k in ('model', 'prompt', 'duration', 'ratio', 'resolution')}
        reference = context['reference_base64'] or context['reference_image']
        if reference:
            payload['image'] = reference
        if job.get('reference_images'):
            payload['image'] = job.pop('reference_images')
        if payload.get('image'):
            options = json.loads(job['options_json'])
            payload['reference_mode'] = options.get('reference_mode') or (
                'multi_image' if isinstance(payload['image'], list) else 'frames'
            )
        result = await request_gateway('POST', '/async/v1/images/generations', payload)
        task_id = result['task_id']
        set_provider_id(task_id)
        del payload, reference
    del context
    # Resume persisted tasks after a Studio restart; never resubmit a known task.
    for _ in range(21600):
        try:
            result = await request_gateway('GET', '/async/v1/images/generations/' + str(task_id))
        except HTTPException as exc:
            if exc.status_code == 429 or exc.status_code >= 500:
                await asyncio.sleep(4)
                continue
            raise
        if result['status'] == 'succeeded':
            from .adapters import normalize_result
            from .video_delivery import store_video
            return await store_video(job['id'], normalize_result(result['data']))
        if result['status'] in ('failure', 'failed'):
            error = result.get('error') or '请检查账号状态'
            if isinstance(error, dict):
                error = error.get('message') or error.get('code') or '请检查账号状态'
            raise ValueError('即梦生成失败：' + str(error))
        await asyncio.sleep(4)
    raise TimeoutError('即梦任务等待超过 24 小时，请管理员核对上游任务')


def register(app, admin_only):
    @app.post('/api/admin/jimeng/install-model')
    def add_model(user=Depends(admin_only)):
        return install_models()

    @app.get('/api/admin/jimeng/messages')
    async def list_admin_messages(user=Depends(admin_only)):
        gateway_error = None
        try:
            accounts = (await request_gateway('GET', '/panel/api/accounts')).get('items', [])
            _sync_admin_messages(accounts)
        except HTTPException as exc:
            gateway_error = str(exc.detail)
        with db() as c:
            messages = [dict(row) for row in c.execute(
                'SELECT * FROM admin_messages ORDER BY created_at DESC,id DESC LIMIT 200').fetchall()]
            unread = c.execute('SELECT COUNT(*) AS count FROM admin_messages WHERE read_at IS NULL').fetchone()['count']
        return {'items': messages, 'unread_count': unread, 'gateway_error': gateway_error}

    @app.post('/api/admin/jimeng/messages/read-all')
    def read_all_admin_messages(user=Depends(admin_only)):
        with db() as c:
            c.execute('UPDATE admin_messages SET read_at=? WHERE read_at IS NULL', (now(),))
        return {'ok': True}

    @app.post('/api/admin/jimeng/messages/{message_id}/read')
    def read_admin_message(message_id: int, user=Depends(admin_only)):
        with db() as c:
            changed = c.execute('UPDATE admin_messages SET read_at=? WHERE id=? AND read_at IS NULL',
                                (now(), message_id)).rowcount
            exists = c.execute('SELECT id FROM admin_messages WHERE id=?', (message_id,)).fetchone()
        if not exists:
            raise HTTPException(404, '消息不存在')
        return {'ok': True, 'changed': bool(changed)}

    @app.api_route('/api/admin/jimeng/{path:path}', methods=['GET', 'POST', 'PATCH', 'PUT', 'DELETE'])
    async def proxy(path: str, request: Request, user=Depends(admin_only)):
        allowed = {
            'accounts': {'GET', 'POST'},
            'settings': {'GET', 'PUT'},
            'login/qr/start': {'POST'},
        }
        methods = allowed.get(path, set())
        if re.fullmatch(r'accounts/\d+', path):
            methods = {'PATCH', 'DELETE'}
        elif re.fullmatch(r'accounts/\d+/probe', path):
            methods = {'POST'}
        elif re.fullmatch(r'accounts/\d+/verification/(start|input|close|resume|diagnose|consent)', path):
            methods = {'POST'}
        elif re.fullmatch(r'accounts/\d+/verification/details', path):
            methods = {'GET'}
        elif re.fullmatch(r'accounts/\d+/verification/[a-f0-9]+', path):
            methods = {'GET'}
        elif re.fullmatch(r'login/qr/[a-f0-9-]+', path):
            methods = {'GET'}
        elif re.fullmatch(r'login/qr/[a-f0-9-]+/cancel', path):
            methods = {'POST'}
        if request.method not in methods:
            raise HTTPException(404, '接口不存在')
        payload = await request.json() if request.method in ('POST', 'PATCH', 'PUT') else None
        result = await request_gateway(request.method, '/panel/api/' + path, payload)
        return Response(json.dumps(result, ensure_ascii=False), media_type='application/json',
                        headers={'Cache-Control': 'no-store'})
