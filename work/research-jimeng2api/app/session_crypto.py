"""Local deployment extension: encrypt account sessions at rest."""
import os
from pathlib import Path
from cryptography.fernet import Fernet
from .config import settings


def cipher():
    path = Path(settings.data_dir) / '.session-key'
    if not path.exists():
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as f:
            f.write(Fernet.generate_key())
    return Fernet(path.read_bytes())


def seal(value):
    return 'enc:' + cipher().encrypt(value.encode()).decode()


def reveal(value):
    return cipher().decrypt(value[4:].encode()).decode() if value.startswith('enc:') else value
