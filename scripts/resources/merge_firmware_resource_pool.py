"""Merge explicit, digest-bound base/Wi-Fi pools into a fresh local candidate.

The base records and pending work stay intact. One fixed firmware resource is
referenced by both models; equal firmware payload bytes have one stored source.
This never authenticates an unsigned pool or executes its resource contents.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from protocol.resource_pool import validate_pool, decoder, local
from protocol.local import _safe_path
from protocol.firmware_contract import IDENTITY, validate_contract

BASE_SHA256 = '6672bdd06e2b6b7a23e0de71532b56c0052a7d2e85e4eaf24bb2ceacde487730'
WIFI_SHA256 = '8d2cb30715f311af7bd1a4bca124c5e5eccf6266cab0f8d076649f3293fd4aae'
MODELS = {'device:oxp3', 'device:x2-mini'}


def sha(data): return hashlib.sha256(data).hexdigest()
def json_bytes(value): return (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode()


def checked_input(root, digest):
    root = _safe_path(root)
    catalog = validate_pool(root, expected_catalog_sha256=digest)
    files = {}
    # Snapshot only inventoried bytes and bind them again to the checked input.
    for row in catalog['files']:
        raw = local(root, row['path']).read_bytes()
        if len(raw) != row['size'] or sha(raw) != row['sha256']: raise ValueError('Input changed during merge')
        files[row['path']] = raw
    if sha(local(root, 'catalog.toon').read_bytes()) != digest: raise ValueError('Input catalog changed during merge')
    return catalog, files


def build(base, wifi, output, *, base_sha256=BASE_SHA256, wifi_sha256=WIFI_SHA256):
    base, wifi, output = _safe_path(base), _safe_path(wifi), _safe_path(output)
    for source in (base, wifi):
        if source == output or source in output.parents: raise ValueError('Output cannot be inside an input pool')
    if os.path.lexists(output): raise FileExistsError('Use a fresh combined pool output')
    base_catalog, base_files = checked_input(base, base_sha256)
    wifi_catalog, wifi_files = checked_input(wifi, wifi_sha256)
    if (len(base_catalog['models']) != 2 or len(wifi_catalog['models']) != 2
            or {m['id'] for m in base_catalog['models']} != MODELS or {m['id'] for m in wifi_catalog['models']} != MODELS):
        raise ValueError('The two explicit OXP3/X2 Mini model sets must match')
    if len(wifi_catalog['resources']) != 1 or wifi_catalog['resources'][0]['id'] != IDENTITY:
        raise ValueError('The Wi-Fi input must contain only the fixed firmware resource')
    if any(r['id'] == IDENTITY for r in base_catalog['resources']): raise ValueError('Base already contains the Wi-Fi resource')
    resource = copy.deepcopy(wifi_catalog['resources'][0]); validate_contract(resource)
    files = {}
    def write(name, raw):
        if name in files and files[name] != raw: raise ValueError('Conflicting source bytes: '+name)
        files[name] = raw
    toon = decoder()
    def document(name, value): write(name, (toon.dumps(value)+'\n').encode())
    base_models = {r['path'] for r in base_catalog['model_refs']}
    for name, raw in base_files.items():
        if name not in base_models: write(name, raw)

    canonical = {}
    for original in base_catalog['resources']:
        for entry in original['payload']['files']:
            key = (entry['sha256'], entry['size']); previous = canonical.get(key)
            if previous is not None and previous != entry['path']:
                raise ValueError('Base already stores duplicated payload bytes; review it explicitly')
            canonical[key] = entry['path']
    rename = {}; payload = {}; deduplicated = []
    for original in resource['payload']['files']:
        entry = dict(original); old = entry['path']; key = (entry['sha256'], entry['size'])
        name = canonical.setdefault(key, old); rename[old] = name; entry['path'] = name; payload[name] = entry
        write(name, wifi_files[old])
        if name != old: deduplicated.append(dict(source=old, canonical=name, sha256=entry['sha256'], size=entry['size']))
    resource['payload']['files'] = list(payload.values())
    for row in resource['install']['files']: row['source'] = rename[row['source']]
    for row in resource.get('provenance', {}).get('files', []):
        if row.get('payload') in rename: row['payload'] = rename[row['payload']]
    resource['license_files'] = [rename.get(name, name) for name in resource['license_files']]
    validate_contract(resource)
    resource_path = wifi_catalog['resource_refs'][0]['path']
    derived_json = str(Path(resource_path).with_suffix('.json')).replace('\\', '/')
    skipped = {r['path'] for r in wifi_catalog['model_refs']} | set(rename) | {resource_path, derived_json}
    for name, raw in wifi_files.items():
        if name not in skipped: write(name, raw)
    document(resource_path, resource)
    if derived_json in wifi_files: write(derived_json, json_bytes(resource))
    resources = copy.deepcopy(base_catalog['resource_refs'])
    resources.append(dict(id=resource['id'], version=resource['version'], path=resource_path, sha256=sha(files[resource_path])))

    models = []
    for original in base_catalog['models']:
        model = copy.deepcopy(original)
        if len({r['resource'] for r in model['resources']}) != len(model['resources']): raise ValueError('Duplicate base model reference')
        extra = next(m for m in wifi_catalog['models'] if m['id'] == model['id'])
        if len(extra['resources']) != 1 or extra['resources'][0]['resource'] != IDENTITY or extra['resources'][0].get('parameters'):
            raise ValueError('Unexpected Wi-Fi model resource reference')
        model['resources'].append(dict(resource=IDENTITY, enabled=False, default=False, parameters={}))
        pending = model.setdefault('pending', [])
        for row in extra.get('pending', []):
            if not isinstance(row, dict) or not isinstance(row.get('component'), str): raise ValueError('Invalid Wi-Fi pending display record')
            if row not in pending: pending.append(copy.deepcopy(row))
        model['complete'] = False
        path = next(r['path'] for r in base_catalog['model_refs'] if r['id'] == model['id'])
        document(path, model); models.append(dict(id=model['id'], path=path, sha256=sha(files[path])))
    provenance = dict(kind='ExplicitFixedWifiPoolMerge', official_release=False,
        inputs=[dict(role='base', catalog_sha256=base_sha256), dict(role='wifi', catalog_sha256=wifi_sha256)],
        shared_resource=IDENTITY, model_ids=sorted(MODELS), payload_deduplication=deduplicated,
        preserved_base_resources=[r['id'] for r in base_catalog['resources']],
        note='Original public provenance and licenses are retained. Existing model identity, parameters and pending work are preserved; hardware acceptance is not granted.')
    write('provenance/merge-fixed-wifi-v1.json', json_bytes(provenance))
    inventory = [dict(path=name, size=len(raw), sha256=sha(raw)) for name, raw in sorted(files.items())]
    catalog = dict(schema_version=1, kind='BoilerResourceCatalog', trust='unsigned-development', official_release=False,
        resources=resources, models=models, files=inventory)
    document('catalog.toon', catalog); write('catalog.json', json_bytes(catalog))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.merge-fixed-wifi-', dir=output.parent) as temporary:
        ready = Path(temporary)/'pool'; ready.mkdir()
        for name, raw in files.items():
            path = local(ready, name); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        validate_pool(ready, expected_catalog_sha256=sha(files['catalog.toon']))
        if os.path.lexists(output): raise FileExistsError('Combined output already exists')
        _safe_path(output); os.rename(ready, output)
    return dict(resources=len(resources), models=len(models), inventory_members=len(inventory),
        payload_members=len(canonical), deduplicated_members=len(deduplicated), catalog_sha256=sha(files['catalog.toon']), official_release=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True); parser.add_argument('--wifi', required=True); parser.add_argument('--output', required=True)
    parser.add_argument('--base-sha256', required=True); parser.add_argument('--wifi-sha256', required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.wifi, args.output, base_sha256=args.base_sha256, wifi_sha256=args.wifi_sha256), indent=2))
