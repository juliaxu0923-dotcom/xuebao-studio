import os
import pathlib
import subprocess
import time
import urllib.request
import shutil

root = pathlib.Path.cwd()
subprocess.run([str(root / '.venv/Scripts/python.exe'), str(root / 'work/start_jimeng.py')], check=True)
renderer_root = root / 'work/infinite-inspect/resources/app/web'
try:
    with urllib.request.urlopen('http://127.0.0.1:8001/canvas', timeout=3) as response:
        if response.status != 200:
            raise RuntimeError('Canvas renderer is not ready')
except OSError:
    renderer_env = os.environ.copy()
    renderer_env.update(PORT='8001', HOSTNAME='127.0.0.1')
    node = shutil.which('node') or str(pathlib.Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')
    renderer = subprocess.Popen([node, 'server.js'], cwd=renderer_root, env=renderer_env,
        stdout=(root / 'work/infinite.out.log').open('w'), stderr=(root / 'work/infinite.err.log').open('w'),
        creationflags=subprocess.CREATE_NO_WINDOW)
    (root / 'work/infinite.pid').write_text(str(renderer.pid))
    for attempt in range(30):
        try:
            urllib.request.urlopen('http://127.0.0.1:8001/canvas', timeout=2).close()
            break
        except OSError:
            time.sleep(1)
    else:
        raise RuntimeError('Canvas renderer failed to start; see work/infinite.err.log')
pid_file = root / 'work/server.pid'
if pid_file.exists():
    subprocess.run(['taskkill', '/PID', pid_file.read_text().strip(), '/T', '/F'], capture_output=True)
env = os.environ.copy()
env['ALLOW_PRIVATE_PROVIDER_URLS'] = '1'
process = subprocess.Popen(
    [str(root / '.venv/Scripts/python.exe'), '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', '8000', '--workers', '1'],
    cwd=root / 'work/yingliu-studio', env=env,
    stdout=(root / 'work/server.out.log').open('w'), stderr=(root / 'work/server.err.log').open('w'),
    creationflags=subprocess.CREATE_NO_WINDOW)
pid_file.write_text(str(process.pid))
time.sleep(3)
with urllib.request.urlopen('http://127.0.0.1:8000/') as response:
    html = response.read().decode('utf-8')
    print('HTTP', response.status)
    print('canvas_composer_present', 'id="composer"' in html)
    print('image_page_present', 'id="imageView"' in html)
    print('video_page_present', 'id="videoView"' in html)
with urllib.request.urlopen('http://127.0.0.1:8000/static/app.js') as response:
    source = response.read().decode('utf-8')
    print('single_click_editor', 'editNode(n.id)' in source)
    print('canvas_submit_handler', "$('generateBtn').onclick" in source)
