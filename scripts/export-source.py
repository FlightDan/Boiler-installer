"""Export reviewed public sources into a fresh directory without private state."""
import argparse
import fnmatch
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.manifest import confined, relative_path


def reviewed_files(root=ROOT):
    policy = json.loads((root/'scripts/source-files.json').read_text(encoding='utf-8'))
    files = policy.get('files')
    if (policy.get('kind') != 'BoilerSourceWhitelist' or not isinstance(files, list)
            or any(not isinstance(name, str) for name in files)
            or len({name.casefold() for name in files}) != len(files)):
        raise ValueError('Invalid or duplicate source whitelist entry')
    for name in files:
        relative_path(name)
        lower_name = name.casefold()
        parts = lower_name.split('/')
        if (any(part in {'.local', '.git', '.codex', '.agents', '__pycache__', 'research'} for part in parts)
                or (name.lower().endswith(('.pem', '.key', '.private', '.pfx', '.p12', '.pyc', '.pdb'))
                    and name != 'protocol/trusted-public-key.pem')
                or any(fnmatch.fnmatchcase(lower_name, pattern.casefold()) for pattern in policy.get('excluded', []))):
            raise ValueError('Private or generated path conflicts with source whitelist: '+name)
        if not confined(root, name).is_file():
            raise ValueError('Whitelisted source missing: '+name)
    return files


def verify_adaptation_snapshots(root, files):
    for name in ('source-lock.json', 'current-source-lock.json'):
        lock = root/'adaptations'/name
        if not lock.exists():
            raise ValueError('Required adaptation source inventory is missing: '+name)
        for entry in json.loads(lock.read_bytes())['files']:
            relative = 'adaptations/'+entry['path']
            if relative not in files:
                raise ValueError('Adaptation source inventory is outside the reviewed source whitelist: '+relative)
            data = confined(root, relative).read_bytes()
            if len(data) != entry['size'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise ValueError('Adaptation source differs from reviewed inventory: '+relative)


def export(destination):
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError('Use a new isolated source checkout directory')
    files = reviewed_files()
    verify_adaptation_snapshots(ROOT, set(files))
    inventory = []
    for name in files:
        source = confined(ROOT, name)
        target = confined(destination, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open('rb') as incoming, target.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        inventory.append(dict(path=name, size=target.stat().st_size,
                              sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
    return dict(directory=str(destination), files=inventory, history='none')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(export(args.destination), indent=2))
