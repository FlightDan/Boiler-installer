"""Build only Boiler's generic payloads; device models are separate packages.

Inputs are freshly prepared Decky/firmware directories. Missing optional inputs
are recorded unavailable. Existing output is never merged with an older cache.
"""
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'components'))
from payload_lock import checked, record
from wireless import validate_bundle
from maintenance import runtime_files


def prepare(output, decky=None, wireless=()):
    output = Path(output)
    if output.exists(): raise FileExistsError('Output exists; use a fresh preparation directory')
    components = {'core:decky': {'available': False, 'reason': 'Explicit locked Decky input required'}}
    for vendor in ('intel', 'realtek', 'mediatek'):
        components['wifi:' + vendor] = {'available': False, 'reason': 'Explicit locked firmware input required'}
    staged = {}
    if decky:
        base = Path(decky)
        item = json.loads((base / 'manifest.json').read_text())
        if item.get('id') != 'core:decky' or item.get('available') is not True:
            raise ValueError('Decky input was not explicitly prepared')
        for entry in item['files']: staged[entry['source']] = checked(base, entry)
        for entry in item.get('notices', []): staged[entry['source']] = checked(base, entry)
        components['core:decky'] = item
    for prepared in wireless:
        base = Path(prepared)
        bundle = json.loads((base / 'manifest.json').read_text())
        vendor = bundle.get('vendor')
        validate_bundle(bundle, base, vendor)
        identity = 'wifi:' + vendor
        if components[identity].get('available'): raise ValueError('Duplicate prepared firmware vendor')
        files = []
        for entry in bundle['files']:
            data = checked(base, entry)
            name = 'wireless/' + vendor + '/' + entry['source']
            staged[name] = data
            files.append(record(name, data, target=entry['target']))
        licenses = []
        for entry in bundle.get('licenses', []):
            data = checked(base, entry); name = 'wireless/' + vendor + '/' + entry['source']
            staged[name] = data; licenses.append(record(name, data, target=entry['target']))
        if not licenses: raise ValueError('Firmware licenses missing')
        components[identity] = dict(bundle, available=True, files=files, licenses=licenses)
    runtime = []
    for source in runtime_files():
        if not source.is_file() or source.is_symlink(): raise FileNotFoundError('Maintenance runtime missing')
        relative = source.relative_to(ROOT).as_posix()
        data = source.read_bytes(); name = 'maintenance-runtime/' + relative
        staged[name] = data
        runtime.append(record(name, data, target='usr/local/lib/boiler/' + relative, mode=0o644))
    launcher = b'#!/bin/sh\nset -eu\ncd /usr/local/lib/boiler\nexec /usr/bin/python3 -B -m maintenance "$@"\n'
    staged['maintenance-runtime/boiler-maintenance'] = launcher
    runtime.append(record('maintenance-runtime/boiler-maintenance', launcher, target='usr/local/bin/boiler-maintenance', mode=0o755))
    component = dict(schema_version=1, kind='ComponentManifest', id='core:maintenance', version='0.1.0', files=runtime)
    components['core:maintenance'] = dict(available=True, files=runtime, component=component)
    output.mkdir(parents=True)
    for name, data in sorted(staged.items()):
        path = output / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    manifest = dict(schema=1, product='Boiler Installer', components=components)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--decky', type=Path)
    parser.add_argument('--wireless', type=Path, action='append', default=[])
    args = parser.parse_args()
    result = prepare(args.output, args.decky, args.wireless)
    print(json.dumps(dict(output=str(args.output), components={key: dict(available=value.get('available', False), files=len(value.get('files', []))) for key,value in result['components'].items()})))
