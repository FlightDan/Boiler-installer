"""Refresh the canonical adaptation source inventory from its explicit path list."""
import argparse
import hashlib
import json
from pathlib import Path


INVENTORY = 'model-source-files.json'
PRIVATE = {'.git', '.local', '.codex', '.agents', '.aws', '__pycache__'}


def source_path(root, name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise ValueError('Expected a repository-relative source path')
    parts = name.split('/')
    if (any(p in ('', '.', '..') or p.endswith(('.', ' ')) or p.casefold() in PRIVATE for p in parts)
            or name == INVENTORY or parts[0] == 'inputs'
            or name.startswith('records/legacy-adaptations-373fe55/')):
        raise ValueError('Path is outside the active source inventory: ' + name)
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise ValueError('Linked source member: ' + name)
    return path


def refresh(root, *, add=(), remove=(), check=False):
    root = Path(root).resolve(strict=True)
    inventory = root / INVENTORY
    current = json.loads(inventory.read_bytes())
    if (current.get('schema_version') != 1 or current.get('kind') != 'CanonicalAdaptationSourceFiles'
            or not isinstance(current.get('files'), list)):
        raise ValueError('Invalid canonical source inventory')
    previous = [row['path'] for row in current['files']]
    names = set(previous)
    if len(names) != len(previous):
        raise ValueError('Duplicate canonical source path')
    if set(add) & set(remove) or not set(remove) <= names:
        raise ValueError('Invalid source path additions or removals')
    if check and (add or remove):
        raise ValueError('Use --write to add or remove source paths')
    names.difference_update(remove)
    names.update(add)
    rows = []
    for name in sorted(names):
        raw = source_path(root, name).read_bytes()
        rows.append(dict(path=name, size=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
    result = dict(current, files=rows)
    if check:
        if result != current:
            raise ValueError('Canonical model source inventory is stale; run this tool with --write')
    else:
        inventory.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    return len(rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--write', action='store_true')
    parser.add_argument('--add', action='append', default=[])
    parser.add_argument('--remove', action='append', default=[])
    args = parser.parse_args()
    print('Canonical model source inventory:', refresh(args.root, add=args.add, remove=args.remove, check=args.check), 'files')
