"""雪宝 Studio: original multi-user canvas + configurable image/video API starter."""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Depends, Request, Response, UploadFile, File, Body
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from PIL import Image, UnidentifiedImageError

from .db import db, setup_db, now, DATA, ROOT, hash_password, check_password, encrypt_secret
from .adapters import MEDIA, build_context, invoke_provider, check_provider_url
from .resource_usage import ResourceMonitor

WEB = ROOT / 'web'
SESSION_SECONDS = 7 * 24 * 3600
COOKIE_NAME = 'yingliu_session'
Image.MAX_IMAGE_PIXELS = 30_000_000
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
MAX_UPLOAD_IMAGE_PIXELS = 4096 * 4096
UPLOAD_CHUNK_BYTES = 256 * 1024
MAX_JOB_CONCURRENCY = 2
RESOURCE_SAMPLE_SECONDS = 5
CPU_PERCENT_TO_EXPAND = 50
CPU_PERCENT_TO_REDUCE = 80
MEMORY_BYTES_TO_EXPAND = 384 * 1024 * 1024
MEMORY_BYTES_TO_REDUCE = 256 * 1024 * 1024
SAMPLES_TO_EXPAND = 3
logger = logging.getLogger(__name__)


def one(sql, params=()):
    with db() as c:
        row = c.execute(sql, params).fetchone()
    return dict(row) if row else None


def allrows(sql, params=()):
    with db() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def whoami(request: Request):
    token = request.cookies.get(COOKIE_NAME, '')
    if not token:
        raise HTTPException(status_code=401, detail='请先登录')
    row = one('''SELECT u.id,u.username,u.role,u.points FROM sessions s
                 JOIN users u ON u.id=s.user_id
                 WHERE s.token_hash=? AND s.expires_at>?''',
              (hashlib.sha256(token.encode()).hexdigest(), now()))
    if not row:
        raise HTTPException(status_code=401, detail='登录已过期，请重新登录')
    return row


def admin_only(user=Depends(whoami)):
    if user['role'] != 'admin':
        raise HTTPException(status_code=403, detail='没有管理员权限')
    return user


def assert_owner(table, obj_id, user_id):
    item = one(f'SELECT * FROM {table} WHERE id=? AND user_id=?', (obj_id, user_id))
    if not item:
        raise HTTPException(status_code=404, detail='数据不存在或无权查看')
    return item


def public_model(m):
    m = dict(m)
    m['has_api_key'] = bool(m.pop('encrypted_key', ''))
    m.pop('auth_header', None) if m['protocol'] == 'demo' else None
    return m


def present_job(j):
    result = dict(j)
    result['options'] = json.loads(result.pop('options_json'))
    return result


def launch_job(job_id):
    state = app.state
    if job_id in state.scheduled_jobs:
        return
    state.scheduled_jobs.add(job_id)
    state.job_queue.append(job_id)
    state.jobs_changed.set()


async def run_job_worker(state):
    while True:
        while not state.job_queue or state.running_jobs >= state.job_concurrency:
            state.jobs_changed.clear()
            await state.jobs_changed.wait()
        job_id = state.job_queue.popleft()
        state.running_jobs += 1
        try:
            await execute_job(job_id)
        except Exception:
            logger.exception('Could not execute Studio job %s', job_id)
        finally:
            state.running_jobs -= 1
            state.scheduled_jobs.discard(job_id)
            state.jobs_changed.set()


async def adjust_job_concurrency(state):
    monitor = ResourceMonitor()
    healthy_samples = 0
    while True:
        usage = monitor.sample()
        cpu = usage.cpu_percent
        memory = usage.available_memory_bytes
        limit = state.job_concurrency
        if cpu is None or memory is None or cpu >= CPU_PERCENT_TO_REDUCE or memory < MEMORY_BYTES_TO_REDUCE:
            limit = 1
            healthy_samples = 0
        elif cpu <= CPU_PERCENT_TO_EXPAND and memory >= MEMORY_BYTES_TO_EXPAND:
            healthy_samples += 1
            if healthy_samples >= SAMPLES_TO_EXPAND:
                limit = MAX_JOB_CONCURRENCY
        else:
            healthy_samples = 0
        if limit != state.job_concurrency:
            state.job_concurrency = limit
            state.jobs_changed.set()
            logger.info('Studio job concurrency set to %s (CPU %s%%, available RAM %s bytes)', limit, cpu, memory)
        await asyncio.sleep(RESOURCE_SAMPLE_SECONDS)


