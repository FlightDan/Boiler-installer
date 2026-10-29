"""Editable graphics candidates and their reproducible metadata-only baseline."""
import hashlib
import fnmatch
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit

DELIVERY = dict(mode='on-demand', installer_bundled_binaries=False,
                preserve_existing=True, selection_scope='generic-without-device-package',
                plan_source='fresh-profile-and-image-bound-plan')
GATES = dict(candidate_baseline='recorded', target_kernel='checked-in-VM',
             early_boot='verify-in-VM', runtime='not_validated')
BASE_PREFIX = 'https://kernel.googlesource.com/pub/scm/linux/kernel/git/firmware/linux-firmware/+/'
IDENTITY = r'graphics:(?:amd|intel)-[a-z0-9-]{1,80}'


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= 4096 and not any(ord(c) < 32 for c in value)


def _relative(value):
    if (not _text(value) or '\\' in value or ':' in value or value.startswith('/')
            or any(p in ('', '.', '..') for p in value.split('/'))):
        raise ValueError('Unsafe graphics path')
    return PurePosixPath(value)


def _read(root, name):
    path = Path(root) / 'components' / name
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise ValueError('Graphics metadata cannot be redirected')
    return path.read_bytes()


def load_spec(root):
    raw = _read(root, 'graphics-baseline-spec.json')
    spec = json.loads(raw)
    revision = spec.get('revision', '')
    if (type(spec.get('schema')) is not int or spec.get('schema') != 1 or spec.get('kind') != 'GraphicsCandidateBaselineSpec'
            or not re.fullmatch(r'[0-9a-f]{40}', revision)
            or spec.get('upstream_url') != BASE_PREFIX + revision + '/'
            or not _text(spec.get('version')) or not isinstance(spec.get('profiles'), dict)
            or not 1 <= len(spec['profiles']) <= 64):
        raise ValueError('Invalid editable graphics baseline specification')
    for identity, profile in spec['profiles'].items():
        if not re.fullmatch(IDENTITY, identity): raise ValueError('Invalid graphics profile identity')
        expected = ['amdgpu'] if identity.startswith('graphics:amd-') else ['xe']
        if profile.get('drivers') != expected: raise ValueError('Invalid graphics profile driver')
        paths = profile.get('patterns', []) + profile.get('extra_paths', [])
        if not paths or not all(isinstance(p, str) for p in paths): raise ValueError('Missing graphics family paths')
        for path in paths:
            _relative(path)
            if ('**' in path or not path.endswith('.bin')
                    or path.split('/')[0] not in (('amdgpu',) if expected == ['amdgpu'] else ('i915', 'xe'))):
                raise ValueError('Graphics pattern leaves its driver family')
        if not profile.get('evidence') or not _text(profile.get('note')):
            raise ValueError('Graphics baseline evidence and uncertainty are required')
    return spec, hashlib.sha256(raw).hexdigest()


