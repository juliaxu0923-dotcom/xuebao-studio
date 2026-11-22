"""Verify and unpack the exact canvas runtime used by the Studio adapter."""
import hashlib
import json
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent


def main():
    artifact = json.loads((ROOT / 'artifacts/manifest.json').read_text(encoding='utf-8'))['runtime']
    archive = ROOT / 'artifacts' / artifact['filename']
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    if checksum != artifact['sha256']:
        raise SystemExit('Canvas archive checksum mismatch; restore the original artifact.')
    target = ROOT / 'work/infinite-inspect/resources/app/web'
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename:
                raise SystemExit('Unexpected path in canvas archive.')
        target.mkdir(parents=True, exist_ok=True)
        bundle.extractall(target)
    print(f'Canvas {artifact["version"]} restored: {target}')


if __name__ == '__main__':
    main()