def prepare_job_references(model, job):
    reference_path = None
    reference_url = None
    opts = json.loads(job['options_json'])
    user_id = job['user_id']
    if opts.get('reference_asset_id'):
        asset = assert_owner('assets', int(opts['reference_asset_id']), user_id)
        reference_path = MEDIA / asset['filename']
    elif opts.get('reference_job_id'):
        source = assert_owner('jobs', opts['reference_job_id'], user_id)
        if source['status'] != 'completed' or source['kind'] != 'image':
            raise ValueError('参考图片任务尚未完成')
        if source['result_url'].startswith('/api/media/'):
            reference_path = MEDIA / source['result_url'].rsplit('/', 1)[-1]
        else:
            reference_url = source['result_url']

    references = []
    for ref in opts.get('reference_inputs') or []:
        if ref.get('asset_id'):
            asset = assert_owner('assets', ref['asset_id'], user_id)
            value = build_context(model, job, MEDIA / asset['filename'])['reference_base64']
            if not value:
                raise ValueError('多人物参考目前支持图片素材')
        else:
            source = assert_owner('jobs', ref['job_id'], user_id)
            if source['status'] != 'completed' or source['kind'] != 'image':
                raise ValueError('参考图片任务尚未完成')
            value = source['result_url']
            if value.startswith('/api/media/'):
                value = build_context(model, job, MEDIA / value.rsplit('/', 1)[-1])['reference_base64']
        references.append(value)
    if references:
        job['reference_images'] = references
    return reference_path, reference_url


async def execute_job(job_id):
    job = one('SELECT * FROM jobs WHERE id=?', (job_id,))
    if not job or job['status'] not in ('queued', 'running'):
        return
    model = one('SELECT * FROM models WHERE id=?', (job['model_id'],))
    if not model:
        fail_job(job_id, '模型已被移除')
        return
    with db() as c:
        c.execute("UPDATE jobs SET status='running', updated_at=? WHERE id=?", (now(), job_id))
    try:
        reference_path = reference_url = None
        if not job['provider_task_id']:
            reference_path, reference_url = await run_in_threadpool(prepare_job_references, model, job)

        def set_provider_id(provider_id):
            with db() as c:
                c.execute('UPDATE jobs SET provider_task_id=?, updated_at=? WHERE id=?',
                          (provider_id, now(), job_id))

        url = await invoke_provider(model, job, reference_path, reference_url, set_provider_id)
        with db() as c:
            c.execute("UPDATE jobs SET status='completed',result_url=?,updated_at=? WHERE id=?",
                      (url, now(), job_id))
    except Exception as exc:
        fail_job(job_id, str(exc)[:600])


def fail_job(job_id, error):
    """Charge refunds are idempotent within one SQLite transaction."""
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        j = c.execute('SELECT user_id, charged_points, refunded, status FROM jobs WHERE id=?',
                      (job_id,)).fetchone()
        if not j or j['status'] in ('completed', 'failed'):
            c.execute('COMMIT')
            return
        if not j['refunded'] and j['charged_points']:
            c.execute('UPDATE users SET points=points+? WHERE id=?',
                      (j['charged_points'], j['user_id']))
        c.execute("UPDATE jobs SET status='failed',error=?,refunded=1,updated_at=? WHERE id=?",
                  (error, now(), job_id))
        c.execute('COMMIT')


@asynccontextmanager
async def lifespan(_app):
    state = _app.state
    state.image_upload_lock = asyncio.Lock()
    state.job_queue = deque()
    state.scheduled_jobs = set()
    state.jobs_changed = asyncio.Event()
    state.running_jobs = 0
    state.job_concurrency = 1
    setup_db()
    from .jimeng import install_models
    install_models()
    from .infinite_canvas import setup_canvas_bridge
    setup_canvas_bridge()
    for j in allrows("""SELECT id FROM jobs WHERE status IN ('queued', 'running')
                        ORDER BY status = 'queued', created_at, id"""):
        launch_job(j['id'])
    workers = [
        asyncio.create_task(run_job_worker(state))
        for _ in range(MAX_JOB_CONCURRENCY)
    ]
    resource_monitor = asyncio.create_task(adjust_job_concurrency(state))
    try:
        yield
    finally:
        # Unfinished jobs stay in SQLite so the next startup can resume them.
        resource_monitor.cancel()
        for worker in workers:
            worker.cancel()
        await asyncio.gather(resource_monitor, *workers, return_exceptions=True)


