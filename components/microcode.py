"""Pinned, generic CPU microcode catalogue and image-bound planning."""
import json
import hashlib
from pathlib import Path
import re

from payload_lock import relative
from wireless import existing_firmware, native_matches, validate_record_metadata

IDS = {'microcode:intel', 'microcode:amd'}


def selected(options):
    repairs = options.get('repairs', [])
    if not isinstance(repairs, list) or any(not isinstance(value, str) for value in repairs):
        raise ValueError('Repairs must be a list of component identities')
    values = [value for value in repairs if value.startswith('microcode:')]
    if len(values) > 1 or any(value not in IDS for value in values):
        raise ValueError('Choose at most one known CPU microcode vendor')
    return values[0] if values else None


def validate_metadata(bundle, identity):
    vendor = identity.removeprefix('microcode:')
    if (identity not in IDS or not isinstance(bundle, dict) or bundle.get('schema') != 1
            or bundle.get('kind') != 'CpuMicrocodeBundle' or bundle.get('id') != identity
            or bundle.get('vendor') != vendor or not isinstance(bundle.get('version'), str)
            or not bundle['version'] or bundle.get('available') is not True
            or bundle.get('delivery') != 'on-demand' or bundle.get('runtime') != 'not_validated'):
        raise ValueError('Invalid CPU microcode bundle: ' + identity)
    provenance = bundle.get('provenance', {})
    if (not isinstance(provenance, dict) or not re.fullmatch(r'[0-9a-f]{40}', str(provenance.get('revision', '')))
            or not isinstance(provenance.get('url'), str) or not provenance['url'].startswith('https://')
            or not isinstance(provenance.get('repository'), str) or not provenance['repository']):
        raise ValueError('Invalid CPU microcode provenance')
    files, licenses = bundle.get('files'), bundle.get('licenses')
    if not isinstance(files, list) or not files or len(files) > 4096 or not isinstance(licenses, list) or not licenses:
        raise ValueError('CPU microcode firmware and licenses required')
    result = {}
    for item in files + licenses:
        validate_record_metadata(item)
        original = relative(item.get('original_source')).as_posix()
        if item['source'] != 'microcode/' + vendor + '/' + original:
            raise ValueError('CPU source namespace differs from upstream path')
        if not isinstance(item.get('url'), str) or not item['url'].startswith('https://'):
            raise ValueError('CPU source URL required')
        if not re.fullmatch(r'[0-9a-f]{40}', str(item.get('git_blob', ''))):
            raise ValueError('CPU source Git blob required')
        if item in files:
            prefix = 'intel-ucode/' if vendor == 'intel' else 'amd-ucode/'
            if not original.startswith(prefix) or item['target'] != 'usr/lib/firmware/' + original:
                raise ValueError('Invalid CPU firmware destination')
            if item['target'] in result: raise ValueError('Duplicate CPU firmware destination')
            result[item['target']] = item
        elif not item['target'].startswith('licenses/'):
            raise ValueError('Invalid CPU license destination')
    return result


def load_catalog(root):
    path = Path(root) / 'components/microcode-firmware.json'
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise ValueError('CPU microcode catalogue cannot be redirected')
    catalog = json.loads(path.read_text(encoding='utf-8'))
    spec_path = Path(root) / 'components/microcode-sources.json'
    if spec_path.is_symlink() or (hasattr(spec_path, 'is_junction') and spec_path.is_junction()):
        raise ValueError('CPU microcode source specification cannot be redirected')
    spec_bytes = spec_path.read_bytes()
    spec = json.loads(spec_bytes)
    if (spec.get('schema') != 1 or spec.get('kind') != 'CpuMicrocodeSourceSpec'
            or spec.get('version') != '0.1.0' or set(spec.get('sources', {})) != {'intel', 'amd'}
            or catalog.get('spec_sha256') != hashlib.sha256(spec_bytes).hexdigest()):
        raise ValueError('CPU microcode source specification changed; regenerate catalogue')
    if (not isinstance(catalog, dict) or catalog.get('schema') != 1
            or catalog.get('kind') != 'CpuMicrocodeCatalogue' or catalog.get('version') != '0.1.0'
            or not isinstance(catalog.get('components'), dict) or set(catalog['components']) != IDS):
        raise ValueError('Invalid CPU microcode catalogue')
    for identity, bundle in catalog['components'].items():
        validate_metadata(bundle, identity)
        vendor = identity.split(':')[1]
        source = spec['sources'][vendor]
        origin = bundle['provenance']
        revision = source.get('revision', '')
        if not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{40}', revision):
            raise ValueError('Invalid CPU source revision')
        url = ('https://raw.githubusercontent.com/intel/Intel-Linux-Processor-Microcode-Data-Files/' + revision + '/'
               if vendor == 'intel' else 'https://kernel.googlesource.com/pub/scm/linux/kernel/git/firmware/linux-firmware/+/' + revision + '/')
        if (origin.get('repository') != source.get('repository') or origin.get('revision') != revision
                or origin.get('tag') != source.get('tag') or origin.get('url') != url
                or bundle['version'] != source.get('version')
                or sorted(row['original_source'] for row in bundle['licenses']) != sorted(source.get('licenses', []))):
            raise ValueError('CPU catalogue does not match its source specification')
        for row in bundle['files'] + bundle['licenses']:
            if row['url'] != url + row['original_source']:
                raise ValueError('CPU catalogue URL differs from its pinned source')
    return catalog


def firmware_metadata(root):
    return load_catalog(root)['components']


def plan_required(root, manifest, identity):
    if identity is None: return {}, {}
    from graphics import require_quiescent, _fingerprint
    require_quiescent(root)
    from maintenance.engine import Engine
    state = Engine(root, read_only=True)._state()
    if 'core:firmware-boot' in state['components']:
        raise ValueError('Restore the existing core:firmware-boot transaction before planning CPU microcode again')
    standalone = 'boot/' + identity.split(':')[1] + '-ucode.img'
    for target, owner in state['owners'].items():
        if re.fullmatch(r'boot/initramfs-[A-Za-z0-9._+-]+\.img', target, re.IGNORECASE) or target.casefold() == standalone:
            raise ValueError('Restore the existing boot owner transaction before planning CPU microcode: ' + owner['component'])
    bundle = manifest.get(identity)
    records = validate_metadata(bundle, identity)
    required, preserved, fingerprints = [], [], {}
    for target in sorted(records):
        paths = [path for suffix in ('', '.xz', '.zst', '.gz') for path in native_matches(Path(root), target + suffix)]
        if paths:
            for path in paths:
                name = path.relative_to(root).as_posix()
                if not existing_firmware(Path(root), name): raise ValueError('Invalid native CPU firmware: ' + name)
                from wireless import native_path
                preserved.append(name)
                fingerprints[name] = _fingerprint(native_path(Path(root), name))
        else:
            required.append(target)
    plan = dict(files=required, preserved=preserved, preserved_fingerprints=fingerprints,
                shared_files=[], requires=[], requirements=sorted(records), resolved_requirements=sorted(records),
                unavailable_requirements=[], alternatives={}, coverage='runtime_dependent', runtime='not_validated',
                runtime_reason='The kernel matches the running processor at early boot',
                payload_version=bundle['version'], provenance=bundle['provenance'])
    return records, {identity: plan}