def validate_catalog(catalog):
    if (not isinstance(catalog, dict) or catalog.get('schema') != 1
            or catalog.get('kind') != 'GraphicsCandidateCatalogue'
            or catalog.get('delivery') != DELIVERY):
        raise ValueError('Invalid graphics candidate catalogue')
    origin = catalog.get('firmware_origin', {})
    revision = origin.get('revision', '')
    if (not re.fullmatch(r'[0-9a-f]{40}', revision)
            or origin.get('url') != BASE_PREFIX + revision + '/'
            or origin.get('scope') != 'editable-test-baseline'):
        raise ValueError('Invalid graphics candidate origin')
    profiles, ids, pci_owners = catalog.get('profiles'), set(), set()
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= 64: raise ValueError('Missing graphics profiles')
    allowed = {'id', 'name', 'processors', 'graphics_name', 'architecture', 'compute_units', 'pci_ids',
               'drivers', 'install_available', 'status', 'gates', 'hardware_evidence', 'candidate_baseline'}
    for profile in profiles:
        if not isinstance(profile, dict) or set(profile) != allowed: raise ValueError('Invalid graphics profile fields')
        identity = profile['id']
        if not isinstance(identity, str) or not re.fullmatch(IDENTITY, identity) or identity in ids:
            raise ValueError('Invalid graphics profile identity')
        ids.add(identity)
        vendor = identity.split(':')[1].split('-')[0]
        if (profile['install_available'] is not True or profile['status'] != 'experimental-baseline'
                or profile['gates'] != GATES or profile['drivers'] != (['amdgpu'] if vendor == 'amd' else ['xe'])):
            raise ValueError('Graphics candidates must retain experimental runtime boundaries')
        baseline = profile['candidate_baseline']
        if (not isinstance(baseline, dict) or set(baseline) != {'version', 'revision'}
                or baseline['revision'] != revision or not _text(baseline['version'])):
            raise ValueError('Invalid graphics baseline reference')
        if (set(profile['name']) != {'zh', 'en'} or not all(_text(v) for v in profile['name'].values())
                or not profile['processors'] or not all(_text(v) for v in profile['processors'])
                or not _text(profile['graphics_name']) or not _text(profile['architecture'])
                or type(profile['compute_units']) is not int or not 1 <= profile['compute_units'] <= 128):
            raise ValueError('Invalid documented graphics identity')
        for pci in profile['pci_ids']:
            if (not isinstance(pci, str) or not re.fullmatch(r'(?:1002|8086):[0-9a-f]{4}', pci)
                    or pci.split(':')[0] != ('1002' if vendor == 'amd' else '8086') or pci in pci_owners):
                raise ValueError('Invalid graphics PCI identity')
            pci_owners.add(pci)
        if not profile['hardware_evidence']: raise ValueError('Missing hardware evidence')
        for item in profile['hardware_evidence']:
            if set(item) != {'url', 'scope'} or item['scope'] != 'hardware-identity-only':
                raise ValueError('Hardware evidence cannot claim firmware coverage')
            url = urlsplit(item['url'])
            hosts = {'amd.com', 'www.amd.com', 'rocmdocs.amd.com', 'rocm.docs.amd.com'} if vendor == 'amd' else {'intel.com', 'www.intel.com'}
            if (url.scheme != 'https' or url.hostname not in hosts or url.username or url.password
                    or url.port not in (None, 443) or not url.path or url.query or url.fragment):
                raise ValueError('Invalid primary hardware evidence URL')
    tokens = set()
    for request in catalog.get('requests', []):
        if (set(request) != {'token', 'status', 'candidate_profiles'} or not _text(request['token'])
                or request['token'] in tokens or request['status'] not in ('documented-candidates', 'needs-model-clarification')
                or not request['candidate_profiles'] or set(request['candidate_profiles']) - ids
                or len(request['candidate_profiles']) != len(set(request['candidate_profiles']))):
            raise ValueError('Invalid graphics family intake')
        tokens.add(request['token'])
        if request['token'] in ('7800', '8800') and request['status'] != 'needs-model-clarification':
            raise ValueError('Ambiguous marketing names cannot be automatic aliases')
    if not tokens: raise ValueError('Missing graphics intake')
    return catalog


def load_catalog(root):
    catalog = validate_catalog(json.loads(_read(root, 'graphics-support.json')))
    spec, _ = load_spec(root)
    if (catalog['firmware_origin']['revision'] != spec['revision']
            or {p['id'] for p in catalog['profiles']} != set(spec['profiles'])
            or any(p['candidate_baseline'] != dict(version=spec['version'], revision=spec['revision']) for p in catalog['profiles'])):
        raise ValueError('Graphics catalogue baseline is stale; regenerate it')
    return catalog


