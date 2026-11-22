"""Account-bound, administrator-operated confirmation sessions."""
import base64
import threading
import uuid
from urllib.parse import urlsplit

from fastapi import HTTPException
from . import db, repo, remote_browser
from .session_crypto import seal, reveal

_sessions = {}
_lock = threading.RLock()
CONTENT_CONSENT_VERSION = 'seedance-content-use-20261121-v1'
CONTENT_CONSENT_TEXT = ('请您使用本人照片/视频或取得其充分授权，确保不存在违反法律法规、'
                      '侵犯他人权益或违反公序良俗的内容。若因您不当使用本功能导致任何第三方权益受损，'
                      '您将依法承担相应法律责任。')


def read_content_consent(client):
    data = client._post('/mweb/v1/get_settings', {}).get('data') or {}
    custom = data.get('user_custom_settings')
    if isinstance(custom, str):
        custom = db.loads(custom, {})
    value = custom.get('aigc_compliance_confirmed') if isinstance(custom, dict) else None
    return value if isinstance(value, bool) else None


def trusted_url(value):
    if not isinstance(value, str) or len(value) > 8192:
        return ''
    try:
        p = urlsplit(value)
        return value if (p.scheme == 'https' and p.hostname == 'jimeng.jianying.com'
                         and p.port in (None, 443) and not p.username and not p.password) else ''
    except (ValueError, TypeError):
        return ''


def pending(account_id):
    return db.get_setting(f'confirmation:{account_id}', {})


def mark(account_id, error):
    with _lock:
        db.put_setting(f'confirmation:{account_id}', {
            'id': uuid.uuid4().hex, 'created_at': db.now(), 'pending': True,
            'url': seal(trusted_url(getattr(error, 'verification_url', ''))),
            'ret': getattr(error, 'ret', None),
            'task_id': getattr(error, 'task_id', None),
            'evidence': getattr(error, 'confirmation_evidence', {}),
        })
        repo.update_account(account_id, status='confirmation', last_error=str(error)[:300])


def public(account_id):
    record = pending(account_id)
    if not record:
        return {}
    result = {k: record[k] for k in ('id', 'created_at', 'pending') if k in record}
    result['has_challenge_url'] = bool(trusted_url(reveal(record.get('url') or '')))
    result['action'] = 'verify' if result['has_challenge_url'] else 'diagnose'
    result['reason'] = 'content_use_confirmation'
    return result


def details(account_id):
    if not repo.get_account(account_id):
        raise HTTPException(404, '账号不存在')
    record = pending(account_id)
    result = public(account_id)
    result.update(account_id=account_id, ret=record.get('ret'),
                  evidence=record.get('evidence') or {}, diagnostics=record.get('diagnostics'))
    result['consent'] = {'version': CONTENT_CONSENT_VERSION, 'text': CONTENT_CONSENT_TEXT,
                         'confirmed': (record.get('diagnostics') or {}).get('content_consent_confirmed')}
    # Only operational metadata leaves the server, never prompts or reference assets.
    latest = db.one("SELECT task_id, ret, ts FROM upstream_logs WHERE account_id = ? "
                    "AND ret = '4010' ORDER BY id DESC LIMIT 1", (account_id,))
    task_id = record.get('task_id') or (latest['task_id'] if latest else None)
    if latest and result['ret'] is None:
        result['ret'] = latest['ret']
    task = repo.get_task(task_id) if task_id else None
    if task:
        video = (task.get('extras') or {}).get('video') or {}
        result['request'] = {'task_id': task_id, 'model': task['model'],
            'duration_s': video.get('duration_s'), 'reference_mode': video.get('reference_mode'),
            'reference_count': len(task.get('image_uris') or []), 'created_at': task['created_at']}
    return result


def diagnose(account_id):
    from .pool import pool
    from .upstream.errors import JimengError
    current = details(account_id)
    if not current.get('id'):
        raise HTTPException(409, '该账号没有生成异常记录')
    cached = current.get('diagnostics') or {}
    if cached.get('checked_at', 0) > db.now() - 30:
        return current
    report = {'checked_at': db.now(), 'generation_tested': False}
    try:
        probe = pool.probe(account_id)
        report.update(login='valid', credits=probe.get('credits'))
    except JimengError as error:
        report.update(login='invalid' if error.kind == 'auth' else 'unknown',
                      error_kind=error.kind, ret=error.ret)
    if report.get('login') == 'valid':
        try:
            report['content_consent_confirmed'] = read_content_consent(pool.runtime(account_id).client)
        except JimengError as error:
            report['settings_error_kind'] = error.kind
        from .upstream import video
        model = video.get((current.get('request') or {}).get('model', ''))
        if model:
            try:
                config = pool.runtime(account_id).client._post('/mweb/v1/video_generate/get_common_config', {}).get('data') or {}
                config_model = next((m for m in config.get('model_list', [])
                                     if m.get('model_req_key') == model.model_req_key), {})
                extra = config_model.get('extra') or {}
                if isinstance(extra, str):
                    extra = db.loads(extra, {})
                report['model_content_confirmation_required'] = extra.get('aigc_compliance_confirmation_required')
            except JimengError as error:
                report['model_config_error_kind'] = error.kind
    with _lock:
        record = pending(account_id)
        if record.get('id') != current.get('id') or record.get('pending') != current.get('pending'):
            raise HTTPException(409, '账号状态已变化，请刷新诊断')
        record['diagnostics'] = report
        db.put_setting(f'confirmation:{account_id}', record)
    return details(account_id)


