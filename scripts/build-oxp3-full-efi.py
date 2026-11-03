"""Build an unsigned full OXP3 fixed-EFI candidate from explicit locked inputs."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from protocol.manifest import validate_package,confined
from protocol.local import import_local
from components.oxp3_full_efi_provider import verify


def module():
    spec=importlib.util.spec_from_file_location('_full_oxp3_lock',ROOT/'scripts/prepare-oxp3-full-efi-provider-lock.py')
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


def encoded(value): return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode()


def build(base,output,version='0.1.0-localtest.20261103.oxp3-full-efi.1'):
    base,output=Path(base).absolute(),Path(output).absolute(); prepared=module()
    if output.exists() or output.resolve().is_relative_to(base.resolve()): raise ValueError('Use a fresh output outside the input')
    if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in (output,*output.parents)):
        raise ValueError('Linked output ancestor')
    baseline=validate_package(base,expected_manifest_sha256=prepared.BASE_SHA)
    lock=(json.dumps(prepared.build(base),sort_keys=True,indent=2)+'\n').encode()
    if lock!=(ROOT/'components/oxp3_full_efi_provider.lock.json').read_bytes(): raise ValueError('Reviewed full OXP3 closure differs')
    directory=output/'package';directory.mkdir(parents=True)
    for row in baseline['files']:
        source=confined(base,row['path']);target=confined(directory,row['path']);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
        raw=target.read_bytes()
        if len(raw)!=row['size'] or prepared.sha(raw)!=row['sha256']: raise ValueError('Base changed during copy')
    transform=prepared.transform_module()
    for name in ('adapter.py','maintenance_adapter.py'):
        raw=(ROOT/'adaptations/brands/onexplayer/oxp3/bridge'/name).read_bytes()
        confined(directory,name).write_bytes(transform.transform(name,raw))
    manifest=json.loads(encoded(baseline));manifest['version']=version;manifest['minimum_engine_version']='0.1.4'
    manifest['components'][0]['version']=version;manifest['components'][0]['minimum_engine_version']='0.1.4'
    manifest.pop('oxp3_efi_contract',None)
    manifest['oxp3_full_efi_contract']={'kind':'oxp3-full-split-lock-efi-v1','component':'oxp3:legacy-adaptation'}
    manifest['full_efi_provenance']=dict(base_manifest_sha256=prepared.BASE_SHA,provider_lock_sha256=prepared.sha(lock),
        bridge_source='reviewed project-local bytes; public source commit pending')
    manifest['files']=[dict(path=p.relative_to(directory).as_posix(),size=p.stat().st_size,sha256=prepared.sha(p.read_bytes()))
                       for p in sorted(directory.rglob('*')) if p.is_file() and p!=directory/'manifest.json']
    (directory/'manifest.json').write_bytes(encoded(manifest));anchor=prepared.sha((directory/'manifest.json').read_bytes())
    validate_package(directory,expected_manifest_sha256=anchor);verify(directory,ROOT/'components/oxp3_full_efi_provider.lock.json')
    archive=output/('boiler-device-oxp3-'+version+'.zip')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as bundle:
        for name in ['manifest.json']+[r['path'] for r in manifest['files']]:
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.create_system=3
            info.external_attr=0o100644<<16;info.compress_type=zipfile.ZIP_DEFLATED
            with bundle.open(info,'w',force_zip64=True) as dst, confined(directory,name).open('rb') as src: shutil.copyfileobj(src,dst,1024*1024)
    imported=import_local(archive,output/'verified-import',developer=True)
    if imported['manifest_sha256']!=anchor: raise ValueError('Final archive import differs')
    report=dict(kind='Oxp3FullEfiCandidate',id='device:oxp3',version=version,manifest_sha256=anchor,
                archive=archive.name,size=archive.stat().st_size,sha256=prepared.sha(archive.read_bytes()),
                provider_lock_sha256=prepared.sha(lock),hardware_acceptance=False,official_release=False)
    (output/'verification.json').write_bytes(encoded(report));return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();print(json.dumps(build(a.base,a.output),indent=2))
