"""Build the X2 HDR-only overlay from an explicitly authenticated OXP3 package.

Other component payloads and the accepted KWin binary runtime are reused.
The desktop bootstrap selects the base luminance policy and retires new trials.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile

BASE_MANIFEST = 'cb39c94bbe7eb351221c380ebdb7a875a097b52c17e90bfb0f1edc22b12ab684'
LEGACY = 'payload/legacy/'

def sha(data): return hashlib.sha256(data).hexdigest()

def encoded(value): return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')

def checked_relative(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise ValueError('Unsafe path')
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ('', '.', '..') for part in value.split('/')):
        raise ValueError('Unsafe path')
    return value

def file_path(root, relative):
    current = root
    for part in checked_relative(relative).split('/'):
        current = current / part
        if current.is_symlink() or getattr(current, 'is_junction', lambda: False)():
            raise ValueError('Linked input')
    if not current.resolve().is_relative_to(root.resolve()): raise ValueError('Escaping input')
    return current

def replace_once(text, old, new):
    if text.count(old) != 1: raise ValueError('Expected unique source anchor: ' + old[:80])
    return text.replace(old, new)

def desktop_launch(raw):
    text = raw.decode('utf-8')
    old = """if /usr/bin/python3 -I -B "$trial" --device-check; then
    export KWIN_X2MINI_HDR_PROFILE=1
    /usr/bin/python3 -I -B "$trial" --install-launcher || /usr/bin/logger -t oxp3-desktop-hdr 'X2mini HDR launcher installation skipped; preserve existing user files'
