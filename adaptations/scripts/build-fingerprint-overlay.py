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

SOURCE_ROOT = Path(__file__).resolve().parents[1]


def load_protocol(installer_root):
    global confined, validate_package, import_local
    root = Path(installer_root).resolve(strict=True)
    # Never reuse a protocol module loaded from a different source tree.
    for name, module in tuple(sys.modules.items()):
        if name != 'protocol' and not name.startswith('protocol.'):
            continue
        if (module is None or not getattr(module, '__file__', None)
                or not Path(module.__file__).resolve().is_relative_to(root/'protocol')):
            raise ValueError('Protocol already loaded from another installer root: '+name)
    # The installer is always supplied explicitly; no sibling checkout search.
    sys.path.insert(0, str(root))
    try:
        from protocol.manifest import confined, validate_package
        from protocol.local import import_local
    finally:
        sys.path.pop(0)

spec = importlib.util.spec_from_file_location('fingerprint_prepare', Path(__file__).with_name('prepare-fingerprint.py'))
prep = importlib.util.module_from_spec(spec); spec.loader.exec_module(prep)
VERSION = '0.1.0-localtest.20261104.fingerprint.10'
REPOSITORY = 'FlightDan/Boiler-adaptations'
INSTALLER_REPOSITORY = 'FlightDan/Boiler-installer'
BASE_AUTHORITY_COMMIT = '3edd6a340b56628785f05365d62349f7d03c63eb'
RELEASE_BASES = {'device:oxp3':'3b4c7dce1c6850dd2e3601ef44542676f904a66eeae9d2ce14ad00e3aa9ddbb2',
                 'device:x2-mini':'96ef91e8b7909ad9a345eef1feed1aac90156e6846e9c2f75fc265d006b273f8'}
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


