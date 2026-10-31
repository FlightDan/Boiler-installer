"""Assemble the fixed, reviewed host runtime inventory; no package code runs.

This supersedes the prototype external-base/copytree builder. Public transaction
sources are retained in the repository with their origin and license. A fork
does not need an old checkout or historical device package to assemble them.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
LOCK = 'adaptations/records/x2-keyboard/runtime-source-lock.json'
LOCK_SHA256 = '0df9fbd4289a890db80e84e3a8d5036bb79129f851de4d83aed5360ddbb7fe7b'
RUNTIME = 'maintenance/keyboard_runtime'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def build(root, output):
    root,output = Path(os.path.abspath(root)),Path(os.path.abspath(output))
    for path in (root,output):
        if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in (path,*path.parents)):
            raise ValueError('Linked explicit keyboard build input/output')
    if output.exists() or output.resolve(strict=False).is_relative_to((root/RUNTIME).resolve()):
        raise ValueError('Use a fresh output outside the runtime source')
    sys.path.insert(0,str(ROOT))
    from protocol.manifest import confined, load_json, verify_file
    lock_path = confined(root,LOCK)
    raw = lock_path.read_bytes()
    if digest(raw) != LOCK_SHA256:
        raise ValueError('Keyboard runtime inventory differs from its reviewed independent pin')
    lock = load_json(lock_path)
    if lock.get('kind') != 'KeyboardRuntimeSourceInventory' or lock.get('schema_version') != 1:
        raise ValueError('Invalid fixed keyboard runtime inventory')
    rows,expected = [],set()
    for row in lock['runtime_sources']:
        name = row['source']
        if name in expected:
            raise ValueError('Duplicate keyboard source')
        path = confined(root,name)
        if any(part.casefold() in {'.local','.git','.codex','.agents','research'} for part in name.split('/')):
            raise ValueError('Private keyboard build source')
        verify_file(path,row)
        expected.add(name)
        rows.append((name,path.read_bytes()))
    # The only source subtree owned by this builder is closed, including data
    # and notices. An accidental public/unreviewed.txt is never copied.
    actual = {p.relative_to(root).as_posix() for p in (root/RUNTIME).rglob('*') if p.is_file() or p.is_symlink()}
    if actual != {name for name in expected if name.startswith(RUNTIME+'/')}:
        raise ValueError('Unreviewed extra or missing keyboard runtime source')
    output.mkdir(parents=True)
    for name,data in rows:
        path = confined(output,name)
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data)
    target = output/LOCK
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(raw)
    report = dict(kind='FixedKeyboardHostRuntimeBuild',installable_device_package=False,
        full_x2_adaptation_complete=False,official_release=False,
        lock_sha256=LOCK_SHA256,files=[dict(path=n,size=len(d),sha256=digest(d)) for n,d in rows])
    (output/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(build(args.project_root,args.output),indent=2))