app = FastAPI(title='雪宝 Studio API', version='0.1.0', lifespan=lifespan)


@app.exception_handler(HTTPException)
async def studio_http_error(request: Request, exc: HTTPException):
    from fastapi.responses import JSONResponse
    message = str(exc.detail)
    body = {'detail': exc.detail}
    if request.url.path.startswith('/api/v1/'):
        body.update(code=exc.status_code, msg=message, message=message,
                    error={'message': message}, data=None)
    return JSONResponse(body, status_code=exc.status_code, headers=exc.headers)


@app.middleware('http')
async def same_origin_guard(request: Request, call_next):
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and request.url.path.startswith('/api/'):
        origin = request.headers.get('origin')
        if origin:
            from urllib.parse import urlparse
            o = urlparse(origin)
            expected_host = request.headers.get('x-forwarded-host') or request.headers.get('host', '')
            if o.netloc != expected_host:
                return Response('跨站请求被拒绝', status_code=403)
    response = await call_next(request)
    if request.url.path == '/' or request.url.path in ('/static/app.js', '/static/editor.css'):
        response.headers['Cache-Control'] = 'no-store'
    return response


class AuthRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=8, max_length=128)

    @field_validator('username')
    @classmethod
    def valid_name(cls, value):
        if not re.fullmatch(r'[A-Za-z0-9_\-]+', value):
            raise ValueError('用户名仅支持英文、数字、下划线和短横线')
        return value


def set_login_cookie(response, uid):
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=SESSION_SECONDS)).isoformat()
    with db() as c:
        c.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)',
                  (hashlib.sha256(token.encode()).hexdigest(), uid, expires_at))
    response.set_cookie(COOKIE_NAME, token, httponly=True,
                        secure=os.getenv('COOKIE_SECURE') == '1', samesite='lax',
                        max_age=SESSION_SECONDS, path='/')


@app.post('/api/auth/login')
def login(data: AuthRequest, response: Response):
    u = one('SELECT * FROM users WHERE username=?', (data.username,))
    if not u or not check_password(data.password, u['password_hash']):
        raise HTTPException(401, '用户名或密码错误')
    set_login_cookie(response, u['id'])
    return {'ok': True}


class RegistrationRequest(AuthRequest):
    invitation_code: str = Field(default='', max_length=80)


@app.post('/api/auth/register')
def register(data: RegistrationRequest, response: Response):
    if os.getenv('ALLOW_SIGNUP', '1') != '1':
        raise HTTPException(403, '管理员已关闭注册')
    code = data.invitation_code.strip().upper()
    if not code:
        raise HTTPException(400, '请填写邀请码')
    password_hash = hash_password(data.password)
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        invitation = c.execute('SELECT id FROM invitations WHERE code=? AND used_at IS NULL AND revoked_at IS NULL', (code,)).fetchone()
        if not invitation:
            raise HTTPException(400, '邀请码无效、已使用或已作废')
        try:
            c.execute('INSERT INTO users(username,password_hash,role,points,created_at) VALUES(?,?,?,?,?)',
                      (data.username, password_hash, 'user',
                       int(os.getenv('NEW_USER_POINTS', '100')), now()))
            uid = c.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
        except sqlite3.IntegrityError:
            raise HTTPException(409, '用户名已经被使用')
        c.execute('UPDATE invitations SET used_by=?,used_username=?,used_at=? WHERE id=?', (uid, data.username, now(), invitation['id']))
        c.execute('COMMIT')
    set_login_cookie(response, uid)
    return {'ok': True}


class InvitationBatch(BaseModel):
    count: int = Field(default=1, ge=1, le=50)


@app.get('/api/admin/invitations')
def list_invitations(user=Depends(admin_only)):
    return allrows('SELECT id,code,created_at,used_username,used_at,revoked_at FROM invitations ORDER BY id DESC')


@app.post('/api/admin/invitations')
def create_invitations(data: InvitationBatch, user=Depends(admin_only)):
    created = []
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        for _ in range(data.count):
            code = secrets.token_hex(12).upper()
            c.execute('INSERT INTO invitations(code,created_by,created_at) VALUES(?,?,?)', (code, user['id'], now()))
            created.append(code)
        c.execute('COMMIT')
    return {'codes': created}