fi"""
    return replace_once(text, old, '# Use the accepted base KWin 1100/472 mapping.').encode('utf-8')


def desktop_options(raw):
    text = raw.decode('utf-8')
    text = replace_once(text, 'KDE \u684c\u9762 HDR \u6620\u5c04\u4e0e\u6062\u590d\uff08X2mini \u5b89\u5168\u8bd5\u7528\uff09', 'KDE \u684c\u9762 HDR \u6620\u5c04\u4e0e\u6062\u590d')
    text = replace_once(text, 'KDE desktop HDR mapping and recovery (X2mini safe trial)', 'KDE desktop HDR mapping and recovery')
    return text.encode('utf-8')


def desktop_trial(raw):
    text = raw.decode('utf-8')
    return replace_once(text, "    if args.device_check:\n", "    if args.install_launcher or not any((args.device_check, args.uninstall_launcher, args.recover_startup, args.watchdog)):\n        raise RuntimeError('X2 Mini HDR trial is retired; use the standard HDR controls')\n    if args.device_check:\n").encode('utf-8')


def desktop_device(raw):
    text = raw.decode('utf-8')
    text = replace_once(text, "_ACCEPTED_PRODUCTS = frozenset(('ONEXPLAYERX2MINI', 'ONEXPLAYERX2MINIPRO'))", "_ACCEPTED_PRODUCTS = frozenset(('ONEXPLAYERX2MINI',))")
    return text.encode('utf-8')


def desktop_device_tests(raw):
    text = raw.decode('utf-8')
    text = replace_once(text, "'product_name': 'ONEXPLAYER X2 Mini PRO',", "'product_name': 'ONEXPLAYER X2 Mini',")
    text = replace_once(text, "        for fields in (\n", "        for fields in (\n                {'sys_vendor': 'ONE-NETBOOK', 'product_name': 'ONEXPLAYER X2 MINI PRO'},\n")
    return text.encode('utf-8')

def replace_tar_member(raw, expected_old, new):
    source = tarfile.open(fileobj=io.BytesIO(raw), mode='r:')
    output = io.BytesIO()
    found = 0
    with source, tarfile.open(fileobj=output, mode='w:', format=tarfile.PAX_FORMAT) as destination:
        for info in source.getmembers():
            info = copy.copy(info)
            member = source.extractfile(info) if info.isfile() else None
            data = member.read() if member is not None else None
            if info.name.removeprefix('./') == 'bin/oxp3-kwin-launch':
                if data != expected_old: raise ValueError('Runtime launcher differs from canonical source')
                data = new; info.size = len(new); found += 1
            destination.addfile(info, io.BytesIO(data) if data is not None else None)
    if found != 1: raise ValueError('Expected one accepted runtime launcher')
    return output.getvalue()

def build(base, output):
    base, output = Path(base).absolute(), Path(output).absolute()
    if base.is_symlink() or output.exists() or output.resolve().is_relative_to(base.resolve()):
        raise ValueError('Use a fresh output outside the authenticated package')
    raw_manifest = file_path(base, 'manifest.json').read_bytes()
    if sha(raw_manifest) != BASE_MANIFEST: raise ValueError('Explicit accepted OXP3 .8 package required')
    manifest = json.loads(raw_manifest)
    records = {record['path']: record for record in manifest['files']}
    originals, changes = {}, {}
    def read(name):
        relative = LEGACY + name
        record = records.get(relative)
        if record is None: raise ValueError('Not in authenticated package: ' + relative)
        data = file_path(base, relative).read_bytes()
        if len(data) != record['size'] or sha(data) != record['sha256']: raise ValueError('Changed package member: ' + relative)
        originals[relative] = data
        return data
    def transform(name, fn):
        data = read(name); replacement = fn(data)
        if data == replacement: raise ValueError('Expected actual HDR change: ' + name)
        changes[LEGACY+name] = replacement

    def profile(raw):
        doc = json.loads(raw)
        if doc['mapping'] != dict(eotf='gamma22', peak=1100, full_frame=472, optical_measurement=False):
            raise ValueError('Unexpected original HDR mapping profile')
        doc['version'] = '20261108-x2-hdr-1100-472-v1'
        doc['device']['product'] = 'ONEXPLAYER X2 Mini'
        doc['acceptance'] = '1100/472 software mapping; physical display acceptance is separate.'
        return encoded(doc)
    transform('components/hdr/profile.json', profile)
    transform('components/options.json', desktop_options)
    transform('components/desktop-hdr/hdr_trial.py', desktop_trial)
    transform('components/desktop-hdr/launch.sh', desktop_launch)
    transform('integration/desktop-hdr/source/launch-stable.sh', desktop_launch)
    transform('components/desktop-hdr/test_x2mini_device.py', desktop_device_tests)
    for name in ('components/desktop-hdr/x2mini_device.py','components/x2mini_hdr_device.py','maintenance/app/oxp3_maintenance/x2mini_hdr_device.py'):
        transform(name, desktop_device)
    integration = json.loads(read('integration/manifest.json'))['desktop-hdr']
    target_changes = {
        'home/deck/.local/lib/oxp3-desktop-hdr-runtime/hdr_trial.py': changes[LEGACY+'components/desktop-hdr/hdr_trial.py'],
        'home/deck/.local/lib/oxp3-desktop-hdr-runtime/launch.sh': changes[LEGACY+'components/desktop-hdr/launch.sh'],
        'home/deck/.local/lib/oxp3-desktop-hdr-runtime/x2mini_device.py': changes[LEGACY+'components/desktop-hdr/x2mini_device.py'],
    }
    for target, replacement in target_changes.items():
        entries = [entry for entry in integration['files'] if entry['target'] == target]
        if len(entries) != 1: raise ValueError('Ambiguous desktop integration target')
        name = 'integration/'+entries[0]['source']; old = read(name)
        canonical = {'launch.sh':'components/desktop-hdr/launch.sh','hdr_trial.py':'components/desktop-hdr/hdr_trial.py','x2mini_device.py':'components/desktop-hdr/x2mini_device.py'}[target.rsplit('/',1)[1]]
        if old != originals[LEGACY+canonical]: raise ValueError('Unsynchronized accepted desktop input')
        changes[LEGACY+name] = replacement
    tar_name = 'integration/'+integration['runtime_archive']
    changes[LEGACY+tar_name] = replace_tar_member(read(tar_name), originals[LEGACY+'components/desktop-hdr/launch.sh'], changes[LEGACY+'components/desktop-hdr/launch.sh'])
    # Keep all KWin source and binary policy files untouched. The accepted runtime
    # already implements both profiles; startup selects the base policy.
    policy = read('integration/desktop-hdr/source/oxp3_gamma22_policy.h')
    if b'Oxp3Gamma22Luminance{475, 700}' not in policy: raise ValueError('Accepted runtime lacks X2 luminance policy')
    output.mkdir(parents=True)
    entries = []
    for index, (path, data) in enumerate(sorted(changes.items())):
        relative = f'files/{index:03d}.bin'; dest = output/relative; dest.parent.mkdir(exist_ok=True)
        dest.write_bytes(data)
        entries.append(dict(path=path, source=relative, size=len(data), sha256=sha(data), base_sha256=sha(originals[path])))
    lock = dict(schema_version=1, kind='BoilerX2CompleteOverlay', role='hdr', base_manifest_sha256=BASE_MANIFEST,
        files=entries, mapping=dict(game=dict(peak=1100, full_frame=472), desktop=dict(peak=1100, full_frame=472)),
        provenance=dict(repository='FlightDan/OneXPlayer3-SteamOS', base_source=manifest['source'],
            public_cpu_baseline='72f864e0498c10366493c74608c5ffed62cc99ca',
            note='Explicit pinned retained OXP3 .8 package, with its own recorded source provenance. HDR profile/bootstrap transform; no CPU source is read. Base native runtime policy is selected.'),
        licenses=['Original AGPL-3.0 project notices retained by complete package','HDR upstream MIT UPSTREAM_LICENSE retained','KWin GPL-2.0-or-later source and notices retained'],
        aggregation=['Regenerate legacy maintenance/manifest.json from final app bytes after all overlays.',
                     'Regenerate outer DevicePackage inventory and archive from final bytes.'],
        acceptance=dict(source_build=True, physical_display=False, native_binary_recompiled=False))
    (output/'overlay-lock.json').write_bytes(encoded(lock))
    return lock

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); result = build(args.base, args.output)
    print(json.dumps(dict(files=len(result['files']), role='hdr', mapping=result['mapping'])))
