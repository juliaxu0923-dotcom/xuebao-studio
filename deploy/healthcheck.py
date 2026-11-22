"""Require the public app, renderer and private gateway to respond."""
from urllib.request import ProxyHandler, build_opener

client = build_opener(ProxyHandler({}))
for url in ('http://127.0.0.1:8000/', 'http://127.0.0.1:8001/canvas', 'http://127.0.0.1:8300/healthz'):
    with client.open(url, timeout=4) as response:
        if response.status != 200:
            raise SystemExit(1)