def verify_checkout(root, repository, commit, names):
    root = Path(root).resolve(strict=True)
    if not isinstance(commit, str) or not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('Full source commit required')
    def git(*args, input=None):
        return subprocess.run(['git', '-C', str(root), *args], input=input,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout
    try:
        if git('remote','get-url','origin').decode().strip() not in (
                'https://github.com/'+repository+'.git', 'https://github.com/'+repository):
            raise ValueError('Unexpected repository origin')
        if git('rev-parse','--verify','HEAD^{commit}').decode().strip()!=commit:
            raise ValueError('Selected source commit must equal HEAD')
        entries={}
        for line in git('ls-tree','-r','-z',commit).split(b'\0'):
            if line:
                meta,name=line.split(b'\t',1); mode,kind,blob=meta.split()
                entries[name.decode('utf-8')]=(mode,kind,blob)
        if len(names)!=len(set(names)):
            raise ValueError('Duplicate source inventory path')
        for name in names:
            parts=name.split('/')
            if (not name or any(p in ('','.','..') for p in parts) or '\\' in name or ':' in name
                    or any(ord(c)<32 for c in name)):
                raise ValueError('Invalid source inventory path')
            path=root
            for part in parts:
                path=path/part
                if path.is_symlink() or getattr(path,'is_junction',lambda:False)():
                    raise ValueError('Linked source inventory path')
            if not path.is_file() or entries.get(name,(None,None,None))[:2] not in ((b'100644',b'blob'),(b'100755',b'blob')):
                raise ValueError('Missing committed source: '+name)
        blobs=git('hash-object','--no-filters','--stdin-paths',input=('\n'.join(names)+'\n').encode()).splitlines()
        if len(blobs)!=len(names) or any(blob!=entries[name][2] for name,blob in zip(names,blobs)):
            raise ValueError('Source bytes differ from selected commit')
        return git
    except (OSError,subprocess.CalledProcessError) as exc:
        raise ValueError('Cannot verify source checkout') from exc


def require_installer_engine(installer_root):
    raw=(Path(installer_root)/'protocol/manifest.py').read_text(encoding='utf-8')
    matches=re.findall(r'^ENGINE_VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"$', raw, re.MULTILINE)
    if len(matches)!=1 or tuple(map(int,matches[0].split('.')))<(0,1,5):
        raise ValueError('Installer protocol engine must support fingerprint minimum 0.1.5')


def release_inputs(model_id, base_sha256, native_sha256, version, authority_root, authority_commit, installer_root, installer_commit):
    if model_id not in RELEASE_BASES or base_sha256 != RELEASE_BASES[model_id] or native_sha256 != RELEASE_NATIVE:
        raise ValueError('Release inputs differ from fixed public anchors')
    if not isinstance(version,str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?',version) or 'localtest' in version.lower():
        raise ValueError('Release requires an explicit non-localtest version')
    authority_root=Path(authority_root).resolve(strict=True); installer_root=Path(installer_root).resolve(strict=True)
    inventory=json.loads((authority_root/'model-source-files.json').read_bytes())
    if inventory.get('schema_version')!=1 or inventory.get('kind')!='CanonicalAdaptationSourceFiles':
        raise ValueError('Invalid authority inventory')
    authority_names=[row['path'] for row in inventory['files']]+['model-source-files.json']
    required={'scripts/build-fingerprint-overlay.py','scripts/prepare-fingerprint.py',
              'scripts/validate-fingerprint-native.py','scripts/fingerprint_provenance.py','fingerprint-source-lock.json'}
    if not required.issubset(authority_names): raise ValueError('Authority inventory omits required sources')
    git=verify_checkout(authority_root,REPOSITORY,authority_commit,authority_names)
    try: git('merge-base','--is-ancestor',BASE_AUTHORITY_COMMIT,authority_commit)
    except subprocess.CalledProcessError as exc: raise ValueError('Authority commit must descend from fixed base') from exc
    installer_inventory=json.loads((installer_root/'scripts/source-files.json').read_bytes())
    installer_names=installer_inventory.get('files',[])
    if installer_inventory.get('kind')!='BoilerSourceWhitelist' or not {'scripts/source-files.json','protocol/manifest.py','protocol/local.py'}.issubset(installer_names):
        raise ValueError('Invalid installer whitelist')
    verify_checkout(installer_root,INSTALLER_REPOSITORY,installer_commit,installer_names)
    require_installer_engine(installer_root)
    # The executing authority snapshot must be identical to committed A, even when
    # it is embedded under the installer rather than the authority checkout.
    for name in authority_names:
        source=authority_root/name; snapshot=SOURCE_ROOT/name
        if not snapshot.is_file() or snapshot.is_symlink() or snapshot.read_bytes()!=source.read_bytes():
            raise ValueError('Executing authority snapshot differs: '+name)
    for row in inventory['files']:
        raw=(authority_root/row['path']).read_bytes()
        if len(raw)!=row['size'] or digest(raw)!=row['sha256']:
            raise ValueError('Authority source inventory differs: '+row['path'])


def build(base, base_sha256, runtime, native_sha256, source_sha256, output, model_id=None, *, installer_root, release=False, version=None, source_commit=None, source_repository=None, authority_root=None, installer_commit=None):
    if release:
        if source_repository != REPOSITORY or authority_root is None: raise ValueError('Explicit authority source required')
        release_inputs(model_id, base_sha256, native_sha256, version, authority_root, source_commit, installer_root, installer_commit)
    elif any(v is not None for v in (version, source_commit, source_repository, authority_root, installer_commit)):
        raise ValueError('Release metadata requires explicit release mode')
    load_protocol(installer_root)
    package_version = version if release else VERSION
    base, output = Path(base).resolve(), Path(output).absolute()
    if output.exists(): raise ValueError('Fresh immutable output required')
    package = validate_package(base, expected_manifest_sha256=base_sha256)
    identity = package.get('id')
    if identity not in MODEL_RULES or (model_id is not None and identity != model_id) or any(x['id'] == 'oxp3:fingerprint-unlock' for x in package['components']):
        raise ValueError('Expected matching supported model base without fingerprint component')
    if not isinstance(package.get('shared_adaptations', {}).get('components'), list):
        raise ValueError('Base package lacks static maintenance contract')
    if release:
        legacy=package.get('legacy_capabilities', [])
        wifi=[row for row in legacy if row.get('id')=='oxp3:wifi']
        decky=[row for row in legacy if row.get('id')=='oxp3:decky']
        if (len(wifi)!=1 or wifi[0].get('available') is not False or wifi[0].get('default') is not False
                or len(decky)!=1 or decky[0].get('available') is not True
                or package.get('selection_contract')!={'kind':'oxp3-legacy-options-v1','component':'oxp3:legacy-adaptation'}
                or not any(row['id']=='oxp3:legacy-adaptation' for row in package['components'])):
            raise ValueError('Base model lacks reviewed Decky or BE201 safety contract')
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
    if release:
        (stage/'fingerprint/evidence/base-manifest.json').write_bytes((base/'manifest.json').read_bytes())
        provenance={'schema_version':1,'kind':'BoilerFingerprintBuildProvenance',
            'base_source':{'repository':REPOSITORY,'commit':BASE_AUTHORITY_COMMIT,'manifest_sha256':base_sha256},
            'builder_source':{'repository':source_repository,'commit':source_commit},
            'installer_source':{'repository':INSTALLER_REPOSITORY,'commit':installer_commit},
            'native_manifest_sha256':native_sha256,'fingerprint_source_lock_sha256':source_sha256}
        (stage/'fingerprint/evidence/build-provenance.json').write_bytes(encoded(provenance))
    for row in native_manifest['files']:
        if row['path'].startswith('licenses/'):
            source=confined(prepared/'native',row['path']); target=stage/'fingerprint'/row['path']; target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(source,target)
    upstream_root = SOURCE_ROOT/'common/fingerprint/upstream'
    upstream_lock = SOURCE_ROOT/'common/fingerprint/source-lock.json'
    for row in json.loads(upstream_lock.read_bytes())['files']:
        source=prep.native.member(SOURCE_ROOT,'common/fingerprint/upstream/'+row['path'])
        target=confined(stage,'fingerprint/upstream/'+row['path'])
        target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(source,target)
    for source, name in ((upstream_lock,'upstream-source-lock.json'),
                         (SOURCE_ROOT/'common/fingerprint/public-bridge-lock.json','public-bridge-lock.json'),
                         (SOURCE_ROOT/'common/fingerprint/public-COPYING','public-COPYING'),
                         (SOURCE_ROOT/'fingerprint-source-lock.json','bridge-source-lock.json'),
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
            base_source={'repository':REPOSITORY,'commit':BASE_AUTHORITY_COMMIT},
            installer_source={'repository':INSTALLER_REPOSITORY,'commit':installer_commit},
            inputs={'base_manifest_sha256':base_sha256,'native_manifest_sha256':native_sha256,
                    'fingerprint_source_lock_sha256':source_sha256},
            generation=['prepare-fingerprint.py','build-fingerprint-overlay.py','protocol.local.import_local'])
    (output/'verification.json').write_bytes(encoded(report)); return report


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('base','base-sha256','runtime','native-sha256','source-sha256','output'): p.add_argument('--'+name,required=True)
    p.add_argument('--installer-root',required=True); p.add_argument('--installer-commit'); p.add_argument('--authority-root'); p.add_argument('--release',action='store_true'); p.add_argument('--version'); p.add_argument('--source-commit'); p.add_argument('--source-repository'); p.add_argument('--model-id')
    a=p.parse_args(); print(json.dumps(build(a.base,a.base_sha256,a.runtime,a.native_sha256,a.source_sha256,a.output,model_id=a.model_id,release=a.release,version=a.version,source_commit=a.source_commit,source_repository=a.source_repository,installer_root=a.installer_root,installer_commit=a.installer_commit,authority_root=a.authority_root),indent=2))
