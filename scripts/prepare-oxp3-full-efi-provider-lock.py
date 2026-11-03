"""Derive the full OXP3 provider closure from its exact reviewed .14 base."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from protocol.manifest import validate_package,confined

BASE_SHA='11b2b74a636dfd892e3113b57e11e30f0445915eebf779f1449dd435c23a3e4d'
TRANSFORM_SHA='64fc948c584ba497ba9df419996c93feaf5120d3d64a17287a502729c3e50d0f'


def sha(raw): return hashlib.sha256(raw).hexdigest()


def transform_module():
    path=ROOT/'adaptations/brands/onexplayer/oxp3/bridge/full_efi_sources.py'
    if sha(path.read_bytes())!=TRANSFORM_SHA: raise ValueError('Full OXP3 source transform differs')
    spec=importlib.util.spec_from_file_location('_full_oxp3_transform',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def build(base):
    base=Path(base);package=validate_package(base,expected_manifest_sha256=BASE_SHA)
    if package['id']!='device:oxp3' or package['version']!='0.1.0-localtest.20261102.14':
        raise ValueError('Wrong reviewed OXP3 full base')
    baseline={r['path']:r for r in package['files']}
    reference=json.loads((ROOT/'components/oxp3_efi_provider.lock.json').read_bytes())
    if reference.get('kind')!='FixedOxp3EfiProviderSourceLock': raise ValueError('Wrong public closure definition')
    names=[r['path'] for r in reference['files']]+['maintenance_adapter.py','scripts/maintenance.sh']
    names+=sorted(n for n in baseline if n.startswith('payload/legacy/maintenance/app/oxp3_maintenance/')
                  and n.endswith('.py') and n not in names)
    if len(set(names))!=len(names): raise ValueError('Duplicate provider source')
    transform=transform_module();rows=[]
    for name in names:
        if name in ('adapter.py','maintenance_adapter.py'):
            raw=transform.transform(name,(ROOT/'adaptations/brands/onexplayer/oxp3/bridge'/name).read_bytes())
        else:
            record=baseline.get(name)
            if record is None: raise ValueError('Full base misses fixed provider member: '+name)
            raw=confined(base,name).read_bytes()
            if len(raw)!=record['size'] or sha(raw)!=record['sha256']:
                raise ValueError('Full base provider member differs: '+name)
        rows.append(dict(path=name,size=len(raw),sha256=sha(raw)))
    return dict(schema=1,kind='FixedFullOxp3EfiProviderSourceLock',public_commit=reference['public_commit'],
                provenance=dict(base_manifest_sha256=BASE_SHA,transform_sha256=TRANSFORM_SHA,
                                bridge_source='reviewed project-local bytes; public source commit pending'),files=rows)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();raw=(json.dumps(build(a.base),sort_keys=True,indent=2)+'\n').encode()
    if a.output.exists(): raise ValueError('Use a fresh output path')
    a.output.write_bytes(raw);print(sha(raw))
