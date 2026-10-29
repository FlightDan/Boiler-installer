"""Build only Boiler's generic payloads; device models are separate packages.

Firmware is a metadata-only, pinned download catalogue by default. Full local
firmware inputs require an explicit development-only switch and are never
permitted in a production installer package. Existing output is never merged.
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
from graphics_catalog import metadata as graphics_metadata
from graphics_catalog import firmware_metadata as graphics_firmware_metadata
from maintenance import runtime_files


def firmware_metadata(root=ROOT):
    """Map reviewed locks to their eventual task-private payload paths.

    This function never reads firmware bytes, prepared bundles, or cache paths.
    Preserve every source-evidence field for host download and guest rechecks.
    """
    components = {}
    for transport in ('wifi', 'bluetooth'):
        for vendor in ('intel', 'realtek', 'mediatek'):
            prefix = 'bluetooth/' if transport == 'bluetooth' else ''
            lock = json.loads((Path(root) / ('components/locks/' + prefix + vendor + '.json')).read_text())
            base = 'wireless/' + prefix + vendor + '/'
            files = [dict(entry, source=base + 'firmware/' + entry['target'].removeprefix('usr/lib/firmware/'))
                     for entry in lock['files']]
            licenses = [dict(entry, source=base + entry['target']) for entry in lock['licenses']]
            components[transport + ':' + vendor] = dict(lock, files=files, licenses=licenses,
                available=True, metadata_available=True, payload_cached=False, delivery='on-demand')
    return components


def local_test_model_assets():
    """Only an explicit local-test build may persist its verified model bundle."""
    from protocol.model_catalog import bundled_packages, bundled_model_package
    descriptor = ROOT/'cache/model-bundles/manifest.json'
    if not descriptor.is_file():
        raise ValueError('Explicit local-test model descriptor is missing')
    files = {'cache/model-bundles/manifest.json': descriptor.read_bytes()}
    for item in bundled_packages(ROOT):
        package = bundled_model_package(item['id'], trust_local_test=True, root=ROOT)
        prefix = Path(item['directory']).relative_to(ROOT).as_posix()
        files[prefix+'/manifest.json'] = (ROOT/prefix/'manifest.json').read_bytes()
        for entry in package['package']['files']:
            files[prefix+'/'+entry['path']] = checked(ROOT/prefix, dict(entry, source=entry['path']))
    actual = {'cache/model-bundles/'+path.relative_to(descriptor.parent).as_posix()
              for path in descriptor.parent.rglob('*') if path.is_file()}
    if actual != set(files):
        raise ValueError('Unlisted local model files cannot enter the maintenance runtime')
    return files


def prepare(output, decky=None, wireless=(), development_firmware_cache=False, local_test_models=False):
    output = Path(output)
    if output.exists(): raise FileExistsError('Output exists; use a fresh preparation directory')
    if wireless and not development_firmware_cache:
        raise ValueError('Explicit firmware inputs require --development-firmware-cache')
    graphics = graphics_metadata(ROOT)
    components = {'core:decky': {'available': False, 'reason': 'Explicit locked Decky input required'}}
    components.update(firmware_metadata())
    components.update(graphics_firmware_metadata(ROOT))
    staged = {}
    if decky:
        base = Path(decky)
        item = json.loads((base / 'manifest.json').read_text())
        if item.get('id') != 'core:decky' or item.get('available') is not True:
            raise ValueError('Decky input was not explicitly prepared')
        for entry in item['files']: staged[entry['source']] = checked(base, entry)
        for entry in item.get('notices', []): staged[entry['source']] = checked(base, entry)
        components['core:decky'] = item
    prepared_identities = set()
    for prepared in wireless:
        base = Path(prepared)
        bundle = json.loads((base / 'manifest.json').read_text())
        vendor = bundle.get('vendor')
        transport = bundle.get('transport', 'wifi')
        validate_bundle(bundle, base, vendor, transport=transport)
        identity = transport + ':' + vendor
        if identity in prepared_identities: raise ValueError('Duplicate prepared firmware vendor')
        prepared_identities.add(identity)
        files = []
        for entry in bundle['files']:
            data = checked(base, entry)
            name = 'wireless/' + ('bluetooth/' if transport == 'bluetooth' else '') + vendor + '/' + entry['source']
            staged[name] = data
            evidence = {key: entry[key] for key in ('git_blob', 'url', 'alias_of', 'alias_evidence', 'whence') if key in entry}
            files.append(record(name, data, target=entry['target'], **evidence))
        licenses = []
        for entry in bundle.get('licenses', []):
            data = checked(base, entry); name = 'wireless/' + ('bluetooth/' if transport == 'bluetooth' else '') + vendor + '/' + entry['source']
            staged[name] = data; licenses.append(record(name, data, target=entry['target']))
        if not licenses: raise ValueError('Firmware licenses missing')
        components[identity] = dict(bundle, available=True, files=files, licenses=licenses,
                                   metadata_available=False, payload_cached=True, delivery='development-cache')
    runtime = []
    for source in runtime_files():
        if not source.is_file() or source.is_symlink(): raise FileNotFoundError('Maintenance runtime missing')
        relative = source.relative_to(ROOT).as_posix()
        data = source.read_bytes(); name = 'maintenance-runtime/' + relative
        staged[name] = data
        runtime.append(record(name, data, target='usr/local/lib/boiler/' + relative, mode=0o644))
    launcher = (b'#!/bin/sh\nset -eu\ncd /usr/local/lib/boiler\n'
                b'exec /usr/bin/python3 -I -B -c \'import sys; sys.path.insert(0, "/usr/local/lib/boiler"); '
                b'from maintenance.__main__ import main; raise SystemExit(main())\' "$@"\n')
    staged['maintenance-runtime/boiler-maintenance'] = launcher
    runtime.append(record('maintenance-runtime/boiler-maintenance', launcher, target='usr/local/bin/boiler-maintenance', mode=0o755))
    ui_launcher = (b'#!/bin/sh\nset -eu\ncd /usr/local/lib/boiler\n'
                   b'exec /usr/bin/python3 -I -B -c \'import sys; sys.path.insert(0, "/usr/local/lib/boiler"); '
                   b'from maintenance.model_ui import main; raise SystemExit(main())\' "$@"\n')
    desktop = ('[Desktop Entry]\nType=Application\nName=Boiler Maintenance\nName[zh_CN]=Boiler 维护工具\n'
               'Comment=Review, install and restore device adaptations\n'
               'Exec=/usr/local/bin/boiler-maintenance-ui\nIcon=preferences-system\nTerminal=false\nCategories=System;Settings;\n').encode('utf-8')
    for name, data, target, mode in (
        ('boiler-maintenance-ui', ui_launcher, 'usr/local/bin/boiler-maintenance-ui', 0o755),
        ('Boiler-Maintenance.desktop', desktop, 'usr/share/applications/Boiler-Maintenance.desktop', 0o644)):
        source = 'maintenance-runtime/' + name
        staged[source] = data; runtime.append(record(source, data, target=target, mode=mode))
    if local_test_models:
        for relative, data in local_test_model_assets().items():
            name = 'maintenance-runtime/' + relative
            staged[name] = data
            runtime.append(record(name, data, target='usr/local/lib/boiler/' + relative, mode=0o644))
    maintenance_release = json.loads((ROOT / 'maintenance/release.json').read_text(encoding='utf-8'))
    component = dict(schema_version=1, kind='ComponentManifest', id='core:maintenance', version=maintenance_release['version'], files=runtime)
    components['core:maintenance'] = dict(available=True, files=runtime, component=component)
    output.mkdir(parents=True)
    for name, data in sorted(staged.items()):
        path = output / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    manifest = dict(schema=1, product='Boiler Installer', components=components,
                    graphics_candidates=graphics)
    if local_test_models:
        manifest['local_test_models'] = True
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--decky', type=Path)
    parser.add_argument('--wireless', type=Path, action='append', default=[])
    parser.add_argument('--development-firmware-cache', action='store_true',
                        help='Development fixtures only: include explicitly prepared firmware bytes')
    parser.add_argument('--local-test-models', action='store_true', help='Persist explicit verified local test model bundles for on-device maintenance')
    args = parser.parse_args()
    result = prepare(args.output, args.decky, args.wireless, args.development_firmware_cache, args.local_test_models)
    print(json.dumps(dict(output=str(args.output), components={key: dict(available=value.get('available', False), files=len(value.get('files', []))) for key,value in result['components'].items()})))
