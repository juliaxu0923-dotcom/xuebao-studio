"""Start the local, authenticated Jimeng gateway without exposing its key."""
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

root = Path(__file__).resolve().parent.parent
studio = root / 'work/yingliu-studio'
sys.path.insert(0, str(studio))
from backend.jimeng import gateway_key, install_model
from backend.db import DATA, setup_db

def start():
    setup_db()
    env = os.environ.copy()
    env.update(JIMENG2API_ADMIN_KEY=gateway_key(), JIMENG2API_DATA=str(DATA / 'jimeng-pool'))
    try:
        urllib.request.urlopen('http://127.0.0.1:8300/healthz', timeout=2).close()
    except OSError:
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:create_app', '--factory',
            '--host', '127.0.0.1', '--port', '8300'], cwd=root / 'work/research-jimeng2api', env=env,
            stdout=(root / 'work/jimeng-pool.out.log').open('a'),
            stderr=(root / 'work/jimeng-pool.err.log').open('a'),
            creationflags=subprocess.CREATE_NO_WINDOW)
        (root / 'work/jimeng-pool.pid').write_text(str(process.pid))
    for _ in range(30):
        try:
            req = urllib.request.Request('http://127.0.0.1:8300/panel/api/accounts',
                headers={'Authorization': 'Bearer ' + gateway_key()})
            urllib.request.urlopen(req, timeout=2).close()
            print('Jimeng gateway ready on 127.0.0.1:8300')
            return
        except OSError:
            time.sleep(1)
    raise RuntimeError('Jimeng gateway failed to start; inspect work/jimeng-pool.err.log')

if __name__ == '__main__':
    start()
