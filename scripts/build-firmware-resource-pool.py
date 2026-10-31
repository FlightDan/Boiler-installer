"""Materialize the reviewed BE201 source candidate into a local-test pool.

The input is explicit and inventory-pinned. No adjacent project or old package
is discovered, and a fixed handler does not turn this unsigned pool official.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protocol.resource_pool import decoder, local, validate_pool
from protocol.firmware_contract import KIND, VERIFY, RESTORE, validate_contract

ORIGINAL_INVENTORY = '189d61cc3bf333d902acc30a3dec84787f6913764843f4bfab7f81e7a3ea2257'
RESOURCE = 'devices/network/intel/be201/resource.toon'


def sha(data): return hashlib.sha256(data).hexdigest()


def build(candidate, output):
    candidate, output = Path(candidate), Path(output)
    raw = local(candidate, 'inventory.json').read_bytes()
    if sha(raw) != ORIGINAL_INVENTORY: raise ValueError('Reviewed Wi-Fi candidate inventory changed')
    inventory = json.loads(raw); entries = {}; contents = {}
    for row in inventory['files']:
        name = row['path']; data = local(candidate, name).read_bytes()
        if name in entries or len(data) != row['size'] or sha(data) != row['sha256']: raise ValueError('Candidate member changed: '+name)
        entries[name] = row; contents[name] = data
    if {p.relative_to(candidate).as_posix() for p in candidate.rglob('*') if p.is_file()} != set(entries)|{'inventory.json'}:
        raise ValueError('Candidate member set changed')
    toon = decoder(); resource = toon.loads(contents[RESOURCE].decode(), strict=True)
    if resource != json.loads(contents[RESOURCE.replace('.toon', '.json')]): raise ValueError('Candidate JSON differs from TOON')
    resource = copy.deepcopy(resource)
    resource.update(version='0.1.0+wifi-runtime.20261031', status='fixed-handler-implemented-hardware-pending')
    resource['install']['kind'] = KIND; resource['verify'] = VERIFY; resource['restore'] = RESTORE
    resource['acceptance'].update(fixed_handler_registered=True, production_handler_registered=False, synthetic_lifecycle_only=True)
    resource['limitations'].append('Fixed handler file lifecycle is implemented; physical facts transport, firmware load, Wi-Fi/Bluetooth and boot/recovery acceptance are still required.')
    validate_contract(resource)
    if output.exists(): raise FileExistsError('Use a new output directory')
    output.mkdir(parents=True)
    def write(name, data):
        target = local(output, name); target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
    def document(name, value): write(name, (toon.dumps(value)+'\n').encode())
    # Copy only explicitly pinned firmware/source/license/provenance members.
    for name, data in contents.items():
        if name.startswith(('devices/network/intel/be201/files/', 'devices/network/intel/be201/sources/', 'provenance/')):
            write(name, data)
    document(RESOURCE, resource)
    write(RESOURCE.replace('.toon', '.json'), (json.dumps(resource, ensure_ascii=False, indent=2)+'\n').encode())
    models = []
    for identity, name in (('oxp3', 'OneXPlayer OXP3'), ('x2-mini', 'OneXPlayer X2 Mini')):
        path = 'brands/onexplayer/models/'+identity+'.toon'
        document(path, dict(schema_version=1, kind='BoilerResourceModel', id='device:'+identity, name=name,
            hardware_matching=dict(advisory_only=True), resources=[dict(resource=resource['id'], default=False)],
            pending=[dict(component='physical-wifi-acceptance', reason='Trusted physical facts, firmware load and radio/boot/recovery acceptance are pending')]))
        models.append(dict(id='device:'+identity, path=path, sha256=sha((output/path).read_bytes())))
    files = [dict(path=p.relative_to(output).as_posix(), size=p.stat().st_size, sha256=sha(p.read_bytes()))
             for p in sorted(output.rglob('*')) if p.is_file()]
    catalog = dict(schema_version=1, kind='BoilerResourceCatalog', trust='unsigned-development', official_release=False,
        resources=[dict(id=resource['id'], version=resource['version'], path=RESOURCE, sha256=sha((output/RESOURCE).read_bytes()))], models=models, files=files)
    document('catalog.toon', catalog)
    write('catalog.json', (json.dumps(catalog, ensure_ascii=False, indent=2)+'\n').encode())
    validate_pool(output, expected_catalog_sha256=sha((output/'catalog.toon').read_bytes()))
    return catalog


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True); parser.add_argument('--output', required=True)
    args = parser.parse_args(); print(json.dumps(build(args.candidate, args.output)))
