"""Cookie-authenticated adapter for the user's Infinite Canvas 0.8.9 renderer."""
import io
import json
import re
from urllib.parse import urlparse
from pathlib import Path
from tempfile import SpooledTemporaryFile

import httpx
from fastapi import Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from starlette.concurrency import run_in_threadpool

from .db import db, now, ROOT

RUNTIME = ROOT.parent / 'infinite-inspect/resources/app/web'
RENDERER = 'http://127.0.0.1:8001'
ASSET_PICKER_CHUNKS = ('10h2fnm8xrvhb', '16bxll2rpzygx', '1i__p954nri0t', '3907tj1oyvi8n')
MEDIA_PROXY_MAX_BYTES = 100 * 1024 * 1024
MEDIA_PROXY_CHUNK_BYTES = 256 * 1024


class _SpooledMediaResponse(StreamingResponse):
    def __init__(self, spool, size, media_type):
        self._spool = spool
        super().__init__(
            iter(lambda: spool.read(MEDIA_PROXY_CHUNK_BYTES), b''),
            media_type=media_type,
            headers={'Content-Length': str(size)},
        )

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._spool.close()


def setup_canvas_bridge():
    with db() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS infinite_canvases (
            project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            canvas_id TEXT NOT NULL UNIQUE, body TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS infinite_user_data (
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(user_id,name))''')


def envelope(data):
    return {'code': 0, 'data': data, 'msg': ''}


def legacy_canvas(p):
    graph = json.loads(p['graph_json'])
    return {'id': str(p['id']), 'title': p['title'], 'createdAt': p['updated_at'],
            'updatedAt': p['updated_at'], 'nodes': [
                {'id': n['id'], 'type': n['type'], 'title': n.get('title', ''),
                 'position': {'x': n.get('x', 0), 'y': n.get('y', 0)}, 'width': 280, 'height': 240,
                 'metadata': {'content': n.get('content', '') if n['type'] == 'text' else n.get('src', ''),
                              'prompt': n.get('prompt', ''),
                              'status': {'completed': 'success', 'failed': 'error', 'running': 'loading'}.get(n.get('status'), 'idle')}}
                for n in graph.get('nodes', [])],
            'connections': [{'id': e['id'], 'fromNodeId': e['source'], 'toNodeId': e['target']} for e in graph.get('edges', [])],
            'chatSessions': [], 'activeChatId': None, 'agentConfig': None, 'autoTitlePending': False,
            'backgroundMode': 'dots', 'showImageInfo': False, 'viewport': {'x': 80, 'y': 100, 'k': 1},
            'sidePanel': {'open': False, 'width': 280}, 'agentPanel': {'open': False, 'width': 390}}


def register(app, api):
    whoami, one, rows = api['whoami'], api['one'], api['allrows']

    def owned_canvas(cid, user):
        p = one('''SELECT p.* FROM projects p LEFT JOIN infinite_canvases i ON i.project_id=p.id
                   WHERE p.user_id=? AND (i.canvas_id=? OR (i.canvas_id IS NULL AND CAST(p.id AS TEXT)=?))''',
                (user['id'], cid, cid))
        if not p:
            raise HTTPException(404, '画布不存在或无权访问')
        return p

    def project_list(user):
        projects = rows('SELECT * FROM projects WHERE user_id=? ORDER BY updated_at DESC', (user['id'],))
        result = []
        for p in projects:
            saved = one('SELECT body FROM infinite_canvases WHERE project_id=?', (p['id'],))
            body = json.loads(saved['body']) if saved else legacy_canvas(p)
            body['title'] = p['title']
            result.append(body)
        return result

    def link_video_jobs(connection, body, project_id, user_id):
        for node in body.get('nodes', []):
            if node.get('type') != 'video':
                continue
            job_id = node.get('metadata', {}).get('videoTaskId')
            if not job_id:
                continue
            connection.execute('''UPDATE jobs SET options_json=json_set(options_json,'$.canvas_node_id',?)
                WHERE id=? AND project_id=? AND user_id=? AND kind='video'
                AND json_extract(options_json,'$.canvas_node_id') IS NULL''',
                (node['id'], job_id, project_id, user_id))

    def save_canvas(body, user):
        cid = str(body.get('id', ''))
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', cid) or not isinstance(body.get('nodes'), list):
            raise HTTPException(400, '画布数据格式错误')
        encoded = json.dumps(body, ensure_ascii=False)
        if len(encoded.encode()) > 10 * 1024 * 1024:
            raise HTTPException(413, '画布数据不能超过 10MB')
        title = str(body.get('title') or '未命名画布')[:80]
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            existing = c.execute('SELECT p.id,p.user_id FROM projects p JOIN infinite_canvases i ON i.project_id=p.id WHERE i.canvas_id=?', (cid,)).fetchone()
            if not existing and cid.isdigit():
                existing = c.execute('SELECT id,user_id FROM projects WHERE id=?', (int(cid),)).fetchone()
            if existing and existing['user_id'] != user['id']:
                raise HTTPException(404, '画布不存在或无权修改')
            if existing:
                pid = existing['id']
                previous = c.execute('SELECT body FROM infinite_canvases WHERE project_id=?', (pid,)).fetchone()
                if previous:
                    link_video_jobs(c, json.loads(previous['body']), pid, user['id'])
                c.execute('UPDATE projects SET title=?,updated_at=? WHERE id=?', (title, now(), pid))
            else:
                pid = c.execute('INSERT INTO projects(user_id,title,graph_json,updated_at) VALUES(?,?,?,?)',
                                (user['id'], title, '{"nodes":[],"edges":[]}', now())).lastrowid
            c.execute('INSERT INTO infinite_canvases(project_id,canvas_id,body) VALUES(?,?,?) ON CONFLICT(project_id) DO UPDATE SET body=excluded.body', (pid, cid, encoded))
            link_video_jobs(c, body, pid, user['id'])
            c.execute('COMMIT')
        return body

    @app.get('/api/canvas/{canvas_id}/nodes/{node_id}/videos')
    def video_history(canvas_id: str, node_id: str, user=Depends(whoami)):
        project = owned_canvas(canvas_id, user)
        saved = one('SELECT body FROM infinite_canvases WHERE project_id=?', (project['id'],))
        body = json.loads(saved['body']) if saved else legacy_canvas(project)
        node = next((item for item in body['nodes'] if item['id'] == node_id and item['type'] == 'video'), None)
        previous_job_id = (node or {}).get('metadata', {}).get('videoTaskId', '')
        return [api['present_job'](job) for job in rows('''
            SELECT j.*,m.name AS model_name,m.model_code FROM jobs j JOIN models m ON m.id=j.model_id
            WHERE j.user_id=? AND j.project_id=? AND j.kind='video' AND j.deleted=0
            AND (json_extract(j.options_json,'$.canvas_node_id')=? OR j.id=?)
            ORDER BY j.created_at DESC,j.rowid DESC''', (user['id'], project['id'], node_id, previous_job_id))]

    @app.get('/api/auth/me')
    def current_user(user=Depends(whoami)):
        return envelope({**user, 'id': str(user['id']), 'displayName': user['username'],
                         'credits': user['points'], 'avatarUrl': '', 'createdAt': '', 'updatedAt': ''})

    @app.get('/api/settings')
    def settings(user=Depends(whoami)):
        models = rows('SELECT id,name,kind,model_code,cost FROM models WHERE enabled=1 AND deleted=0')
        image = [m['model_code'] for m in models if m['kind'] == 'image']
        video = [m['model_code'] for m in models if m['kind'] == 'video']
        channels = []
        for m in models:
            channels.append({'id': str(m['id']), 'name': m['name'], 'protocol': 'openai', 'baseUrl': '/api',
                             'models': [m['model_code']], 'modelCapabilities': {m['model_code']: m['kind']},
                             'weight': 1, 'timeout': 300, 'enabled': True, 'remark': '', 'workflows': []})
        return envelope({'modelChannel': {'availableModels': image + video, 'availableWorkflows': [],
                         'modelCosts': [{'model': m['model_code'], 'credits': m['cost']} for m in models],
                         'channels': channels, 'defaultModel': next(iter(image), ''), 'defaultImageModel': next(iter(image), ''),
                         'defaultVideoModel': next(iter(video), ''), 'defaultTextModel': '', 'systemPrompt': '',
                         'systemPrompts': {'image': '', 'video': '', 'text': '', 'workflow': '', 'workflowAgent': ''},
                         'allowCustomChannel': False, 'allowUserRemoteChannel': False},
                         'auth': {'allowRegister': False, 'linuxDo': {'enabled': False}},
                         'storage': {'mode': 'server_sqlite_s3', 'allowUserProvider': False}})

    @app.get('/api/storage/config')
    def storage_config(user=Depends(whoami)):
        return envelope({'mode': 'server_sqlite_s3', 'allowUserProvider': False,
                         'allowUserGlobalProvider': True, 'autoSyncAllAssets': True})

    @app.get('/api/agent-skills')
    def empty_agent_skills(user=Depends(whoami)):
        return envelope([])

    @app.get('/api/files/{asset_id}/content')
    def file_content(asset_id: int, user=Depends(whoami)):
        asset = api['assert_owner']('assets', asset_id, user['id'])
        return api['serve_media'](asset['filename'], user)

    @app.get('/api/files/{asset_id}')
    def file_info(asset_id: int, user=Depends(whoami)):
        asset = api['assert_owner']('assets', asset_id, user['id'])
        return envelope({'url': '/api/media/' + asset['filename'], 'storageKey': 'server:' + str(asset_id)})

    @app.get('/api/proxy-image')
    async def proxy_generated_media(url: str, user=Depends(whoami)):
        if not one('SELECT id FROM jobs WHERE user_id=? AND result_url=?', (user['id'], url)):
            raise HTTPException(404, '只能读取当前账号生成的媒体')
        api['check_provider_url'](url)
        # Finish validation before sending headers so oversized downloads can still return 413.
        spool = SpooledTemporaryFile(max_size=MEDIA_PROXY_CHUNK_BYTES, dir=api['MEDIA'])
        try:
            async with httpx.AsyncClient(
                timeout=60, follow_redirects=False, headers={'Accept-Encoding': 'identity'},
            ) as client:
                async with client.stream('GET', url) as result:
                    result.raise_for_status()
                    length = result.headers.get('content-length', '')
                    if (result.headers.get('content-encoding', 'identity').lower() == 'identity'
                            and length.isdecimal() and int(length) > MEDIA_PROXY_MAX_BYTES):
                        raise HTTPException(413, '媒体文件过大')
                    content_type = result.headers.get('content-type', 'application/octet-stream').split(';')[0]
                    if not content_type.startswith(('image/', 'video/')):
                        raise HTTPException(400, '上游未返回图片或视频')
                    size = 0
                    async for chunk in result.aiter_bytes(chunk_size=MEDIA_PROXY_CHUNK_BYTES):
                        size += len(chunk)
                        if size > MEDIA_PROXY_MAX_BYTES:
                            raise HTTPException(413, '媒体文件过大')
                        await run_in_threadpool(spool.write, chunk)
            spool.seek(0)
            return _SpooledMediaResponse(spool, size, content_type)
        except BaseException:
            spool.close()
            raise

    async def upload(file, user):
        result = await api['upload_image'](file, user)
        asset = api['assert_owner']('assets', result['id'], user['id'])
        path = api['MEDIA'] / asset['filename']
        width = height = 0
        if asset['mime'].startswith('image/'):
            with Image.open(path) as image:
                width, height = image.size
        return {**result, 'storageKey': 'server:' + str(result['id']), 'width': width,
                'height': height, 'bytes': path.stat().st_size, 'mimeType': asset['mime']}

    def task_view(job, kind):
        model = one('SELECT model_code FROM models WHERE id=?', (job['model_id'],))
        return {'id': job['id'], 'status': job['status'], 'progress': 100 if job['status'] == 'completed' else 0,
                'model': model['model_code'], 'prompt': job['prompt'], 'source': 'canvas',
                'source_id': str(job['project_id'] or ''), 'created_at': job['created_at'],
                'image_url' if kind == 'image' else 'video_url': job.get('result_url') or '',
                'url': job.get('result_url') or '', 'error': {'message': job.get('error') or ''}}

    @app.api_route('/api/v1/{path:path}', methods=['GET', 'POST', 'DELETE'])
    async def canvas_api(path: str, request: Request, user=Depends(whoami)):
        method = request.method
        content_type = request.headers.get('content-type', '')
        form = await request.form() if 'multipart/form-data' in content_type else None
        body = {} if form is not None or method != 'POST' else await request.json()
        if path == 'canvas/projects' and method == 'GET':
            return envelope(project_list(user))
        if path == 'canvas/projects' and method == 'POST':
            return envelope(save_canvas(body['data'], user))
        if path == 'canvas/projects/sync' and method == 'POST':
            # Server state wins on bootstrap; stale browser caches must not resurrect deleted projects.
            return envelope(project_list(user))
        if path == 'canvas/projects/delete' and method == 'POST':
            for cid in body.get('ids', []):
                p = owned_canvas(str(cid), user)
                api['delete_project'](p['id'], user)
            return envelope({'deleted': True})
        if path == 'canvas/tasks/delete' and method == 'POST':
            owned_canvas(str(body.get('source_id', '')), user)
            # Node deletion is saved with the graph; generated works remain in My Works.
            return envelope({'deleted': True})
        if path == 'user-config':
            return envelope({'modelConfig': {'channelMode': 'remote'}, 'syncCapabilities': {'userData': True, 'workflows': False, 'assets': True}})
        if path.startswith('user-data/') or path == 'user-config/model':
            if method == 'POST':
                encoded = json.dumps(body.get('data', body))
                if len(encoded) > 10 * 1024 * 1024:
                    raise HTTPException(413, '数据过大')
                with db() as c:
                    c.execute('INSERT INTO infinite_user_data VALUES(?,?,?) ON CONFLICT(user_id,name) DO UPDATE SET body=excluded.body', (user['id'], path, encoded))
                return envelope(body.get('data', body))
            saved = one('SELECT body FROM infinite_user_data WHERE user_id=? AND name=?', (user['id'], path))
            return envelope(json.loads(saved['body']) if saved else None)
        if path == 'files' and method == 'POST':
            return envelope(await upload(form['file'], user))
        if path.startswith('files/') and method == 'DELETE':
            # Canvas removal does not destroy media still referenced by another project or job.
            api['assert_owner']('assets', path.split('/')[-1], user['id'])
            return envelope(True)
        if method == 'GET' and path in ('workflows', 'channels', 'channel-translations', 'canvas/image-tasks', 'video-tasks', 'agent-skills'):
            return envelope([])
        if method == 'GET' and (path.startswith('canvas/image-tasks/') or path.startswith('videos/')):
            kind = 'image' if path.startswith('canvas/') else 'video'
            job = api['assert_owner']('jobs', path.split('/')[-1], user['id'])
            return envelope(task_view(job, kind))
        if method == 'POST' and path in ('canvas/image-tasks', 'videos'):
            kind = 'image' if path.startswith('canvas/') else 'video'
            data = dict(form) if form is not None else body.get('request', body)
            model = one('SELECT * FROM models WHERE model_code=? AND kind=? AND enabled=1 AND deleted=0', (data.get('model'), kind))
            channel_id = request.headers.get('X-Model-Channel-ID') or body.get('channelId') or (form.get('_canvas_channel_id') if form is not None else None)
            if channel_id:
                model = one('SELECT * FROM models WHERE id=? AND model_code=? AND kind=? AND enabled=1 AND deleted=0', (channel_id, data.get('model'), kind))
            if not model:
                raise HTTPException(400, '模型未配置或已停用')
            cid = request.headers.get('X-Studio-Project-ID') or (form.get('_canvas_source_id') if form is not None else body.get('sourceId'))
            project = owned_canvas(str(cid), user) if cid else None
            node_id = request.headers.get('X-Video-Task-Source-ID') if kind == 'video' else None
            if node_id and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', node_id):
                raise HTTPException(400, '视频节点标识无效')
            if project and kind == 'video':
                saved = one('SELECT body FROM infinite_canvases WHERE project_id=?', (project['id'],))
                if saved:
                    with db() as c:
                        link_video_jobs(c, json.loads(saved['body']), project['id'], user['id'])
            files = [value for _, value in form.multi_items() if hasattr(value, 'filename')] if form is not None else []
            reference_fields = ('input_reference[]', 'first_frame_url', 'last_frame_url', 'image', 'image_url', 'video_url', 'audio_url', 'reference_video', 'reference_audio')
            addresses = []
            for key in reference_fields:
                values = form.getlist(key) if form is not None else data.get(key, [])
                if not isinstance(values, list):
                    values = [values]
                addresses.extend(value for value in values if isinstance(value, str) and value)
            from .jimeng import is_jimeng
            multi = len(files) + len(addresses) > 1
            if multi and not is_jimeng(model):
                raise HTTPException(400, '当前模型尚未接入多素材参考')
            if len(files) + len(addresses) > 9:
                raise HTTPException(400, '多人物参考最多支持 9 张图片')
            reference_inputs = []
            if multi:
                for file in files:
                    reference_inputs.append({'asset_id': (await upload(file, user))['id']})
                for address in addresses:
                    path = urlparse(address).path
                    asset = one('SELECT id FROM assets WHERE user_id=? AND filename=?', (user['id'], path.rsplit('/',1)[-1])) if path.startswith('/api/media/') else None
                    if asset:
                        reference_inputs.append({'asset_id': asset['id']})
                    elif re.fullmatch(r'/api/files/\d+/content', path):
                        reference_inputs.append({'asset_id': api['assert_owner']('assets', path.split('/')[3], user['id'])['id']})
                    else:
                        source = one("SELECT id FROM jobs WHERE user_id=? AND result_url=? AND status='completed' AND kind='image'", (user['id'], address))
                        if not source:
                            raise HTTPException(400, '请先将参考图片上传到雪宝')
                        reference_inputs.append({'job_id': source['id']})
                files, addresses = [], []
            ref_id = (await upload(files[0], user))['id'] if files else None
            ref_job_id = None
            if addresses:
                address = addresses[0]
                path = urlparse(address).path
                asset = one('SELECT id FROM assets WHERE user_id=? AND filename=?', (user['id'], path.rsplit('/', 1)[-1])) if path.startswith('/api/media/') else None
                if asset:
                    ref_id = asset['id']
                elif re.fullmatch(r'/api/files/\d+/content', path):
                    ref_id = api['assert_owner']('assets', path.split('/')[3], user['id'])['id']
                else:
                    source = one("SELECT id FROM jobs WHERE user_id=? AND result_url=? AND status='completed' AND kind='image'", (user['id'], address))
                    if not source:
                        raise HTTPException(400, '请先将参考图片上传到雪宝')
                    ref_job_id = source['id']
            size = str(data.get('size') or data.get('aspect_ratio') or '1:1')
            ratio = size if size in ('1:1', '9:16', '16:9', '4:3') else '1:1'
            if 'x' in size:
                try:
                    w, h = map(int, size.split('x'))
                    ratio = min(('1:1', '9:16', '16:9', '4:3'), key=lambda r: abs(w / h - int(r.split(':')[0]) / int(r.split(':')[1])))
                except (ValueError, ZeroDivisionError):
                    pass
            seconds = data.get('seconds', data.get('duration', 5))
            if seconds is None or str(seconds).strip() == '':
                raise HTTPException(400, '必须输入视频秒数')
            try:
                seconds = float(seconds)
            except (ValueError, TypeError):
                raise HTTPException(400, '必须输入视频秒数')
            if seconds > 30:
                raise HTTPException(400, '需要在30秒以内')
            if not 0 < seconds <= 30:
                raise HTTPException(400, '视频秒数必须大于0')
            from .jimeng import is_jimeng
            if is_jimeng(model):
                from .jimeng import validate_jimeng_duration
                try:
                    validate_jimeng_duration(model, seconds)
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from exc
                if ref_id:
                    reference = api['assert_owner']('assets', ref_id, user['id'])
                    if not reference['mime'].startswith('image/'):
                        raise HTTPException(400, '当前即梦号池模型仅支持图片首帧参考，视频和音频参考尚未接入')
            resolution = 'auto'
            if kind == 'video':
                resolution = str(data.get('resolution_name') or data.get('resolution') or '720p').strip().lower()
                if resolution in ('480', '720', '1080'):
                    resolution += 'p'
                if resolution not in ('480p', '720p', '1080p'):
                    raise HTTPException(400, '视频分辨率无效')
            job = await api['create_job'](api['CreateJob'](project_id=project['id'] if project else None,
                canvas_node_id=node_id if project else None,
                model_id=model['id'], prompt=str(data.get('prompt') or body.get('prompt') or ''), ratio=ratio,
                duration=seconds,
                resolution=resolution, reference_asset_id=ref_id, reference_job_id=ref_job_id, reference_inputs=reference_inputs), user)
            return envelope(task_view(api['assert_owner']('jobs', job['id'], user['id']), kind))
        return Response(json.dumps({'code': 400, 'msg': '此功能尚未接入雪宝，请使用图片或视频生成', 'data': None}), status_code=400, media_type='application/json')

    async def renderer(request: Request, user):
        path = request.url.path
        if path.startswith('/canvas/studio-'):
            pid = path.removeprefix('/canvas/studio-')
            api['assert_owner']('projects', pid, user['id'])
            link = one('SELECT canvas_id FROM infinite_canvases WHERE project_id=?', (pid,))
            return RedirectResponse('/canvas/' + (link['canvas_id'] if link else pid))
        # The renderer serves only a generic shell; project APIs enforce ownership.
        # A just-created local canvas may navigate here before its first save finishes.
        headers = {k: v for k, v in request.headers.items() if k.lower() in ('rsc', 'next-router-state-tree', 'next-router-prefetch', 'next-url', 'accept')}
        async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
            try:
                result = await client.get(RENDERER + path, params=request.query_params, headers=headers)
            except httpx.HTTPError:
                return HTMLResponse('画布服务未启动，请重新启动雪宝 Studio。', status_code=503)
        content = result.content
        content_type = result.headers.get('content-type', 'text/html')
        # Next assets use immutable caching. A patched bundle needs a new URL,
        # including references inside the server-rendered hydration payload.
        content = content.replace(b'0iqtaf0ai17gn.js', b'0iqtaf0ai17gn-studio-copy-v2.js')
        content = content.replace(b'1_tj89_8ipyt2.js', b'1_tj89_8ipyt2-studio-model-names-v1.js')
        content = content.replace(b'20g6bk7ya_8li.js', b'20g6bk7ya_8li-studio-model-names-v1.js')
        for name in ASSET_PICKER_CHUNKS:
            content = content.replace((name + '.js').encode(), (name + '-studio-assets-v1.js').encode())
        for name in ('1e3il75m4m7o5', '2ln7-76yya1ga'):
            content = content.replace((name + '.js').encode(), (name + '-studio-video-v2.js').encode())
        if 'text/html' in content_type:
            bootstrap = '<link rel="stylesheet" href="/static/infinite-bridge.css?v=model-names-v1"><script src="/static/infinite-bridge.js" data-user="' + str(user['id']) + '"></script>'
            bootstrap += '<link rel="stylesheet" href="/static/canvas-reference-picker.css?v=5"><script src="/static/canvas-reference-picker.js?v=5"></script>'
            content = content.replace(b'<head>', ('<head>' + bootstrap).encode(), 1)
        return Response(content, status_code=result.status_code, headers={'content-type': content_type, 'cache-control': 'no-store'})

    @app.get('/canvas')
    @app.get('/canvas/{canvas_id}')
    async def canvas_page(request: Request, user=Depends(whoami)):
        return await renderer(request, user)

    if RUNTIME.exists():
        @app.get('/_next/static/chunks/{name}-studio-assets-v1.js')
        def studio_asset_picker(name: str):
            if name not in ASSET_PICKER_CHUNKS:
                raise HTTPException(404)
            source = (RUNTIME / '.next/static/chunks' / (name + '.js')).read_text(encoding='utf-8')
            # Only personal assets get removal controls; library cards retain insertion.
            pattern = r'\(0,t\.jsx\)\((\w+),\{title:(\w+)\.title,kind:\2\.kind,cover:\2\.coverUrl'
            source, count = re.subn(pattern, lambda match: (
                '(0,t.jsx)(StudioRemovableAssetCard,{studioCard:' + match[1] + ',studioAsset:' + match[2]
                + ',title:' + match[2] + '.title,kind:' + match[2] + '.kind,cover:' + match[2] + '.coverUrl'
            ), source)
            marker = 'e.s(["AssetPickerModal",0,function('
            if count != 1 or source.count(marker) != 1:
                raise HTTPException(503, '画布版本已变化，需要更新素材移除适配')
            component = (ROOT / 'web/canvas-asset-card.js').read_text(encoding='utf-8')
            source = source.replace(marker, component + '\n' + marker, 1)
            return Response(source, media_type='application/javascript', headers={'cache-control': 'no-store'})

        @app.get('/_next/static/chunks/10h2fnm8xrvhb.js')
        @app.get('/_next/static/chunks/16bxll2rpzygx.js')
        @app.get('/_next/static/chunks/1i__p954nri0t.js')
        @app.get('/_next/static/chunks/3907tj1oyvi8n.js')
        def studio_asset_picker_original(request: Request):
            return studio_asset_picker(request.url.path.rsplit('/', 1)[-1][:-3])

        @app.get('/_next/static/chunks/{name}-studio-video-v1.js')
        @app.get('/_next/static/chunks/{name}-studio-video-v2.js')
        def studio_video_settings(name: str):
            if name not in ('1e3il75m4m7o5', '2ln7-76yya1ga'):
                raise HTTPException(404)
            source = (RUNTIME / '.next/static/chunks' / (name + '.js')).read_text(encoding='utf-8')
            start = source.index('(0,t.jsxs)("div",{className:"grid grid-cols-[1fr_auto_1fr] items-center gap-2.5",children:[(0,t.jsx)(_,{prefix:"W"')
            end = source.index('(0,t.jsx)("div",{className:"grid grid-cols-3 gap-2.5",children:l.seedanceRatioOptions', start)
            source = source[:start] + source[end:]
            source = source.replace('e.videoSeconds||"6"', 'e.videoSeconds??"6"')
            source = source.replace('value:e,onChange:e=>n(e.target.value),onBlur:e=>a?.(e.target.value)', '''value:e,"aria-label":"视频秒数",step:"any",required:true,onChange:e=>{e.target.setCustomValidity("");n(e.target.value)},onBlur:e=>{const v=e.target.value;const error=v.trim()===""?"必须输入视频秒数":Number(v)>30?"需要在30秒以内":Number(v)<=0?"视频秒数必须大于0":"";e.target.setCustomValidity(error);if(error)e.target.reportValidity()}''')
            return Response(source, media_type='application/javascript', headers={'cache-control':'no-store'})

        @app.get('/_next/static/chunks/1e3il75m4m7o5.js')
        @app.get('/_next/static/chunks/2ln7-76yya1ga.js')
        def studio_video_settings_original(request: Request):
            return studio_video_settings(request.url.path.rsplit('/',1)[-1][:-3])

        @app.get('/_next/static/chunks/0iqtaf0ai17gn.js')
        @app.get('/_next/static/chunks/0iqtaf0ai17gn-studio-ref-v3.js')
        @app.get('/_next/static/chunks/0iqtaf0ai17gn-studio-ref-v4.js')
        @app.get('/_next/static/chunks/0iqtaf0ai17gn-studio-ref-v5.js')
        @app.get('/_next/static/chunks/0iqtaf0ai17gn-studio-ref-v6.js')
        @app.get('/_next/static/chunks/0iqtaf0ai17gn-studio-copy-v1.js')
        @app.get('/_next/static/chunks/0iqtaf0ai17gn-studio-copy-v2.js')
        @app.get('/_next/static/chunks/0iqtaf0ai17gn-studio-ref-v2.js')
        def studio_reference_picker():
            source = (RUNTIME / '.next/static/chunks/0iqtaf0ai17gn.js').read_text(encoding='utf-8')
            copy_callbacks = [
                (r'aR=\(0,s\.useCallback\)\(e=>\{let t=nV\.current\.find\(t=>t\.id===e\);.*?\},\[\]\),aL=',
                 '''aR=(0,s.useCallback)(id=>{
                    const original=nV.current.find(node=>node.id===id);
                    if(!original)return;
                    const sources=nV.current.filter(node=>node.id===id||original.type===e9.Group&&node.metadata?.groupId===id);
                    const copied=studioCopyCanvasNodes(sources,nV.current,{x:36,y:36},()=> (0,$.nanoid)());
                    nV.current=[...nV.current,...copied.nodes];nB.current=[...nB.current,...copied.connections];
                    ee(nV.current);ei(nB.current);eI(new Set([copied.nodes.find(node=>node.type===original.type).id]));
                    eA(null);tO(original.type===e9.Group?null:copied.nodes[0].id);
                 },[]),aL='''),
                (r'aV=\(0,s\.useCallback\)\(\(\)=>\{let e=f\.current;.*?\},\[n2\]\),aO=',
                 '''aV=(0,s.useCallback)(()=>{
                    const clipboard=f.current;if(!clipboard?.nodes.length)return false;
                    const center=n2(),bounds=td(clipboard.nodes);
                    const offset={x:center.x-(bounds.left+bounds.right)/2,y:center.y-(bounds.top+bounds.bottom)/2};
                    const copied=studioCopyCanvasNodes(clipboard.nodes,nV.current,offset,()=> (0,$.nanoid)());
                    nV.current=[...nV.current,...copied.nodes];nB.current=[...nB.current,...copied.connections];
                    ee(nV.current);ei(nB.current);eI(new Set(copied.nodes.map(node=>node.id)));
                    eA(null);e1(null);tO(copied.nodes[0]?.type===e9.Group?null:copied.nodes[0]?.id||null);return true;
                 },[n2]),aO='''),
            ]
            for pattern, replacement in copy_callbacks:
                source, count = re.subn(pattern, lambda match: replacement, source)
                if count != 1:
                    raise HTTPException(503, '画布复制关联适配需要更新')
            copy_helper = (ROOT / 'web/canvas-copy.js').read_text(encoding='utf-8')
            source = source.replace('function xl(', copy_helper + '\nfunction xl(', 1)
            duration_summary = 'videoSeconds:(0,et.isAutoDLConfig)({...d,activeChannelId:w,videoChannelId:y},c.metadata?.model||p)?c.metadata?.seconds??d.videoSeconds:c.metadata?.seconds||d.videoSeconds||en.defaultConfig.videoSeconds'
            duration_display = '''videoSeconds:(()=>{const m=String(c.metadata?.model||p||""),v=(0,et.isAutoDLConfig)({...d,activeChannelId:w,videoChannelId:y},c.metadata?.model||p)?c.metadata?.seconds??d.videoSeconds:c.metadata?.seconds||d.videoSeconds||en.defaultConfig.videoSeconds;if(!m.startsWith("jimeng-video"))return v;const n=Number(v),max=m==="jimeng-video-25"?30:15;return String(Number.isFinite(n)?Math.max(4,Math.min(max,n)):4)})()'''
            if source.count(duration_summary) != 1:
                raise HTTPException(503, '画布即梦视频时长摘要适配需要更新')
            source = source.replace(duration_summary, duration_display)
            duration_label = '(0,eW.videoSecondsLabel)(e.videoSeconds)'
            duration_label_clamped = '(0,eW.videoSecondsLabel)(String(k).startsWith("jimeng-video")?String(Number.isFinite(Number(e.videoSeconds))?Math.max(4,Math.min(k==="jimeng-video-25"?30:15,Number(e.videoSeconds))):4):e.videoSeconds)'
            if source.count(duration_label) != 1:
                raise HTTPException(503, '画布即梦视频时长显示适配需要更新')
            source = source.replace(duration_label, duration_label_clamped)
            needle = 'onStartReferenceSelection:ig'
            replacement = '''onStartReferenceSelection:async targetId=>{
                const target=nV.current.find(node=>node.id===targetId);
                if(!target)return;
                const connected=new Set((aN.get(targetId)||[]).map(node=>node.id));
                const choice=await window.studioPickReference(nV.current.filter(node=>node.id!==targetId&&!connected.has(node.id)&&pG(node)),{video:target.type===e9.Video});
                if(!choice)return;
                if(!nV.current.some(node=>node.id===targetId))return;
                for(const selectedFile of choice.files||[choice.file]){
                if(selectedFile)choice.file=selectedFile;
                let sourceId=choice.nodeId;
                if(choice.file){
                    const hide=o.loading("正在上传参考素材...",0);
                    try{
                        const kind=choice.file.type.startsWith("video/")?"video":choice.file.type.startsWith("audio/")||/\\.(mp3|wav)$/i.test(choice.file.name)?"audio":"image";
                        const image=kind==="image"?await (0,eg.uploadImage)(choice.file):await (0,ef.uploadMediaFile)(choice.file,kind);
                        if(!nV.current.some(node=>node.id===targetId))return;
                        const size=kind==="audio"?tv[e9.Audio]:e5(image.width||640,image.height||360);
                        sourceId=(0,$.nanoid)();
                        const offset=(choice.files||[]).indexOf(selectedFile);
                        const node={id:sourceId,type:kind,title:choice.file.name,position:{x:target.position.x-size.width-96,y:target.position.y+Math.max(0,offset)*(size.height+24)},...size,metadata:kind==="audio"?yx(image):kind==="video"?yp(image):yh(image)};
                        ee(nodes=>[...nodes,node]);
                    }catch(error){o.error(choice.file.name+"："+(error.message||"参考上传失败，请重试"));continue;}finally{hide();}
                }else if(!nV.current.some(node=>node.id===sourceId&&pG(node)))return;
                ei(edges=>edges.some(edge=>edge.fromNodeId===sourceId&&edge.toNodeId===targetId)?edges:[...edges,{id:(0,$.nanoid)(),fromNodeId:sourceId,toNodeId:targetId}]);
                }
                eI(new Set([targetId]));tO(targetId);nz(null);
            }'''
            if source.count(needle) != 1:
                raise HTTPException(503, '画布版本已变化，需要更新参考选择适配')
            source = source.replace(needle, replacement)
            needle = 'iB=(0,s.useCallback)(async(e,n,a)=>{'
            guard = '''iB=(0,s.useCallback)(async(e,n,a)=>{if(n==="video"){const node=nV.current.find(t=>t.id===e);const value=node?.metadata?.videoSeconds??z.videoSeconds??"6";const seconds=Number(value);const error=String(value).trim()===""?"必须输入视频秒数":seconds>30?"需要在30秒以内":!Number.isFinite(seconds)||seconds<=0?"视频秒数必须大于0":"";if(error){o.error(error);return;}}'''
            if source.count(needle) != 1:
                raise HTTPException(503, '画布视频校验适配需要更新')
            source = source.replace(needle, guard)
            video_patches = [
                ('(0,r.jsx)(xl,{node:e,isRunning:e8===e.id',
                 '(0,r.jsx)(StudioVideoPromptPanel,{node:e,isRunning:e8===e.id'),
                ('iB=(0,s.useCallback)(async(e,n,a)=>{',
                 'iB=(0,s.useCallback)((e,n,a)=>studioRunVideoGeneration(e,n,nV,async()=>{'),
                ('finally{tn(null)}},[z,R]),i$=', 'finally{tn(null)}}),[z,R]),i$='),
                ('p=i?.type===e9.Video&&!i.metadata?.content,x=p?e:',
                 'p=i?.type===e9.Video,x=p?e:'),
                ('iW=(0,s.useCallback)(async e=>{var n;',
                 'iW=(0,s.useCallback)(async e=>{if(e.type===e9.Video)return iB(e.id,"video",e.metadata?.prompt||"");var n;'),
                ('i(e.id,M,n),P||L("")', 'i(e.id,M,n),P||"video"===M||L("")'),
                ('"aria-label":"生成",children:(0,r.jsxs)',
                 '"aria-label":"video"===M&&(e.metadata?.videoTaskId||e.metadata?.content)?"重新生成":"生成",title:"video"===M&&(e.metadata?.videoTaskId||e.metadata?.content)?"重新生成":"生成",children:(0,r.jsxs)'),
                ('{var p,f,g,x;let y,v,b,w,k,C,j,S=',
                 '{t=t||e.type===e9.Video&&e.metadata?.status==="loading";var p,f,g,x;let y,v,b,w,k,C,j,S='),
                ('"data-canvas-no-zoom":!0,className:"rounded-2xl border p-3 shadow-2xl backdrop-blur",style:{background:T.toolbar.panel',
                 '"data-canvas-no-zoom":!0,className:"rounded-2xl border p-3 shadow-2xl backdrop-blur"+("video"===M?" studio-video-prompt-panel":""),style:{background:T.toolbar.panel'),
                (',(0,r.jsx)(tp.Modal,{title:"编辑提示词",open:F',
                 ',"video"===M?(0,r.jsx)(StudioVideoHistory,{node:e,busy:t,onConfigChange:a,theme:T}):null,(0,r.jsx)(tp.Modal,{title:"编辑提示词",open:F'),
                ('ep=[...ed?ec?',
                 'ep=[...en&&!ed&&e.metadata?.status!=="loading"?[{id:"regenerateVideo",title:"重新生成",label:"重新生成",icon:(0,r.jsx)(gg,{className:"size-4"}),onClick:()=>P(e)}]:[],...ed?ec?'),
            ]
            for needle, replacement in video_patches:
                if source.count(needle) != 1:
                    raise HTTPException(503, '画布视频记录适配需要更新')
                source = source.replace(needle, replacement)
            component = (ROOT / 'web/canvas-video-history.js').read_text(encoding='utf-8')
            source = source.replace('function xl(', component + '\nfunction xl(', 1)
            source = source.replace('node?.metadata?.videoSeconds??z.videoSeconds', 'node?.metadata?.seconds??z.videoSeconds')
            source = source.replace('t?.metadata?.seconds||e.videoSeconds||en.defaultConfig.videoSeconds', 't?.metadata?.seconds??e.videoSeconds??en.defaultConfig.videoSeconds')
            source = source.replace('c.metadata?.seconds||d.videoSeconds||en.defaultConfig.videoSeconds', 'c.metadata?.seconds??d.videoSeconds??en.defaultConfig.videoSeconds')
            source = source.replace('g.metadata?.seconds||f.videoSeconds||en.defaultConfig.videoSeconds', 'g.metadata?.seconds??f.videoSeconds??en.defaultConfig.videoSeconds')
            for name in ('1e3il75m4m7o5', '2ln7-76yya1ga'):
                source = source.replace(name + '.js', name + '-studio-video-v2.js')
            return Response(source, media_type='application/javascript', headers={'cache-control': 'no-store'})

        @app.get('/_next/static/chunks/1_tj89_8ipyt2.js')
        @app.get('/_next/static/chunks/1_tj89_8ipyt2-studio-resolutions-v1.js')
        @app.get('/_next/static/chunks/1_tj89_8ipyt2-studio-resolutions-v2.js')
        @app.get('/_next/static/chunks/1_tj89_8ipyt2-studio-model-names-v1.js')
        @app.get('/_next/static/chunks/20g6bk7ya_8li.js')
        @app.get('/_next/static/chunks/20g6bk7ya_8li-studio-model-names-v1.js')
        def studio_model_channels(request: Request):
            name = request.url.path.rsplit('/', 1)[-1]
            name = name.replace('-studio-resolutions-v1', '').replace('-studio-resolutions-v2', '').replace('-studio-model-names-v1', '')
            source = (RUNTIME / '.next/static/chunks' / name).read_text(encoding='utf-8')
            # Restore account channels and remove the desktop build's single-vendor video filter.
            if name == '1_tj89_8ipyt2.js':
                from .jimeng import JIMENG_VIDEO_MODELS, JIMENG_VIDEO_RESOLUTIONS
                supported = ','.join(json.dumps({
                    'value': item['code'], 'label': item['name'],
                    'durations': [str(value) for value in range(item['min'], item['max'] + 1)],
                    'resolutions': list(JIMENG_VIDEO_RESOLUTIONS), 'maxReferences': 9,
                }, ensure_ascii=False, separators=(',', ':')) for item in JIMENG_VIDEO_MODELS)
                patches = [
                    ('})(e,a,!1),[e,a])', '})(e,a,!0),[e,a])'),
                    ('maxReferences:9}],p=["1:1","3:4","4:3","9:16","16:9","21:9"];',
                     'maxReferences:9},' + supported + '],p=["1:1","3:4","4:3","9:16","16:9","21:9"];'),
                    ('normalizeToken51VideoDuration",0,function(e,t){let a=v(e);return a.durations.some(e=>e===t)?t:a.durations[0]}',
                     'normalizeToken51VideoDuration",0,function(e,t){let a=v(e),n=String(t??"");if(a.durations.some(e=>e===n))return n;let r=Number(n),i=a.durations.map(Number);return String(Number.isFinite(r)?Math.max(i[0],Math.min(i[i.length-1],r)):i[0])}'),
                ]
            else:
                patches = [
                    ('"video"===l?n.filter(e=>e.protocol===g.TOKEN51_VIDEO_PROTOCOL&&(0,g.isToken51VideoModel)(e.model)):l?', 'l?'),
                    ('function x({model:e,label:n,channelName:r}){return(0,t.jsxs)("span",{className:"flex min-w-0 items-center gap-2",children:[(0,t.jsx)(b,{model:e}),(0,t.jsx)("span",{className:"truncate",title:e,children:n||(0,g.token51VideoModelLabel)(e)}),r?(0,t.jsx)("span",{className:"ml-auto max-w-24 shrink-0 truncate text-xs opacity-50",children:r}):null]})}',
                     'function x({model:e,label:n,channelName:r}){let o=(0,g.token51VideoModelLabel)(e),i=o!==e?o:n||o;return(0,t.jsxs)("span",{className:"studio-model-option",children:[(0,t.jsx)(b,{model:e}),(0,t.jsxs)("span",{className:"studio-model-option-labels",children:[(0,t.jsx)("span",{className:"studio-model-option-name",title:i,children:i}),r&&r!==i?(0,t.jsx)("span",{className:"studio-model-option-channel text-xs opacity-50",children:r}):null]})]})}'),
                    ('(0,t.jsxs)(u,{className:(0,i.cn)("canvas-composer-model-picker',
                     '(0,t.jsxs)(u,{"data-studio-model-kind":l,className:(0,i.cn)("canvas-composer-model-picker'),
                    ('"data-[state=open]:border-ring data-[state=open]:ring-2 data-[state=open]:ring-ring/20",m)',
                     '"data-[state=open]:border-ring data-[state=open]:ring-2 data-[state=open]:ring-ring/20","video"===l?m?.replace("!w-[180px]",""):m)'),
                    ('className:"z-[1200] w-80 max-w-[calc(100vw-24px)] rounded-xl border border-border/70 bg-popover p-1 shadow-xl"',
                     'className:"studio-model-menu z-[1200] w-80 max-w-[calc(100vw-24px)] rounded-xl border border-border/70 bg-popover p-1 shadow-xl"'),
                    ('textValue:`${"workflowRef"in e?e.label:j(e.model,e)} ${e.model} ${e.channelName}`',
                     'textValue:`${"workflowRef"in e?e.label:(0,g.token51VideoModelLabel)(j(e.model,e)||e.model)} ${e.model} ${e.channelName}`'),
                ]
            for needle, replacement in patches:
                if source.count(needle) != 1:
                    raise HTTPException(503, '画布版本已变化，需要更新渠道适配')
                source = source.replace(needle, replacement)
            return Response(source, media_type='application/javascript', headers={'cache-control': 'no-store'})
        app.mount('/_next/static', StaticFiles(directory=RUNTIME / '.next/static'), name='infinite-static')
        for folder in ('icons', 'director', 'onnxruntime', 'models'):
            if (RUNTIME / 'public' / folder).exists():
                app.mount('/' + folder, StaticFiles(directory=RUNTIME / 'public' / folder), name='infinite-' + folder)