@app.delete('/api/admin/invitations/{invitation_id}')
def revoke_invitation(invitation_id: int, user=Depends(admin_only)):
    with db() as c:
        result = c.execute('UPDATE invitations SET revoked_at=? WHERE id=? AND used_at IS NULL AND revoked_at IS NULL', (now(), invitation_id))
        if not result.rowcount:
            raise HTTPException(409, '邀请码不存在、已使用或已作废')
    return {'ok': True}


@app.post('/api/auth/logout')
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE_NAME, '')
    if token:
        with db() as c:
            c.execute('DELETE FROM sessions WHERE token_hash=?',
                      (hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie(COOKIE_NAME, path='/')
    return {'ok': True}


@app.get('/api/me')
def me(user=Depends(whoami)):
    return user


@app.get('/api/models')
def models(kind: Literal['image', 'video'] | None = None, user=Depends(whoami)):
    if kind:
        rows = allrows('SELECT * FROM models WHERE enabled=1 AND kind=? ORDER BY id', (kind,))
    else:
        rows = allrows('SELECT * FROM models WHERE enabled=1 ORDER BY id')
    result = []
    from .jimeng import is_jimeng, jimeng_capabilities
    for m in rows:
        item = {'id': m['id'], 'name': m['name'], 'kind': m['kind'], 'cost': m['cost'],
                'model_code': m['model_code'], 'is_demo': m['protocol'] == 'demo'}
        if m['kind'] == 'video':
            capability = jimeng_capabilities(m) if is_jimeng(m) else None
            item.update(capability or {'duration_min': 1, 'duration_max': 30,
                                       'duration_options': [5, 10, 15, 30]})
        result.append(item)
    return result


class ProjectIn(BaseModel):
    title: str = Field(default='未命名画布', min_length=1, max_length=80)
    nodes: list[dict] = Field(default_factory=list)
    edges: list[dict] = Field(default_factory=list)


def check_graph(data):
    if len(data.nodes) > 300 or len(data.edges) > 600:
        raise HTTPException(400, '画布节点或连接数超出限制')
    graph = json.dumps({'nodes': data.nodes, 'edges': data.edges}, ensure_ascii=False)
    if len(graph.encode()) > 1024 * 1024:
        raise HTTPException(400, '画布数据超出 1MB')
    return graph


@app.get('/api/projects')
def projects(user=Depends(whoami)):
    return allrows('SELECT id,title,updated_at FROM projects WHERE user_id=? ORDER BY updated_at DESC',
                   (user['id'],))


@app.post('/api/projects')
def new_project(data: ProjectIn, user=Depends(whoami)):
    graph = check_graph(data)
    with db() as c:
        c.execute('INSERT INTO projects(user_id,title,graph_json,updated_at) VALUES(?,?,?,?)',
                  (user['id'], data.title, graph, now()))
        pid = c.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
    return {'id': pid}


@app.get('/api/projects/{project_id}')
def get_project(project_id: int, user=Depends(whoami)):
    p = assert_owner('projects', project_id, user['id'])
    return {'id': p['id'], 'title': p['title'], **json.loads(p['graph_json'])}


@app.put('/api/projects/{project_id}')
def save_project(project_id: int, data: ProjectIn, user=Depends(whoami)):
    assert_owner('projects', project_id, user['id'])
    graph = check_graph(data)
    with db() as c:
        c.execute('UPDATE projects SET title=?,graph_json=?,updated_at=? WHERE id=?',
                  (data.title, graph, now(), project_id))
    return {'ok': True}


@app.delete('/api/projects/{project_id}')
def delete_project(project_id: int, user=Depends(whoami)):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        project = c.execute('SELECT id FROM projects WHERE id=? AND user_id=?', (project_id, user['id'])).fetchone()
        if not project:
            raise HTTPException(404, '画布不存在或无权删除')
        c.execute('UPDATE jobs SET project_id=NULL WHERE project_id=?', (project_id,))
        c.execute('DELETE FROM projects WHERE id=?', (project_id,))
        c.execute('COMMIT')
    return {'ok': True}


def prepare_upload_image(source):
    formats = ('JPEG', 'PNG', 'WEBP')
    size_error = f'图片像素不能超过 {MAX_UPLOAD_IMAGE_PIXELS:,}'
    try:
        with Image.open(source, formats=formats) as probe:
            if probe.width * probe.height > MAX_UPLOAD_IMAGE_PIXELS:
                raise HTTPException(413, size_error)
            probe.verify()
        source.seek(0)
        rgb = Image.open(source, formats=formats)
        if rgb.mode != 'RGB':
            original = rgb
            try:
                rgb = original.convert('RGB')
            finally:
                original.close()
        try:
            rgb.thumbnail((2560, 2560))
            rgb.load()
            return rgb
        except Exception:
            rgb.close()
            raise
    except Image.DecompressionBombError as exc:
        raise HTTPException(413, size_error) from exc
    except (UnidentifiedImageError, ValueError, SyntaxError, OSError) as exc:
        raise HTTPException(400, '请上传有效的 JPEG/PNG/WebP 图片') from exc


def validate_upload_media(source, suffix):
    header = source.read(13)
    source.seek(0)
    if suffix == '.wav':
        valid = header.startswith(b'RIFF') and header[8:12] == b'WAVE'
    elif suffix == '.mp3':
        valid = header.startswith(b'ID3') or (len(header) > 2 and header[0] == 255 and header[1] & 224 == 224)
    elif suffix == '.mp4':
        valid = len(header) == 13 and header[4:8] == b'ftyp'
    else:
        valid = header.startswith(b'\x1aE\xdf\xa3')
    if not valid:
        kind = '音频' if suffix in ('.mp3', '.wav') else '视频'
        raise HTTPException(400, f'{kind}文件格式无效')


def save_upload(source, user_id, suffix, media_type):
    source.seek(0, os.SEEK_END)
    if source.tell() > MAX_UPLOAD_BYTES:
        raise HTTPException(413, '素材不能超过 30MB')
    source.seek(0)

    filename = uuid.uuid4().hex + suffix
    destination = MEDIA / filename
    temporary = destination.with_suffix('.part')
    try:
        if suffix == '.jpg':
            with prepare_upload_image(source) as image:
                image.save(temporary, format='JPEG', quality=88)
        else:
            validate_upload_media(source, suffix)
            with temporary.open('wb') as output:
                shutil.copyfileobj(source, output, length=UPLOAD_CHUNK_BYTES)
        temporary.replace(destination)
        try:
            with db() as c:
                aid = c.execute('INSERT INTO assets(user_id,filename,mime,created_at) VALUES(?,?,?,?)',
                                (user_id, filename, media_type, now())).lastrowid
        except Exception:
            destination.unlink(missing_ok=True)
            raise
    finally:
        temporary.unlink(missing_ok=True)
    result = {'id': aid, 'url': f'/api/media/{filename}'}
    if suffix != '.jpg':
        result['mime'] = media_type
    return result


@app.post('/api/uploads')
async def upload_image(file: UploadFile = File(...), user=Depends(whoami)):
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(413, '素材不能超过 30MB')
    media_type = (file.content_type or '').lower()
    suffix = Path(file.filename or '').suffix.lower()
    if suffix in ('.mp3', '.wav'):
        media_type = 'audio/mpeg' if suffix == '.mp3' else 'audio/wav'
    elif not (media_type in ('video/mp4', 'video/webm') and suffix in ('.mp4', '.webm')):
        async with app.state.image_upload_lock:
            return await run_in_threadpool(save_upload, file.file, user['id'], '.jpg', 'image/jpeg')
    return await run_in_threadpool(save_upload, file.file, user['id'], suffix, media_type)


@app.get('/api/media/{filename}')
def serve_media(filename: str, user=Depends(whoami)):
    if not re.fullmatch(r'[a-f0-9-]{16,64}\.(png|jpg|mp4|webm|mp3|wav)', filename):
        raise HTTPException(404, '文件不存在')
    asset = one('SELECT id FROM assets WHERE filename=? AND user_id=?', (filename, user['id']))
    job = one('SELECT id FROM jobs WHERE result_url=? AND user_id=?',
              ('/api/media/' + filename, user['id']))
    if not asset and not job:
        raise HTTPException(404, '文件不存在')
    p = MEDIA / filename
    if not p.exists():
        raise HTTPException(404, '媒体文件已丢失')
    return FileResponse(p, media_type={'png': 'image/png', 'jpg': 'image/jpeg', 'mp4': 'video/mp4', 'webm': 'video/webm', 'mp3':'audio/mpeg', 'wav':'audio/wav'}[p.suffix[1:]])


class ReferenceInput(BaseModel):
    asset_id: int | None = None
    job_id: str | None = None


class CreateJob(BaseModel):
    project_id: int | None = None
    canvas_node_id: str | None = Field(default=None, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')
    model_id: int
    prompt: str = Field(min_length=1, max_length=8000)
    ratio: Literal['9:16', '16:9', '1:1', '4:3'] = '9:16'
    duration: float = Field(default=5, gt=0, le=30)
    resolution: Literal['auto', '1K', '2K', '4K', '480p', '720p', '1080p'] = '720p'
    image_count: int = Field(default=1, ge=1, le=4)
    reference_asset_id: int | None = None
    reference_job_id: str | None = None
    reference_inputs: list[ReferenceInput] = Field(default_factory=list, max_length=9)


@app.post('/api/jobs')
async def create_job(data: CreateJob, user=Depends(whoami)):
    model = one('SELECT * FROM models WHERE id=? AND enabled=1', (data.model_id,))
    if not model:
        raise HTTPException(404, '模型不存在或已停用')
    from .jimeng import is_jimeng, validate_jimeng_duration, validate_jimeng_resolution
    if is_jimeng(model):
        try:
            validate_jimeng_duration(model, data.duration)
            validate_jimeng_resolution(model, data.resolution)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if data.project_id is not None:
        assert_owner('projects', data.project_id, user['id'])
    if data.canvas_node_id and data.project_id is None:
        raise HTTPException(400, '画布视频记录需要关联项目')
    if data.reference_asset_id is not None:
        asset = assert_owner('assets', data.reference_asset_id, user['id'])
        if model['kind'] == 'image' and not asset['mime'].startswith('image/'):
            raise HTTPException(400, '图片生成仅支持图片参考素材')
    if data.reference_job_id is not None:
        source = assert_owner('jobs', data.reference_job_id, user['id'])
        if source['status'] != 'completed' or source['kind'] != 'image':
            raise HTTPException(400, '只能使用已完成的图片任务作为参考')
    if data.reference_inputs:
        from .jimeng import is_jimeng
        if not is_jimeng(model):
            raise HTTPException(400, '此模型尚未接入多图片参考')
        for ref in data.reference_inputs:
            if bool(ref.asset_id) == bool(ref.job_id):
                raise HTTPException(400, '参考素材标识无效')
            if ref.asset_id:
                asset = assert_owner('assets', ref.asset_id, user['id'])
                if not asset['mime'].startswith('image/'):
                    raise HTTPException(400, '多人物参考请使用图片素材')
            else:
                source = assert_owner('jobs', ref.job_id, user['id'])
                if source['kind'] != 'image' or source['status'] != 'completed':
                    raise HTTPException(400, '请选择已完成的图片素材')
    job_id = uuid.uuid4().hex
    opts = data.model_dump(include={'ratio', 'duration', 'resolution', 'image_count', 'reference_asset_id', 'reference_job_id', 'reference_inputs', 'canvas_node_id'})
    cost = model['cost']
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        changed = c.execute('UPDATE users SET points=points-? WHERE id=? AND points>=?',
                            (cost, user['id'], cost)).rowcount
        if changed == 0:
            c.execute('ROLLBACK')
            raise HTTPException(402, '积分不足，请联系管理员充值')
        c.execute('''INSERT INTO jobs
          (id,user_id,project_id,model_id,kind,prompt,options_json,charged_points,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?)''',
          (job_id, user['id'], data.project_id, model['id'], model['kind'],
           data.prompt, json.dumps(opts), cost, now(), now()))
        c.execute('COMMIT')
    launch_job(job_id)
    return {'id': job_id, 'status': 'queued', 'cost': cost}


@app.get('/api/jobs')
def jobs(user=Depends(whoami)):
    return [present_job(j) for j in allrows(
        '''SELECT j.*, m.name AS model_name FROM jobs j JOIN models m ON m.id=j.model_id
           WHERE j.user_id=? AND j.deleted=0 ORDER BY j.created_at DESC LIMIT 100''', (user['id'],))]


@app.delete('/api/jobs/{job_id}')
def delete_job(job_id: str, user=Depends(whoami)):
    job = assert_owner('jobs', job_id, user['id'])
    if job['status'] not in ('completed', 'failed'):
        raise HTTPException(409, '请等待任务完成后删除')
    # Preserve media and references used by existing canvas nodes.
    with db() as c:
        c.execute('UPDATE jobs SET deleted=1 WHERE id=? AND user_id=?', (job_id, user['id']))
    return {'ok': True}


@app.get('/api/jobs/{job_id}')
def get_job(job_id: str, user=Depends(whoami)):
    j = assert_owner('jobs', job_id, user['id'])
    model = one('SELECT name FROM models WHERE id=?', (j['model_id'],))
    j['model_name'] = model['name'] if model else '未知模型'
    return present_job(j)


class ModelIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    model_code: str = Field(min_length=1, max_length=160)
    kind: Literal['image', 'video']
    protocol: Literal['demo', 'sync_json', 'async_json']
    api_url: str = ''
    poll_url: str = ''
    auth_mode: Literal['bearer', 'header', 'none'] = 'bearer'
    auth_header: str = 'X-API-Key'
    api_key: str = ''
    request_template: str = '{"model":"{{model}}","prompt":"{{prompt}}"}'
    result_path: str = 'data.0.url'
    task_id_path: str = 'id'
    status_path: str = 'status'
    completed_status: str = 'completed,succeeded,success'
    failed_status: str = 'failed,error'
    cost: int = Field(default=10, ge=0, le=10000000)
    enabled: bool = True

    @field_validator('request_template')
    @classmethod
    def valid_json(cls, value):
        result = json.loads(value)
        if not isinstance(result, dict):
            raise ValueError('请求模板必须是 JSON 对象')
        return value


def validate_model(data):
    from .jimeng import is_jimeng
    if is_jimeng(data.model_dump()):
        if data.kind != 'video':
            raise HTTPException(400, '即梦号池目前只接入视频生成')
        return
    if data.protocol != 'demo':
        if not data.api_url:
            raise HTTPException(400, '必须填写提交任务 API 地址')
        try:
            check_provider_url(data.api_url)
            if data.protocol == 'async_json':
                if '{{task_id}}' not in data.poll_url:
                    raise ValueError('轮询 URL 必须包含 {{task_id}}')
                check_provider_url(data.poll_url.replace('{{task_id}}', 'test'))
        except ValueError as exc:
            raise HTTPException(400, str(exc))


@app.get('/api/admin/overview')
def admin_overview(user=Depends(admin_only)):
    return {
        'users': one('SELECT COUNT(*) AS count FROM users')['count'],
        'jobs': one('SELECT COUNT(*) AS count FROM jobs')['count'],
        'models': one('SELECT COUNT(*) AS count FROM models WHERE deleted=0')['count'],
        'completed': one("SELECT COUNT(*) AS count FROM jobs WHERE status='completed'")['count'],
    }


@app.get('/api/admin/models')
def admin_models(user=Depends(admin_only)):
    return [public_model(m) for m in allrows('SELECT * FROM models WHERE deleted=0 ORDER BY id DESC')]


def model_payload(data, encrypted_key):
    return (data.name, data.model_code, data.kind, data.protocol, data.api_url,
            data.poll_url, data.auth_mode, data.auth_header, encrypted_key,
            data.request_template, data.result_path, data.task_id_path,
            data.status_path, data.completed_status, data.failed_status, data.cost,
            int(data.enabled))


@app.post('/api/admin/models')
def add_model(data: ModelIn, user=Depends(admin_only)):
    validate_model(data)
    with db() as c:
        c.execute('''INSERT INTO models
          (name,model_code,kind,protocol,api_url,poll_url,auth_mode,auth_header,encrypted_key,
           request_template,result_path,task_id_path,status_path,completed_status,failed_status,
           cost,enabled,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (*model_payload(data, encrypt_secret(data.api_key)), now()))
        mid = c.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
    return {'id': mid}


@app.put('/api/admin/models/{model_id}')
def edit_model(model_id: int, data: ModelIn, user=Depends(admin_only)):
    old = one('SELECT * FROM models WHERE id=?', (model_id,))
    if not old:
        raise HTTPException(404, '模型不存在')
    validate_model(data)
    key = encrypt_secret(data.api_key) if data.api_key else old['encrypted_key']
    with db() as c:
        c.execute('''UPDATE models SET
          name=?,model_code=?,kind=?,protocol=?,api_url=?,poll_url=?,auth_mode=?,auth_header=?,
          encrypted_key=?,request_template=?,result_path=?,task_id_path=?,status_path=?,
          completed_status=?,failed_status=?,cost=?,enabled=? WHERE id=?''',
          (*model_payload(data, key), model_id))
    return {'ok': True}


class ModelEnabledIn(BaseModel):
    enabled: bool = Field(strict=True)


@app.patch('/api/admin/models/{model_id}/enabled')
def set_model_enabled(model_id: int, data: ModelEnabledIn, user=Depends(admin_only)):
    with db() as c:
        changed = c.execute('UPDATE models SET enabled=? WHERE id=? AND deleted=0',
                            (int(data.enabled), model_id)).rowcount
    if not changed:
        raise HTTPException(404, '模型不存在或已删除')
    return {'id': model_id, 'enabled': data.enabled}


@app.delete('/api/admin/models/{model_id}')
def delete_model(model_id: int, user=Depends(admin_only)):
    model = one('SELECT id FROM models WHERE id=?', (model_id,))
    if not model:
        raise HTTPException(404, '模型不存在')
    with db() as c:
        c.execute('UPDATE models SET deleted=1, enabled=0 WHERE id=?', (model_id,))
    return {'ok': True}


@app.get('/api/admin/users')
def admin_users(user=Depends(admin_only)):
    return allrows('SELECT id,username,role,points,created_at FROM users ORDER BY id DESC LIMIT 200')


class AdminUserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=8, max_length=128)
    points: int = Field(default=100, ge=0, le=10000000)


class AdminPasswordChange(BaseModel):
    password: str = Field(min_length=8, max_length=128)


@app.post('/api/admin/users')
def create_admin_user(data: AdminUserCreate, user=Depends(admin_only)):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', data.username):
        raise HTTPException(400, '用户名仅支持英文、数字、下划线和短横线')
    with db() as c:
        try:
            c.execute('INSERT INTO users(username,password_hash,role,points,created_at) VALUES(?,?,?,?,?)',
                      (data.username, hash_password(data.password), 'user', data.points, now()))
            uid = c.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
        except sqlite3.IntegrityError:
            raise HTTPException(409, '用户名已经被使用')
    return {'id': uid}


@app.put('/api/admin/users/{user_id}/password')
def change_user_password(user_id: int, data: AdminPasswordChange, user=Depends(admin_only)):
    with db() as c:
        updated = c.execute('UPDATE users SET password_hash=? WHERE id=?',
                            (hash_password(data.password), user_id)).rowcount
    if not updated:
        raise HTTPException(400, '用户不存在')
    return {'ok': True}


@app.delete('/api/admin/users/{user_id}')
def delete_admin_user(user_id: int, user=Depends(admin_only)):
    if user_id == user['id']:
        raise HTTPException(400, '不能删除当前登录的管理员账户')
    target = one('SELECT id,role FROM users WHERE id=?', (user_id,))
    if not target or target['role'] == 'admin':
        raise HTTPException(400, '管理员账户不能通过此处删除')
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
        c.execute('DELETE FROM assets WHERE user_id=?', (user_id,))
        c.execute('DELETE FROM jobs WHERE user_id=?', (user_id,))
        c.execute('DELETE FROM projects WHERE user_id=?', (user_id,))
        c.execute('DELETE FROM users WHERE id=?', (user_id,))
        c.execute('COMMIT')
    return {'ok': True}


class CreditChange(BaseModel):
    amount: int = Field(ge=-1000000, le=1000000)


@app.post('/api/admin/users/{user_id}/credits')
def change_credit(user_id: int, data: CreditChange, user=Depends(admin_only)):
    with db() as c:
        updated = c.execute('UPDATE users SET points=points+? WHERE id=? AND points+?>=0',
                            (data.amount, user_id, data.amount)).rowcount
    if not updated:
        raise HTTPException(400, '用户不存在或扣减后积分不足')
    return {'ok': True}


@app.get('/api/admin/jobs')
def admin_jobs(user=Depends(admin_only)):
    return allrows('''SELECT j.id,u.username,m.name AS model_name,j.kind,j.status,j.charged_points,
                             j.error,j.created_at
                       FROM jobs j JOIN users u ON u.id=j.user_id JOIN models m ON m.id=j.model_id
                       ORDER BY j.created_at DESC LIMIT 100''')


@app.get('/')
def index():
    return FileResponse(WEB / 'index.html')


app.mount('/static', StaticFiles(directory=str(WEB)), name='static')

from .infinite_canvas import register as register_infinite_canvas
register_infinite_canvas(app, globals())

from .jimeng import register as register_jimeng
register_jimeng(app, admin_only)