def firmware_metadata(root):
    """Return verified current snapshot metadata; never fetch or read binaries."""
    spec, digest = load_spec(root)
    snapshot = json.loads(_read(root, 'graphics-firmware.json'))
    if (snapshot.get('schema') != 1 or snapshot.get('kind') != 'GraphicsFirmwareBaseline'
            or snapshot.get('spec_sha256') != digest or snapshot.get('version') != spec['version']
            or snapshot.get('revision') != spec['revision'] or not isinstance(snapshot.get('components'), dict)
            or set(snapshot['components']) != set(spec['profiles'])):
        raise ValueError('Graphics firmware baseline is stale; regenerate from the editable specification')
    baseline = dict(spec_sha256=digest, version=spec['version'], revision=spec['revision'])
    for identity, bundle in snapshot['components'].items():
        if (bundle.get('schema') != 1 or bundle.get('kind') != 'GraphicsFirmwareBundle'
                or bundle.get('id') != identity or bundle.get('version') != spec['version']
                or bundle.get('drivers') != spec['profiles'][identity]['drivers']
                or bundle.get('available') is not True or bundle.get('delivery') != 'on-demand'
                or bundle.get('runtime') != 'not_validated' or bundle.get('status') != 'experimental-baseline'
                or bundle.get('baseline') != baseline
                or bundle.get('provenance') != dict(revision=spec['revision'], url=spec['upstream_url'])):
            raise ValueError('Invalid graphics firmware bundle baseline')
        if not bundle.get('files') or not bundle.get('licenses'): raise ValueError('Incomplete graphics bundle')
        if len(bundle['files']) > 256 or len(bundle['licenses']) > 32: raise ValueError('Oversized graphics bundle')
        prefix = 'graphics/' + identity.split(':')[1] + '/'
        patterns = spec['profiles'][identity]['patterns'] + spec['profiles'][identity].get('extra_paths', [])
        seen = set()
        for license_file, records in ((False, bundle['files']), (True, bundle['licenses'])):
            for entry in records:
                target = entry.get('target', '')
                _relative(target)
                expected = prefix + (target if license_file else 'firmware/' + target.removeprefix('usr/lib/firmware/'))
                upstream = entry.get('alias_of', target.removeprefix('usr/lib/firmware/'))
                if license_file:
                    if not target.startswith('licenses/') or len(target.split('/')) != 2: raise ValueError('Invalid graphics license destination')
                    upstream = 'WHENCE' if target == 'licenses/WHENCE' else 'LICENSES/' + target.removeprefix('licenses/')
                elif (not target.startswith('usr/lib/firmware/') or target.split('/')[3] not in (('amdgpu',) if bundle['drivers'] == ['amdgpu'] else ('i915', 'xe'))):
                    raise ValueError('Graphics firmware leaves its driver family')
                if not license_file:
                    for path in (target.removeprefix('usr/lib/firmware/'), upstream):
                        if not any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns):
                            raise ValueError('Graphics firmware leaves its selected candidate family')
                    if 'alias_of' in entry and not entry.get('alias_evidence'):
                        raise ValueError('Graphics firmware alias requires source evidence')
                _relative(upstream)
                if (entry.get('source') != expected or target in seen or type(entry.get('size')) is not int
                        or not 0 < entry['size'] <= 64 * 1024 * 1024
                        or not re.fullmatch(r'[0-9a-f]{64}', entry.get('sha256', ''))
                        or not re.fullmatch(r'[0-9a-f]{40}', entry.get('git_blob', ''))
                        or entry.get('url') != spec['upstream_url'] + upstream):
                    raise ValueError('Invalid graphics firmware record')
                seen.add(target)
    return snapshot['components']


def metadata(root):
    catalog = load_catalog(root)
    firmware_metadata(root)
    content = json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return dict(schema=1, kind='GraphicsCandidateMetadata', sha256=hashlib.sha256(content).hexdigest(),
                install_available=True, firmware_binary_count=0, runtime='not_validated',
                status='experimental-baseline', catalogue=catalog)
