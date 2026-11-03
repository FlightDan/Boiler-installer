"""Add the experimental fingerprint component to an explicitly anchored model package."""
import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import stat
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from protocol.manifest import confined, validate_package
from protocol.local import import_local

spec = importlib.util.spec_from_file_location('fingerprint_prepare', Path(__file__).with_name('prepare-fingerprint.py'))
prep = importlib.util.module_from_spec(spec); spec.loader.exec_module(prep)
VERSION = '0.1.0-localtest.20261104.fingerprint.10'
REPOSITORY = 'FlightDan/Boiler-installer'
RELEASE_BASES = {'device:oxp3':'c4ab2fb447c071191fd9a5786295bd1da1e8395c3a54355315aeb5b6c7179857',
                 'device:x2-mini':'a31ad3041b60a5f36a7ce9d694904d252cbf10e6beb3511eaa074ab97be713a1'}
RELEASE_NATIVE = 'a51df24539a63ad82e74ac6b911f28835da19211ee50fd7c90457f15db20936f'
PLUGIN = 'var/lib/oxp3-installer/decky/runtime/plugins/Boiler-Fingerprint-Unlock/'
NATIVE = 'var/lib/oxp3-installer/fingerprint/'
PROFILE = 'brands/onexplayer/oxp3/current/fingerprint/'
BRIDGE = 'common/fingerprint/bridge/'
SHARED_PROFILE = 'common/fingerprint/runtime-profile/'
X2_RULE = 'brands/onexplayer/x2-mini/current/fingerprint/69-boiler-x2-fingerprint.rules'
MODEL_RULES = {'device:oxp3': (PROFILE+'69-boiler-oxp3-fingerprint.rules', 'etc/udev/rules.d/69-boiler-oxp3-fingerprint.rules'),
               'device:x2-mini': (X2_RULE, 'etc/udev/rules.d/69-boiler-x2-fingerprint.rules')}
TARGETS = {
    SHARED_PROFILE+'spidev.conf':'etc/modprobe.d/boiler-oxp3-fingerprint.conf',
    SHARED_PROFILE+'boiler-fprintd.service':'etc/systemd/system/boiler-fprintd.service',
    SHARED_PROFILE+'net.reactivated.Fprint.service':'usr/share/dbus-1/system-services/net.reactivated.Fprint.service',
    SHARED_PROFILE+'profile.json':PLUGIN+'profile.json',
    BRIDGE+'main.py':PLUGIN+'main.py', BRIDGE+'index.js':PLUGIN+'dist/index.js',
    BRIDGE+'plugin.json':PLUGIN+'plugin.json', BRIDGE+'package.json':PLUGIN+'package.json',
}


def digest(data): return hashlib.sha256(data).hexdigest()
def encoded(data): return (json.dumps(data, ensure_ascii=False, indent=2)+'\n').encode()


def release_archive(stage, archive, package, rows):
    modes = {}
    for component in package['components']:
        for item in component.get('files', []):
            mode = item.get('mode', 0o644)
            old = modes.setdefault(item['source'], mode)
            if old != mode:
                raise ValueError('Conflicting archive modes for component source')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as bundle:
        for name in ['manifest.json']+[row['path'] for row in rows]:
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | modes.get(name, 0o644)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            info._compresslevel = 1
            with confined(stage, name).open('rb') as source, bundle.open(info, 'w', force_zip64=True) as target:
                shutil.copyfileobj(source, target, length=1024*1024)


