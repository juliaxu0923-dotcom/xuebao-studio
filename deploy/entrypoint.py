"""Initialize private storage and supervise the three local services."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'work/yingliu-studio'))
os.umask(0o077)

from backend.db import DATA, setup_db
from backend.jimeng import gateway_key

setup_db()
os.environ['JIMENG2API_ADMIN_KEY'] = gateway_key()
os.environ['JIMENG2API_DATA'] = str(DATA / 'jimeng-pool')
os.execv('/usr/bin/supervisord', ['supervisord', '-c', str(ROOT / 'deploy/supervisord.conf')])
