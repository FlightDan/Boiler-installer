"""Overlay reviewed OXP3 repairs on the exact retained local model.

The historical public source snapshot and its CPU/HDR bytes are immutable.
This builder reads only an anchored full base and explicit repair inventories.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from protocol.manifest import confined, validate_package
from protocol.local import import_local

BASE_ANCHOR = 'a56006b425522d1c753254d3e0b9d11f7e79aea21c221a4a481830508c410393'
VERSION = '0.1.0-localtest.20261101.10'
REPAIRS = 'brands/onexplayer/oxp3/current/upstream-20261029'
XEVMA = 'brands/onexplayer/oxp3/current/xevma-20261029'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode('utf-8')


def record(path, data):
    return dict(path=path, size=len(data), sha256=digest(data))


def refresh_maintenance_manifest(directory):
    """Inventory final generated maintenance bytes before the outer package."""
    name = 'payload/legacy/maintenance/manifest.json'
    path = Path(directory)/name
    manifest = json.loads(path.read_bytes())
    if manifest.get('schema') != 1 or not isinstance(manifest.get('files'), dict):
        raise ValueError('Invalid retained maintenance manifest')
    app = path.parent/'app'
    actual = {file.relative_to(app).as_posix(): digest(file.read_bytes())
              for file in sorted(app.rglob('*')) if file.is_file()}
    if actual.keys() != manifest['files'].keys():
        raise ValueError('Maintenance application member inventory changed')
    manifest['files'] = actual
    path.write_bytes(encoded(manifest))
    return name


def patched_input_helper(data, dock_source):
    """Apply connector support to generated runtime profiles, preserving /etc."""
    data = data.replace(b'\r\n', b'\n')
    marker = b"RUNTIME = Path('/run/oxp3-input')"
    before = b"    return text.replace(blocks[0].replace('\\n', newline), replacement.replace('\\n', newline), 1)"
    after = (b"    rendered = text.replace(blocks[0].replace('\\n', newline), replacement.replace('\\n', newline), 1)\n"
             b"    try:\n        return add_connector_keyboard(rendered.encode('utf-8')).decode('utf-8')\n"
             b"    except (ValueError, UnicodeError):\n        return None")
    if data.count(marker) != 1 or data.count(before) != 1:
        raise ValueError('Unexpected pinned input profile helper')
    return data.replace(marker, dock_source+b'\n\n'+marker).replace(before, after)


def sources(root=ROOT):
    folder = root/'adaptations'
    lock = json.loads((folder/'upstream-source-lock.json').read_bytes())
    if lock.get('kind') != 'BoilerUpstreamRepairSources' or lock.get('schema_version') != 1:
        raise ValueError('Explicit reviewed repair source inventory required')
    seen = set()
    for row in lock['files']:
        if row['path'] in seen:
            raise ValueError('Duplicate source inventory entry')
        seen.add(row['path'])
        data = confined(folder, row['path']).read_bytes()
        if len(data) != row['size'] or digest(data) != row['sha256']:
            raise ValueError('Reviewed repair source changed: '+row['path'])
    actual = {path.relative_to(folder).as_posix() for part in (REPAIRS, XEVMA)
              for path in (folder/part).iterdir() if path.is_file() and path.suffix != '.pyc'}
    if actual != seen:
        raise ValueError('Repair sources differ from the explicit reviewed inventory')
    return lock


def build(base, prepared_decky, output, xevma_input, root=ROOT):
    base, prepared_decky, output = map(lambda p: Path(p).resolve(), (base, prepared_decky, output))
    if output.exists():
        raise ValueError('Use a fresh immutable build directory')
    baseline = validate_package(base, expected_manifest_sha256=BASE_ANCHOR)
    if baseline['id'] != 'device:oxp3' or baseline['version'] != '0.1.0-localtest.20261030.4':
        raise ValueError('Unexpected retained OXP3 base')
    source_lock = sources(root)
    authored = root/'adaptations'/REPAIRS
    spec = importlib.util.spec_from_file_location('boiler_dock_transform', authored/'dock_input.py')
    transform = importlib.util.module_from_spec(spec); spec.loader.exec_module(transform)
    decky_lock = json.loads((root/'components/locks/decky.json').read_bytes())
    decky = json.loads((prepared_decky/'manifest.json').read_bytes())
    if decky.get('version') != '3.3.1' or decky_lock['version'] != '3.3.1':
        raise ValueError('Decky 3.3.1 prepared input required')
    for row in decky['files']+decky['notices']:
        data = confined(prepared_decky, row['source']).read_bytes()
        if len(data) != row['size'] or digest(data) != row['sha256']:
            raise ValueError('Prepared Decky input changed')
    binary = (prepared_decky/'decky/PluginLoader').read_bytes()
    if len(binary) != decky_lock['file']['size'] or digest(binary) != decky_lock['file']['sha256']:
        raise ValueError('Decky binary differs from source lock')
    directory = output/'package'; directory.mkdir(parents=True)
    for row in baseline['files']:
        target = confined(directory, row['path']); target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(confined(base, row['path']), target)
    manifest = json.loads(encoded(baseline))
    manifest['version'] = VERSION
    manifest['components'][0]['version'] = VERSION
    changed = []
    # The immutable base predates directory-runtime verification size support.
    # Ship the reviewed bridge source, then inventory its final bytes normally.
    (directory/'adapter.py').write_bytes((root/'adaptations/brands/onexplayer/oxp3/bridge/adapter.py').read_bytes())
    changed.append('adapter.py')
    # Both templates feed the original managed input owner, including gyro.
    integration = directory/'payload/legacy/integration'
    resources = json.loads((integration/'manifest.json').read_bytes())
    for feature in ('input', 'gyro'):
        for row in resources[feature]['files']:
            if row['target'].startswith('etc/inputplumber/devices.d/') and row['target'].endswith('.yaml'):
                path = confined(integration, row['source'])
                original = path.read_bytes(); updated = transform.add_connector_keyboard(original)
                path.write_bytes(updated); changed.append(path.relative_to(directory).as_posix())
    # Maintenance preserves existing /etc calibration/templates. Its helper,
    # like the installation copy, must add the connector to /run at boot too.
    for name in ('payload/legacy/integration/input/003.bin',
                 'payload/legacy/components/input_profile.py',
                 'payload/legacy/maintenance/app/oxp3_maintenance/input_profile.py'):
        path = directory/name
        path.write_bytes(patched_input_helper(path.read_bytes(), (authored/'dock_input.py').read_bytes()))
        changed.append(name)
    # Preserve the managed provider and journal semantics, refresh its payload
    # and explicit version marker in both installation and maintenance copies.
    plugin_dir = directory/'payload/legacy/plugins/decky'
    (plugin_dir/'PluginLoader').write_bytes(binary)
    changed.append((plugin_dir/'PluginLoader').relative_to(directory).as_posix())
    for row in decky_lock['notices']:
        source = prepared_decky/'sources'/row['source']
        data = source.read_bytes()
        if len(data) != row['size'] or digest(data) != row['sha256']:
            raise ValueError('Decky source archive differs from reviewed lock')
        (plugin_dir/row['source']).write_bytes(data)
    for name in ('payload/legacy/components/plugin_installer.py',):
        path = directory/name
        data = path.read_bytes()
        before, after = b"b'3.2.10\\n'", b"b'3.3.1\\n'"
        if data.count(before) != 1:
            raise ValueError('Unexpected managed Decky version marker: '+name)
        path.write_bytes(data.replace(before, after)); changed.append(name)
    name = 'payload/legacy/maintenance/app/oxp3_maintenance/components.py'
    path = directory/name
    data = path.read_bytes()
    before = b"'var/lib/oxp3-installer/decky/runtime/services/PluginLoader': _entry(binary, 0o755),"
    after = before + b"\n              'var/lib/oxp3-installer/decky/runtime/services/VERSION': _entry(b'3.3.1\\n'),"
    if data.count(before) != 1:
        raise ValueError('Unexpected managed maintenance Decky provider')
    path.write_bytes(data.replace(before, after)); changed.append(name)
    name = 'payload/legacy/components/catalog.json'
    path = directory/name; catalog = json.loads(path.read_bytes())
    entries = [row for row in catalog['plugins'] if row['id'] == 'decky']
    if len(entries) != 1 or entries[0]['version'] != '3.2.10':
        raise ValueError('Unexpected retained Decky catalog entry')
    entries[0]['version'] = '3.3.1'
    path.write_bytes(encoded(catalog)); changed.append(name)
    name = 'payload/legacy/licenses/runtime-inventory.json'
    path = directory/name; notices = json.loads(path.read_bytes())
    entries = [row for row in notices['runtime'] if row['name'] == 'Decky Loader']
    if len(entries) != 1 or entries[0]['version'] != '3.2.10':
        raise ValueError('Unexpected retained Decky license record')
    entries[0].update(version='3.3.1', source=decky_lock['url'], license=decky_lock['license'],
        sources='plugins/decky/source-v3.3.1.zip')
    entries[0].pop('pending', None)
    for row in notices['files']:
        if row.get('file') == 'plugins/decky/PluginLoader':
            row.update(bytes=len(binary), sha256=digest(binary))
    path.write_bytes(encoded(notices)); changed.append(name)
    for cap in manifest.get('legacy_capabilities', []):
        if cap['id'] == 'oxp3:decky':
            cap['payload_version'] = '3.3.1'
        if cap['id'] in ('oxp3:input', 'oxp3:gyro'):
            cap['notes'] = {'en': 'Includes connector keyboard interface 1a86:fe02; physical dock acceptance pending.',
                            'zh': '包含连接底座 1a86:fe02 键盘接口；实体底座验收待完成。'}
    install = json.loads((authored/'install.json').read_bytes())
    # New static files have one Engine owner, independent from legacy audio.
    component = dict(schema_version=1, kind='ComponentManifest', id=install['id'], version=install['version'],
        name=install['name'], description=install['description'], default=False, optional=True,
        requires=[], conflicts=[], files=[], compatibility=dict(architectures=['x86_64'], os_ids=['steamos'], os_versions=['3.10']))
    for row in install['files']:
        relative = REPAIRS+'/'+row['source']
        if relative not in {item['path'] for item in source_lock['files']}:
            raise ValueError('Uninventoried speaker input')
        data = confined(authored, row['source']).read_bytes()
        source = 'upstream-repairs/speaker/'+row['source']
        path = confined(directory, source); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        component['files'].append(dict(source=source, target=row['target'], size=len(data), sha256=digest(data), mode=row.get('mode', 0o644)))
    manifest['components'].append(component)
    manifest['shared_adaptations']['components'].append(component['id'])
    native = root/'adaptations'/XEVMA
    pin = json.loads((native/'module-lock.json').read_bytes())
    native_install = json.loads((native/'install.json').read_bytes())
    if (pin['kernel'] != '7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9'
            or native_install['id'] != 'oxp3:xevmafix'):
        raise ValueError('Unexpected xevma exact-kernel source')
    component = dict(schema_version=1, kind='ComponentManifest', id=native_install['id'],
        version=native_install['version'], name=native_install['name'], description=native_install['description'],
        default=False, optional=True, requires=[], conflicts=[], files=[],
        compatibility=dict(architectures=['x86_64'], os_ids=['steamos'], os_versions=['3.10'], kernels=[pin['kernel']]))
    module = confined(Path(xevma_input), pin['module']['file']).read_bytes()
    if (len(module) != pin['module']['size'] or digest(module) != pin['module']['sha256']
            or module[:6] != b'\x7fELF\x02\x01'
            or int.from_bytes(module[18:20], 'little') != 62
            or ('vermagic='+pin['module']['vermagic']).encode()+b'\0' not in module):
        raise ValueError('xevma module differs from reviewed exact ABI input')
    for row in native_install['files']:
        if row['source'] == pin['module']['file']:
            data = module
        else:
            if XEVMA+'/'+row['source'] not in {item['path'] for item in source_lock['files']}:
                raise ValueError('Uninventoried xevma source')
            data = confined(native, row['source']).read_bytes()
        source = 'upstream-repairs/xevmafix/'+row['source']
        path = confined(directory, source); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        component['files'].append(dict(source=source, target=row['target'], size=len(data),
            sha256=digest(data), mode=row.get('mode', 0o644)))
    manifest['components'].append(component)
    manifest['shared_adaptations']['components'].append(component['id'])
    manifest['shared_adaptations']['warnings'].append('OXP3 speaker and connector fixes require physical-device acceptance; no sleep is initiated by installation.')
    changed.append(refresh_maintenance_manifest(directory))
    evidence = dict(kind='Oxp3UpstreamRepairs', upstream=source_lock['upstream'], base_manifest_sha256=BASE_ANCHOR,
        changed_base_files=changed, source_lock_sha256=digest((root/'adaptations/upstream-source-lock.json').read_bytes()),
        decky_version='3.3.1', xevmafix=dict(kernel=pin['kernel'], module=pin['module'],
            hardware_accepted=False, module_loaded=False), hdr_changed=False, cpu_changed=False)
    (directory/'upstream-repairs.json').write_bytes(encoded(evidence))
    (directory/'upstream-repairs-source-lock.json').write_bytes(encoded(source_lock))
    (directory/'UPSTREAM_REPAIRS_NOTICES.md').write_text(
        '# OXP3 upstream repair notices\n\n'
        'Speaker/connector adaptations derive from HHHHanasak1/onexplayer3-steamos-setup '
        'commit e72e814da1944da3792076689995b6fe961f5768 (MIT). '
        'The complete notice is in upstream-repairs/speaker/UPSTREAM_LICENSE.\n\n'
        'xevmafix declares GPL in the upstream source. Its complete GPL-2.0 license, '
        'corresponding source, Makefile, instructions and exact build input lock are in '
        'upstream-repairs/xevmafix/. No permission to relicense the module is implied by '
        'the enclosing upstream repository MIT license.\n\n'
        'Decky Loader 3.3.1 retains GPL-2.0 and its corresponding official source archive '
        'under payload/legacy/plugins/decky/source-v3.3.1.zip.\n', encoding='utf-8')
    manifest['files'] = [record(path.relative_to(directory).as_posix(), path.read_bytes())
                         for path in sorted(directory.rglob('*')) if path.is_file() and path.name != 'manifest.json']
    # Nested integration manifests are regular inventory members too.
    manifest['files'] += [record(path.relative_to(directory).as_posix(), path.read_bytes())
                          for path in sorted(directory.rglob('manifest.json')) if path != directory/'manifest.json']
    manifest['files'].sort(key=lambda row: row['path'])
    (directory/'manifest.json').write_bytes(encoded(manifest))
    anchor = digest((directory/'manifest.json').read_bytes())
    validate_package(directory, expected_manifest_sha256=anchor)
    # All unrelated base bytes, including CPU/HDR/Opti/Battery/mic, stay exact.
    for row in baseline['files']:
        if row['path'] not in changed and digest((directory/row['path']).read_bytes()) != row['sha256']:
            raise ValueError('Unrelated base file changed: '+row['path'])
    public = json.loads((root/'adaptations/source-lock.json').read_bytes())
    cpu = [row for row in public['files'] if '/controls/cpu/' in row['path']]
    if public['source']['commit'] != '72f864e0498c10366493c74608c5ffed62cc99ca' or len(cpu) != 5:
        raise ValueError('Public CPU baseline identity differs')
    for row in cpu:
        path = directory/'payload/legacy/components/controls/cpu'/Path(row['path']).name
        if digest(path.read_bytes()) != row['sha256']:
            raise ValueError('Model CPU bytes differ from the immutable public baseline')
    archive = output/('boiler-device-oxp3-'+VERSION+'.zip')
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=1) as bundle:
        for name in ['manifest.json']+[row['path'] for row in manifest['files']]:
            bundle.write(directory/name, name)
    imported = import_local(archive, output/'verified-import', developer=True)
    if imported['manifest_sha256'] != anchor:
        raise ValueError('ZIP import differs')
    report = dict(evidence, version=VERSION, manifest_sha256=anchor, files=len(manifest['files']),
        directory=str(directory), path=str(archive), size=archive.stat().st_size, sha256=digest(archive.read_bytes()),
        safe_protocol_import=True, unchanged_base_files_verified=len(baseline['files'])-len(changed))
    (output/'verification.json').write_bytes(encoded(report))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--decky', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--xevma', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.decky, args.output, args.xevma), ensure_ascii=False, indent=2))