def confirm_content_use(account_id, event_id, consent_version, confirmed):
    from .pool import pool
    if confirmed is not True or consent_version != CONTENT_CONSENT_VERSION:
        raise HTTPException(400, '请阅读并勾选当前版本的素材使用确认内容')
    with _lock:
        record = pending(account_id)
        if not repo.get_account(account_id):
            raise HTTPException(404, '账号不存在')
        if record.get('id') != event_id:
            raise HTTPException(409, '账号异常记录已变化，请刷新后确认')
        if not record.get('pending') and record.get('consent_version') != consent_version:
            raise HTTPException(409, '当前异常已处理，请刷新诊断')
    client = pool.runtime(account_id).client
    upstream_state = read_content_consent(client)
    if upstream_state is None:
        raise HTTPException(502, '即梦未返回明确的素材使用确认状态，请稍后重新检测')
    if upstream_state is False:
        client._post('/mweb/v1/update_settings', {'custom_settings': {'aigc_compliance_confirmed': True}})
    if read_content_consent(client) is not True:
        raise HTTPException(502, '即梦尚未返回确认成功的状态，账号仍保持暂停，请稍后重新检测')
    with _lock:
        record = pending(account_id)
        if record.get('id') != event_id or not repo.get_account(account_id):
            raise HTTPException(409, '确认期间账号状态已变化，请刷新后检测')
        record.update(pending=False, consent_version=consent_version, confirmed_on_upstream_at=db.now())
        report = record.get('diagnostics') or {}
        report.update(content_consent_confirmed=True, generation_tested=False, checked_at=db.now())
        record['diagnostics'] = report
        db.put_setting(f'confirmation:{account_id}', record)
        repo.update_account(account_id, status='unknown', last_error='')
    pool.drop(account_id)
    return {'ok': True, 'upstream_confirmed': True, 'generation_submitted': False,
            'message': '即梦已确认素材使用声明，账号已恢复接单。请回到画布手动重试生成。'}


def import_previous_errors():
    """Carry forward the latest recorded failure, never infer from browser login."""
    from .upstream.errors import JimengError
    with _lock:
        for row in db.query('SELECT id, last_error FROM accounts'):
            if pending(row['id']):
                continue
            latest = db.one("SELECT ret, error FROM upstream_logs WHERE account_id = ? "
                            "AND path LIKE '%/generate' ORDER BY id DESC LIMIT 1", (row['id'],))
            message = row['last_error'] or ''
            if latest:
                message = latest['error'] if str(latest['ret']) == '4010' else ''
            if '安全确认' in message:
                mark(row['id'], JimengError(message, kind='confirmation'))


def session(account_id, session_id):
    entry = _sessions.get(account_id)
    if not entry or entry.id != session_id:
        raise HTTPException(404, '验证会话已结束，请重新打开')
    return entry


def start(account_id, purpose='challenge'):
    with _lock:
        if not repo.get_account(account_id):
            raise HTTPException(404, '账号不存在')
        record = pending(account_id)
        if not record.get('pending'):
            raise HTTPException(409, '该账号当前没有待处理确认')
        url = trusted_url(reveal(record.get('url') or ''))
        if purpose not in ('challenge', 'account_page'):
            raise HTTPException(400, '不支持的页面类型')
        if purpose == 'challenge' and not url:
            raise HTTPException(409, '未获得可用验证地址，请查看生成诊断。官网首页不是验证页面。')
        old = _sessions.get(account_id)
        if not old and sum(s.state in ('starting', 'live') for s in _sessions.values()) >= 3:
            raise HTTPException(409, '请先关闭其他账号验证窗口')
        if old:
            old.close()
        entry = remote_browser.RemoteSession(verification_account_id=account_id,
            target_url=url if purpose == 'challenge' else remote_browser.HOME)
        entry.attached = 1
        _sessions[account_id] = entry
        entry.start()
        return {'session_id': entry.id, 'event_id': record['id'],
                'has_challenge_url': bool(url) and purpose == 'challenge',
                'purpose': purpose, 'account_id': account_id}


def snapshot(account_id, session_id):
    with _lock:
        entry = session(account_id, session_id)
        frame = entry.pop_frame()
        return {'state': entry.state, 'error': entry.error, 'meta': entry.meta,
                'frame': base64.b64encode(frame).decode() if frame else None}


def close(account_id, session_id):
    with _lock:
        entry = session(account_id, session_id)
        entry.close()
        del _sessions[account_id]


def resume(account_id, event_id):
    from .pool import pool
    with _lock:
        record = pending(account_id)
        if not record.get('pending') or record.get('id') != event_id:
            raise HTTPException(409, '账号确认状态已变化，请刷新后处理')
        if not repo.get_account(account_id):
            raise HTTPException(404, '账号不存在')
    if read_content_consent(pool.runtime(account_id).client) is not True:
        raise HTTPException(409, '即梦尚未确认素材使用声明，请先阅读并提交确认内容')
    # Only upstream confirmation permits dispatch; balance probes are insufficient.
    with _lock:
        record = pending(account_id)
        if not record.get('pending') or record.get('id') != event_id:
            raise HTTPException(409, '账号确认状态已变化，请刷新后处理')
        if not repo.get_account(account_id):
            raise HTTPException(404, '账号不存在')
        record.update(pending=False, acknowledged_at=db.now(), url='')
        db.put_setting(f'confirmation:{account_id}', record)
        repo.update_account(account_id, status='unknown', last_error='')
        return {'ok': True, 'message': '已允许手动重试；未自动提交任务，未标记验证通过。若问题未解决，生成可能再次失败。'}


def cleanup(account_id):
    with _lock:
        entry = _sessions.pop(account_id, None)
        if entry:
            entry.close()
        db.execute('DELETE FROM settings WHERE key = ?', (f'confirmation:{account_id}',))
