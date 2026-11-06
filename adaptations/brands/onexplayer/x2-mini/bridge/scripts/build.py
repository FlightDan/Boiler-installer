"""Build the reviewed, partial X2 shared package from explicit project sources.

No old bundle, model cache, device, deployment hook, signing key or network is read.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

BRIDGE = Path(__file__).resolve().parents[1]

def digest(data):
    return hashlib.sha256(data).hexdigest()

def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode()

def build(project_root, output, adaptations_root=None):
    project_root, output = Path(project_root).resolve(), Path(output).absolute()
    sys.path.insert(0, str(project_root))
    from protocol.manifest import confined, validate_package
    from protocol.local import import_local
    if output.exists() or output.is_symlink():
        raise ValueError('Use a new output directory')
    lock = json.loads((BRIDGE/'source-lock.json').read_bytes())
    if lock.get('kind') != 'X2SharedSourceLock' or lock.get('schema_version') != 1:
        raise ValueError('Explicit reviewed X2 source lock is required')
    if lock.get('delivery_scope') != 'partial-shared-adaptations':
        raise ValueError('This builder does not implement the full X2 adaptation list')
    adaptations = Path(adaptations_root).resolve() if adaptations_root else project_root/'adaptations'
    parser_path = confined(project_root, 'scripts/toon_catalog.py')
    spec = importlib.util.spec_from_file_location('boiler_x2_toon_catalog', parser_path)
    catalog = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(catalog)
    selected = [(name,model) for name,model in catalog.models(adaptations) if model['id']=='guide:x2-mini']
    if len(selected) != 1:
        raise ValueError('One X2 Mini TOON model definition is required')
    toon_path, model = selected[0]
    if model['build']['adapter'] != 'x2-current-v1' or model['build']['complete'] is not False:
        raise ValueError('The partial X2 model must select its reviewed source-only adapter')
    lock_name = 'adaptations/brands/onexplayer/x2-mini/bridge/source-lock.json'
    pins = [row for row in model['build']['materials'] if row['lock'] == lock_name]
    if len(pins) != 1 or pins[0]['sha256'] != digest((BRIDGE/'source-lock.json').read_bytes()):
        raise ValueError('X2 source lock differs from the model TOON material pin')
    template = copy.deepcopy(model['package'])
    if 'files' in template:
        raise ValueError('Package file inventory is generated from locked bytes, not hand-maintained')
    rows, targets = [], set()
    for entry in lock['inputs']:
        if entry['location'] not in ('project', 'bridge'):
            raise ValueError('Unsupported input location')
        if any(part.casefold() in {'.local','.git','.codex','.agents','.aws','research'} for part in entry['source'].split('/')):
            raise ValueError('Private or research material cannot be a package source')
        source = confined(project_root if entry['location'] == 'project' else BRIDGE, entry['source'])
        raw = source.read_bytes()
        if len(raw) != entry['size'] or digest(raw) != entry['sha256']:
            raise ValueError('Source differs from reviewed lock: '+entry['source'])
        if entry['output'] in targets:
            raise ValueError('Duplicate output in source lock')
        targets.add(entry['output'])
        rows.append((entry, raw))
    expected = {part['id'] for part in template['components']}
    if (not expected or len(expected) != len(template['components'])
            or any(part.get('hooks') for part in template['components'])
            or template.get('delivery_scope') != 'partial-shared-adaptations'):
        raise ValueError('A partial static X2 package definition must be supplied by TOON')
    output.mkdir(parents=True)
    package = output/'package'; package.mkdir()
    for entry, raw in rows:
        path = confined(package, entry['output']); path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(raw)
    template['files'] = [dict(path=p.relative_to(package).as_posix(),size=p.stat().st_size,sha256=digest(p.read_bytes()))
                         for p in sorted(package.rglob('*')) if p.is_file()]
    (package/'manifest.json').write_bytes(json_bytes(template))
    anchor=digest((package/'manifest.json').read_bytes())
    validate_package(package,expected_manifest_sha256=anchor)
    archive=output/('boiler-device-x2-mini-'+template['version']+'.zip')
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(['manifest.json']+[row['path'] for row in template['files']]):
            info=zipfile.ZipInfo(name,date_time=(2026,10,30,0,0,0)); info.compress_type=zipfile.ZIP_DEFLATED
            bundle.writestr(info,(package/name).read_bytes())
    result=import_local(archive,output/'verified-import',developer=True)
    if result['manifest_sha256'] != anchor:
        raise ValueError('Final ZIP import differs from built package')
    report=dict(kind='X2SharedSourceBuildVerification',build_verified=True,
        delivery_scope='partial-shared-adaptations',full_x2_adaptation_complete=False,
        version=template['version'],manifest_sha256=anchor,
        model_toon=toon_path,model_toon_sha256=digest(confined(adaptations,toon_path).read_bytes()),
        source_lock_sha256=digest((BRIDGE/'source-lock.json').read_bytes()),
        archive=archive.name,size=archive.stat().st_size,sha256=digest(archive.read_bytes()),
        components=sorted(expected),files=len(template['files']),
        pending=model['build']['missing'],hardware_acceptance=False,deployment_executed=False)
    (output/'verification.json').write_bytes(json_bytes(report))
    return report

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--adaptations-root',type=Path,help='Explicit staged catalog root; default is PROJECT_ROOT/adaptations')
    args=parser.parse_args()
    print(json.dumps(build(args.project_root,args.output,args.adaptations_root),ensure_ascii=False,indent=2))
