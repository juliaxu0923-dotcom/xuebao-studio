"""SQLite persistence and password/key helpers for the single-server starter."""
import os
import sqlite3
import hashlib
import secrets
import base64
import hmac
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.getenv('APP_DATA_DIR', str(ROOT / 'data')))
DATA.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA / 'studio.sqlite3'


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA busy_timeout=15000')
    con.execute('PRAGMA foreign_keys=ON')
    try:
        yield con
    finally:
        con.close()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 310000)
    return f'{base64.b64encode(salt).decode()}:{base64.b64encode(key).decode()}'


def check_password(password: str, stored: str) -> bool:
    try:
        salt64, key64 = stored.split(':', 1)
        salt, expected = base64.b64decode(salt64), base64.b64decode(key64)
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 310000)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def cipher():
    key_file = DATA / '.encryption-key'
    if not key_file.exists():
        with os.fdopen(os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as f:
            f.write(Fernet.generate_key())
    return Fernet(key_file.read_bytes())


def encrypt_secret(secret: str) -> str:
    return cipher().encrypt(secret.encode()).decode() if secret else ''


def decrypt_secret(secret: str) -> str:
    return cipher().decrypt(secret.encode()).decode() if secret else ''


def setup_db():
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user',
          points INTEGER NOT NULL DEFAULT 0 CHECK(points >= 0), created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS invitations (
          id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE,
          created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
          created_at TEXT NOT NULL, used_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
          used_username TEXT NOT NULL DEFAULT '', used_at TEXT, revoked_at TEXT
        );
        CREATE TABLE IF NOT EXISTS models (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, model_code TEXT NOT NULL,
          kind TEXT NOT NULL CHECK(kind IN ('image','video')),
          protocol TEXT NOT NULL CHECK(protocol IN ('demo','sync_json','async_json')),
          api_url TEXT NOT NULL DEFAULT '', poll_url TEXT NOT NULL DEFAULT '',
          auth_mode TEXT NOT NULL DEFAULT 'bearer', auth_header TEXT NOT NULL DEFAULT 'X-API-Key',
          encrypted_key TEXT NOT NULL DEFAULT '', request_template TEXT NOT NULL DEFAULT '{}',
          result_path TEXT NOT NULL DEFAULT 'data.0.url', task_id_path TEXT NOT NULL DEFAULT 'id',
          status_path TEXT NOT NULL DEFAULT 'status', completed_status TEXT NOT NULL DEFAULT 'completed',
          failed_status TEXT NOT NULL DEFAULT 'failed', cost INTEGER NOT NULL DEFAULT 0,
          enabled INTEGER NOT NULL DEFAULT 1, deleted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projects (
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          title TEXT NOT NULL, graph_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS assets (
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          filename TEXT NOT NULL UNIQUE, mime TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          project_id INTEGER REFERENCES projects(id), model_id INTEGER NOT NULL REFERENCES models(id),
          kind TEXT NOT NULL, prompt TEXT NOT NULL, options_json TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'queued', provider_task_id TEXT NOT NULL DEFAULT '',
          result_url TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
          charged_points INTEGER NOT NULL DEFAULT 0, refunded INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS admin_messages (
          id INTEGER PRIMARY KEY, source_key TEXT NOT NULL UNIQUE,
          kind TEXT NOT NULL, account_id INTEGER NOT NULL, account_name TEXT NOT NULL DEFAULT '',
          title TEXT NOT NULL, body TEXT NOT NULL, event_at TEXT NOT NULL,
          created_at TEXT NOT NULL, read_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_admin_messages_read ON admin_messages(read_at, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id);
        ''')
        columns = {row['name'] for row in c.execute('PRAGMA table_info(models)').fetchall()}
        if 'deleted' not in columns:
            c.execute('ALTER TABLE models ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')
        if 'points_due' not in {r['name'] for r in c.execute('PRAGMA table_info(users)')}:
            c.execute('ALTER TABLE users ADD COLUMN points_due INTEGER NOT NULL DEFAULT 0')
        job_columns = {r['name'] for r in c.execute('PRAGMA table_info(jobs)')}
        for name, declaration in (
            ('deleted', 'INTEGER NOT NULL DEFAULT 0'),
            ('billing_status', "TEXT NOT NULL DEFAULT ''"),
            ('reserved_points', 'INTEGER NOT NULL DEFAULT 0'),
            ('billing_check_at', 'INTEGER NOT NULL DEFAULT 0'),
        ):
            if name not in job_columns:
                c.execute(f'ALTER TABLE jobs ADD COLUMN {name} {declaration}')
        c.execute("""CREATE INDEX IF NOT EXISTS idx_jobs_billing_check ON jobs(billing_check_at)
                     WHERE billing_status='pending' AND status IN ('completed', 'failed')""")
        count = c.execute('SELECT COUNT(*) AS n FROM models').fetchone()['n']
        if count == 0:
            for kind, name, code, cost in (
                ('image', '演示生图 · 无需 API', 'demo-image', 0),
                ('video', '演示视频 · 非 AI 生成', 'demo-video', 0),
            ):
                c.execute('''INSERT INTO models
                  (name, model_code, kind, protocol, cost, created_at)
                  VALUES (?, ?, ?, 'demo', ?, ?)''', (name, code, kind, cost, now()))
        admin_name = os.getenv('ADMIN_USERNAME', 'admin').strip()
        admin = c.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
        if not admin:
            pwd = os.getenv('ADMIN_PASSWORD', '').strip()
            if len(pwd) < 12:
                pwd = secrets.token_urlsafe(18)
                initial_file = DATA / 'admin-initial-password.txt'
                with os.fdopen(os.open(initial_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as f:
                    f.write(f'管理员账号: {admin_name}\n初始密码: {pwd}\n请登录后在部署前修改配置，正式环境请设置 ADMIN_PASSWORD。\n')
                print(f'[SETUP] Admin password saved in {initial_file}', flush=True)
            c.execute('INSERT INTO users(username,password_hash,role,points,created_at) VALUES(?,?,\'admin\',10000,?)',
                      (admin_name, hash_password(pwd), now()))