def release_inputs(model_id, base_sha256, native_sha256, version, commit, repository):
    if model_id not in RELEASE_BASES or base_sha256 != RELEASE_BASES[model_id] or native_sha256 != RELEASE_NATIVE:
        raise ValueError('Release inputs differ from fixed public anchors')
    if not isinstance(version, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?', version) or 'localtest' in version.lower():
        raise ValueError('Release requires an explicit non-localtest version')
    if repository != REPOSITORY or not isinstance(commit, str) or not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('Release requires original repository and full source commit')
    def git(*args, input=None):
        return subprocess.run(['git', '-C', str(ROOT), *args], input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout
    try:
        if git('remote', 'get-url', 'origin').decode().strip() not in ('https://github.com/'+REPOSITORY+'.git', 'https://github.com/'+REPOSITORY):
            raise ValueError('Unexpected source repository origin')
        if git('rev-parse', '--verify', commit+'^{commit}').decode().strip() != commit:
            raise ValueError('Invalid source commit')
        if git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip() != commit:
            raise ValueError('Selected source commit must equal HEAD')
        files = json.loads((ROOT/'scripts/source-files.json').read_bytes())['files']
        required = {'scripts/source-files.json', 'adaptations/scripts/build-fingerprint-overlay.py',
                    'adaptations/scripts/prepare-fingerprint.py', 'adaptations/scripts/validate-fingerprint-native.py',
                    'adaptations/scripts/fingerprint_provenance.py', 'adaptations/scripts/toon_catalog.py',
                    'adaptations/fingerprint-source-lock.json', 'protocol/manifest.py', 'protocol/local.py'}
        if len(files) != len(set(files)) or not required.issubset(files):
            raise ValueError('Invalid source whitelist')
        tree = {}
        for entry in git('ls-tree', '-r', '-z', commit).split(b'\0'):
            if entry:
                meta, name = entry.split(b'\t', 1)
                mode, kind, blob = meta.split()
                tree[name.decode('utf-8')] = (mode, kind, blob)
        for name in files:
            path = confined(ROOT, name)
            if not path.is_file() or tree.get(name, (None,None,None))[:2] not in ((b'100644',b'blob'),(b'100755',b'blob')):
                raise ValueError('Source missing or not a regular committed file: '+name)
        blobs = git('hash-object', '--no-filters', '--stdin-paths', input=('\n'.join(files)+'\n').encode()).splitlines()
        if len(blobs) != len(files) or any(blob != tree[name][2] for name,blob in zip(files,blobs)):
            raise ValueError('Public source bytes differ from selected commit')
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError('Cannot verify selected source commit') from exc


def build(base, base_sha256, runtime, native_sha256, source_sha256, output, model_id=None, *, release=False, version=None, source_commit=None, source_repository=None):
    if release:
        release_inputs(model_id, base_sha256, native_sha256, version, source_commit, source_repository)
    elif any(v is not None for v in (version, source_commit, source_repository)):
        raise ValueError('Release metadata requires explicit release mode')
    package_version = version if release else VERSION
    base, output = Path(base).resolve(), Path(output).absolute()
    if output.exists(): raise ValueError('Fresh immutable output required')
    package = validate_package(base, expected_manifest_sha256=base_sha256)
    identity = package.get('id')
    if identity not in MODEL_RULES or (model_id is not None and identity != model_id) or any(x['id'] == 'oxp3:fingerprint-unlock' for x in package['components']):
        raise ValueError('Expected matching supported model base without fingerprint component')
    if not isinstance(package.get('shared_adaptations', {}).get('components'), list):
        raise ValueError('Base package lacks static maintenance contract')
    prep.prepare(runtime, native_sha256, source_sha256, output/'prepared')
    stage = output/'package'; stage.mkdir()
    for row in package['files']:
        target = confined(stage, row['path']); target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(confined(base, row['path']), target)
    package = json.loads(encoded(package)); package['version'] = package_version
    if release:
        package['source'] = {'kind':'github','repository':source_repository,'commit':source_commit}
        package['trust'] = {'status':'index-signature-required','official_release':False}
        package['minimum_engine_version'] = '0.1.5'
    component = {'schema_version':1,'kind':'ComponentManifest','id':'oxp3:fingerprint-unlock','version':package_version,
        'name':{'zh':'指纹解锁（实验）','en':'Fingerprint unlock (experimental)'},
        'description':{'zh':'默认开启；下次启动生效。保留 PIN 和已有指纹数据，硬件兼容性尚需验证。','en':'Enabled by default; active after next boot. PIN and existing fingerprints are retained; hardware compatibility remains unverified.'},
        'default':True,'optional':True,'requires':[],'conflicts':[], 'files':[],
        'compatibility':{'architectures':['x86_64'],'os_ids':['steamos'],'os_versions':['3.10'],'device_ids':[identity.removeprefix('device:')]}}
    if release:
        component['minimum_engine_version'] = '0.1.5'
    prepared = output/'prepared'
    def add(data, source, target, mode):
        path=confined(stage, source); path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
        component['files'].append({'source':source,'target':target,'size':len(data),'sha256':digest(data),'mode':mode})
    for name, target in [MODEL_RULES[identity], *TARGETS.items()]:
        data = confined(prepared/'sources', name).read_bytes()
        add(data, 'fingerprint/profile/'+name, target, 0o644)
    native_manifest = json.loads((prepared/'native/runtime-manifest.json').read_bytes())
    for row in native_manifest['files']:
        name = row['path']
        if name.startswith('licenses/') or name == 'closure-proof.json': continue
        configuration = {'config/net.reactivated.Fprint.conf':'etc/dbus-1/system.d/net.reactivated.Fprint.conf',
            'config/net.reactivated.fprint.device.policy':'usr/share/polkit-1/actions/net.reactivated.fprint.device.policy'}
        target = configuration.get(name, NATIVE+name)
        add(confined(prepared/'native',name).read_bytes(), 'fingerprint/native/'+name, target, row['mode'])
    for name in ('runtime-manifest.json','closure-proof.json'):
        data=(prepared/'native'/name).read_bytes(); path=stage/'fingerprint/evidence'/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
    for row in native_manifest['files']:
        if row['path'].startswith('licenses/'):
            source=confined(prepared/'native',row['path']); target=stage/'fingerprint'/row['path']; target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(source,target)
    upstream_root = ROOT/'adaptations/common/fingerprint/upstream'
    upstream_lock = ROOT/'adaptations/common/fingerprint/source-lock.json'
    for row in json.loads(upstream_lock.read_bytes())['files']:
        source=prep.native.member(ROOT/'adaptations','common/fingerprint/upstream/'+row['path'])
        target=confined(stage,'fingerprint/upstream/'+row['path'])
        target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(source,target)
    for source, name in ((upstream_lock,'upstream-source-lock.json'),
                         (ROOT/'adaptations/common/fingerprint/public-bridge-lock.json','public-bridge-lock.json'),
                         (ROOT/'adaptations/common/fingerprint/public-COPYING','public-COPYING'),
                         (ROOT/'adaptations/fingerprint-source-lock.json','bridge-source-lock.json'),
                         (prepared/'sources'/SHARED_PROFILE/'COPYING','COPYING')):
        target=stage/'fingerprint/evidence'/name; shutil.copyfile(source,target)
    package['components'].append(component); package['shared_adaptations']['components'].append(component['id'])
    package['shared_adaptations'].setdefault('warnings',[]).append('Experimental fingerprint unlock requires hardware validation and takes effect after reboot.')
    rows=[]
    for path in sorted(stage.rglob('*')):
        if path.is_file() and path != stage/'manifest.json':
            data=path.read_bytes(); rows.append({'path':path.relative_to(stage).as_posix(),'size':len(data),'sha256':digest(data)})
    package['files']=rows; raw=encoded(package); (stage/'manifest.json').write_bytes(raw); anchor=digest(raw)
    validate_package(stage,expected_manifest_sha256=anchor)
    archive=output/('boiler-'+identity.replace(':','-')+'-'+package_version+'.zip')
    if release:
        release_archive(stage, archive, package, rows)
    else:
        with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED,compresslevel=1) as bundle:
            for name in ['manifest.json']+[r['path'] for r in rows]: bundle.write(stage/name,name)
    imported=import_local(archive,output/'verified-import',developer=True)
    if imported['manifest_sha256'] != anchor: raise ValueError('Reimport differs')
    report={'version':package_version,'manifest_sha256':anchor,'sha256':digest(archive.read_bytes()),
        'directory':str(stage),'path':str(archive),'size':archive.stat().st_size,'hardware_accepted':False}
    if release:
        report.update(directory='package', path=archive.name, source={'repository':source_repository,'commit':source_commit},
            inputs={'base_manifest_sha256':base_sha256,'native_manifest_sha256':native_sha256,
                    'fingerprint_source_lock_sha256':source_sha256},
            generation=['prepare-fingerprint.py','build-fingerprint-overlay.py','protocol.local.import_local'])
    (output/'verification.json').write_bytes(encoded(report)); return report


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('base','base-sha256','runtime','native-sha256','source-sha256','output'): p.add_argument('--'+name,required=True)
    p.add_argument('--release',action='store_true'); p.add_argument('--version'); p.add_argument('--source-commit'); p.add_argument('--source-repository'); p.add_argument('--model-id')
    a=p.parse_args(); print(json.dumps(build(a.base,a.base_sha256,a.runtime,a.native_sha256,a.source_sha256,a.output,model_id=a.model_id,release=a.release,version=a.version,source_commit=a.source_commit,source_repository=a.source_repository),indent=2))
