"""Frozen partial-model data for existing tests, independent of current TOON."""
import hashlib
import json
from pathlib import Path

FIXTURE=Path(__file__).resolve().parent/'fixtures/x2/partial-shared.4'
FIXTURE_LOCK_SHA256='b54e4a387314a2b4f086453c559bb63d7c76dc9de657733b5a33217af2316538'


def copy_toon_sources(parser_source, destination, *, catalog=None):
    """Use current authenticated parser code with exact historical model data.

    No current model TOON, installed user state, old bundle or product migration
    fallback is discovered. This helper is only in the tests source tree.
    """
    raw=(FIXTURE/'fixture-lock.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=FIXTURE_LOCK_SHA256:
        raise ValueError('Historical partial test fixture inventory changed')
    lock=json.loads(raw)
    rows=[]
    for row in lock['files']:
        raw=(FIXTURE/row['path']).read_bytes()
        if len(raw)!=row['size'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:
            raise ValueError('Historical partial test fixture bytes changed: '+row['path'])
        rows.append((row['path'],raw))
    parser_source,destination=Path(parser_source),Path(destination)
    names={'toon-parser-lock.json'}
    parser_lock=json.loads((parser_source/'toon-parser-lock.json').read_bytes())
    names.update('vendor/'+row['path'] for row in parser_lock['files'])
    rows.extend((name,(parser_source/name).read_bytes()) for name in sorted(names))
    for name,raw in rows:
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(raw)
    if catalog is not None:
        target=destination.parent/'scripts/toon_catalog.py'
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(Path(catalog).read_bytes())
