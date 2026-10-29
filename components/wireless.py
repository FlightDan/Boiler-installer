"""Resolve firmware against existing target modules; never ship a replacement kernel."""
import fnmatch
from pathlib import Path
from pathlib import PurePosixPath
import stat
import subprocess
import re
import gzip
import lzma
import tempfile
from payload_lock import checked, relative, regular

VENDORS = ('intel', 'realtek', 'mediatek')
MODULES = {'intel': ('iwl*',), 'realtek': ('rtl*', 'rtw*'), 'mediatek': ('mt76*', 'mt79*')}
TRANSPORTS = ('wifi', 'bluetooth')
BLUETOOTH_MODULES = {'intel': ('btintel', 'btintel_pcie'), 'realtek': ('btrtl',), 'mediatek': ('btmtk',)}
# Driver-derived names, not a vendor directory copy. These families include
# names constructed from controller registers at runtime, so MODULE_FIRMWARE
# alone cannot establish hardware coverage. Reviewed upstream implementations:
# https://github.com/torvalds/linux/blob/v6.12/drivers/bluetooth/btintel.c
# https://github.com/torvalds/linux/blob/v6.12/drivers/bluetooth/btrtl.c
# https://github.com/torvalds/linux/blob/v6.12/drivers/bluetooth/btmtk.c
# https://github.com/torvalds/linux/blob/v6.12/drivers/bluetooth/btmtk.h
BLUETOOTH_PATHS = {
    'intel': (r'intel/ibt-[A-Za-z0-9.-]+\.(?:sfi|ddc|bseq)',),
    'realtek': (r'rtl_bt/rtl[A-Za-z0-9_]+_(?:fw(?:_v[0-9]+)?|config)\.bin',),
    'mediatek': (r'mediatek/BT_RAM_CODE_MT[0-9A-Fa-f]{4}_[A-Za-z0-9_]+_hdr\.bin',
                 r'mediatek/mt7925/BT_RAM_CODE_MT7925_[A-Za-z0-9_]+_hdr\.bin',
                 r'mediatek/mt(?:7622|7663|7668)pr2h\.bin'),
}


def bluetooth_path(vendor, path):
    return any(re.fullmatch(pattern, path) for pattern in BLUETOOTH_PATHS[vendor])


def component_identity(value):
    """Legacy Wi-Fi vendor tokens remain supported alongside explicit IDs."""
    if not isinstance(value, str): raise ValueError('Unknown wireless component')
    identity = value if ':' in value else 'wifi:' + value
    transport, vendor = identity.split(':', 1)
    if transport not in TRANSPORTS or vendor not in VENDORS:
        raise ValueError('Unknown wireless vendor or transport')
    return identity
# iwl6000 advertises API 6 although only API 4 was released. The driver's
# explicit minimum is 4; do not invent a generic "lower version is okay" rule.
# Upstream source: https://kernel.googlesource.com/pub/scm/linux/kernel/git/torvalds/linux/+/1c8c5a9d38f607c0b6fd12c91cbe1a4418762a21/drivers/net/wireless/intel/iwlwifi/cfg/6000.c
ALTERNATIVES = {'iwlwifi-6000-6.ucode': ('iwlwifi-6000-4.ucode',)}


def native_path(root, image_relative):
    """Resolve native links in the image namespace, including missing leaves."""
    root = Path(root)
    pending = list(relative(image_relative).parts)
    resolved = []
    visited = set()
    while pending:
        part = pending.pop(0)
        if part in ('', '.'): continue
        if part == '..':
            if not resolved: raise ValueError('Native firmware link escapes image root')
            resolved.pop(); continue
        candidate = root.joinpath(*resolved, part)
        if candidate.is_symlink():
            key = (tuple(resolved), part, tuple(pending))
            if key in visited or len(visited) >= 40: raise ValueError('Native firmware link cycle')
            visited.add(key)
            target = PurePosixPath(candidate.readlink().as_posix())
            if target.is_absolute(): resolved = []; link_parts = target.parts[1:]
            else: link_parts = target.parts
            pending = list(link_parts) + pending
        else:
            if hasattr(candidate, 'is_junction') and candidate.is_junction(): raise ValueError('Native firmware junction is unsupported')
            resolved.append(part)
    return root.joinpath(*resolved)


def existing_firmware(root, image_relative):
    """Read-only native links stay inside the image; payload links are banned."""
    candidate = native_path(root, image_relative)
    try: return stat.S_ISREG(candidate.lstat().st_mode)
    except FileNotFoundError: return False


def native_matches(root, pattern):
    """Glob in the target namespace without following guest-host directory links."""
    root = Path(root)
    patterns = relative(pattern).parts
    paths = ['']
    inspected = 0
    for index, part in enumerate(patterns):
        next_paths = []
        for prefix in paths:
            directory = native_path(root, prefix) if prefix else root
            if not any(marker in part for marker in '*?['):
                # Exact firmware names dominate real module requirements. Do not
                # enumerate every file in /usr/lib for each pathname component.
                # lstat deliberately retains broken leaf links for the caller's
                # conservative preservation policy; namespace resolution below
                # still handles aliases inside the image rather than the host.
                try:
                    mode = (directory / part).lstat().st_mode
                except (FileNotFoundError, NotADirectoryError):
                    continue
                lexical = prefix + '/' + part if prefix else part
                if index == len(patterns) - 1:
                    if stat.S_ISLNK(mode) or stat.S_ISREG(mode): next_paths.append(lexical)
                elif native_path(root, lexical).is_dir():
                    next_paths.append(lexical)
                continue
            try:
                entries = list(directory.iterdir())
            except (FileNotFoundError, NotADirectoryError):
                continue
            inspected += len(entries)
            if inspected > 65536: raise ValueError('Native firmware enumeration exceeds inspection limit')
            for entry in entries:
                if not fnmatch.fnmatchcase(entry.name, part): continue
                lexical = prefix + '/' + entry.name if prefix else entry.name
                if index < len(patterns) - 1:
                    if native_path(root, lexical).is_dir(): next_paths.append(lexical)
                elif entry.is_symlink() or stat.S_ISREG(entry.lstat().st_mode):
                    next_paths.append(lexical)
        paths = next_paths
    return [root / value for value in paths]


def selected(options):
    values = options.get('wireless', [])
    if values is None: values = []  # Optional SelectedOptions field from C#.
    if not isinstance(values, list) or any(type(value) is not str for value in values):
        raise ValueError('Wireless selection must be a list')
    values = values + [value for value in options.get('repairs', []) if value.startswith(('wifi:', 'bluetooth:'))]
    normalized = [component_identity(value).removeprefix('wifi:') for value in values]
    return sorted(set(normalized))


def module_requirements(root, vendor, probe=None, transport='wifi'):
    """Ask modinfo about each installed module, including compressed modules."""
    root = Path(root)
    if vendor not in VENDORS or transport not in TRANSPORTS: raise ValueError('Unknown wireless vendor or transport')
    directory = native_path(root, 'usr/lib/modules')
    if directory.is_symlink() or not directory.is_dir(): raise ValueError('Target kernel modules are missing')
    def modinfo(path):
        # BusyBox modinfo in the isolated Alpine runtime need not support the
        # target SteamOS module compression. Decode only into ephemeral scratch.
        compressed = path.suffix in ('.zst', '.xz', '.gz')
        if not compressed:
            return subprocess.check_output(['modinfo', '-F', 'firmware', str(path)], text=True, timeout=30).splitlines()
        maximum = 64 * 1024 * 1024
        if path.stat().st_size > maximum: raise ValueError('Compressed module exceeds inspection limit')
        if path.suffix == '.zst':
            process = subprocess.Popen(['zstd', '-q', '-d', '-c', str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                data = process.stdout.read(maximum + 1)
                if len(data) > maximum: raise ValueError('Decoded module exceeds inspection limit')
                if process.wait(timeout=30): raise ValueError('Target module decompression failed')
            finally:
                if process.poll() is None: process.kill(); process.wait()
                process.stdout.close(); process.stderr.close()
        else:
            opener = lzma.open if path.suffix == '.xz' else gzip.open
            with opener(path, 'rb') as stream: data = stream.read(maximum + 1)
            if len(data) > maximum: raise ValueError('Decoded module exceeds inspection limit')
        with tempfile.TemporaryDirectory(prefix='boiler-modinfo-') as temporary:
            decoded = Path(temporary) / 'module.ko'; decoded.write_bytes(data)
            return subprocess.check_output(['modinfo', '-F', 'firmware', str(decoded)], text=True, timeout=30).splitlines()
    probe = probe or modinfo
    found = []
    requirements = set()
    for path in sorted(directory.rglob('*')):
        name = path.name.split('.ko', 1)[0]
        patterns = BLUETOOTH_MODULES[vendor] if transport == 'bluetooth' else MODULES[vendor]
        if '.ko' not in path.name or not any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns): continue
        if transport == 'bluetooth' and path.parent.relative_to(directory).parts[-2:] != ('drivers', 'bluetooth'):
            continue
        if transport == 'wifi' and vendor == 'realtek' and 'wireless' not in path.parts and name.startswith('rtl81'):
            continue  # rtl8169/rtl8152 Ethernet are outside a Wi-Fi selection.
        regular(root, path.relative_to(root).as_posix())
        found.append(path.relative_to(root).as_posix())
        for value in probe(path):
            value = value.strip()
            if value:
                relative(value)
                requirements.add(value)
    if not found: raise ValueError('Selected vendor has no existing target driver: ' + vendor)
    if not requirements and transport == 'wifi': raise ValueError('Existing driver declares no firmware requirements: ' + vendor)
    return sorted(requirements), found


def validate_record_metadata(item):
    if not isinstance(item, dict): raise ValueError('Invalid firmware record')
    relative(item.get('source')); relative(item.get('target'))
    if type(item.get('size')) is not int or not 0 <= item['size'] <= 128 * 1024 * 1024:
        raise ValueError('Invalid firmware record size')
    if not isinstance(item.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', item['sha256']):
        raise ValueError('Invalid firmware SHA256')
    if 'git_blob' in item and (not isinstance(item['git_blob'], str) or not re.fullmatch('[0-9a-f]{40}', item['git_blob'])):
        raise ValueError('Invalid firmware Git blob')
    if 'url' in item and (not isinstance(item['url'], str) or not item['url'].startswith('https://')):
        raise ValueError('Invalid firmware source URL')
    if 'alias_of' in item: relative(item['alias_of'])
    if 'alias_evidence' in item:
        evidence = item['alias_evidence']
        if not isinstance(evidence, list) or not evidence: raise ValueError('Invalid firmware alias evidence')
        for declaration in evidence:
            if not isinstance(declaration, dict): raise ValueError('Invalid firmware alias declaration')
            relative(declaration.get('source'))
            if declaration.get('source') == 'WHENCE':
                if type(declaration.get('line')) is not int or declaration['line'] < 1 or not isinstance(declaration.get('declaration'), str):
                    raise ValueError('Invalid WHENCE declaration')
            elif (declaration.get('kind') != 'git-symlink' or
                  not re.fullmatch('[0-9a-f]{40}', str(declaration.get('git_blob', '')))):
                raise ValueError('Invalid Git symlink declaration')
    if 'whence' in item:
        evidence = item['whence']
        if (not isinstance(evidence, dict) or evidence.get('source') != 'WHENCE' or
                not re.fullmatch('[0-9a-f]{40}', str(evidence.get('git_blob', ''))) or
                not isinstance(evidence.get('url'), str) or not evidence['url'].startswith('https://')):
            raise ValueError('Invalid firmware WHENCE evidence')


def validate_metadata(bundle, vendor, transport='wifi'):
    if (bundle.get('schema') != 1 or bundle.get('vendor') != vendor or
            transport not in TRANSPORTS or bundle.get('transport', 'wifi') != transport or
            bundle.get('kind') != 'firmware-only' or not bundle.get('version') or
            not isinstance(bundle.get('files'), list) or not bundle['files']):
        raise ValueError('Missing or invalid pinned firmware bundle: ' + vendor)
    if 'provenance' in bundle:
        provenance = bundle['provenance']
        if (not isinstance(provenance, dict) or
                any(not isinstance(provenance.get(key), str) or not provenance[key] for key in ('url', 'revision', 'license')) or
                not provenance['url'].startswith('https://')):
            raise ValueError('Invalid firmware source provenance')
    result = {}
    for item in bundle['files']:
        validate_record_metadata(item)
        target = relative(item['target']).as_posix()
        if not target.startswith('usr/lib/firmware/') or re.search(r'\.ko(?:\.(?:xz|zst|gz))?$', target):
            raise ValueError('Wireless payload must contain firmware only')
        if transport == 'bluetooth' and not bluetooth_path(vendor, target.removeprefix('usr/lib/firmware/')):
            raise ValueError('Bluetooth payload contains a path outside the driver firmware allowlist')
        if target in result: raise ValueError('Duplicate firmware destination')
        result[target] = item
    return result


def validate_bundle(bundle, payload, vendor, transport='wifi'):
    return {target: checked(payload, item) for target, item in validate_metadata(bundle, vendor, transport).items()}


def plan_required(root, manifest, vendors, probe=None):
    """Inspect target modules and metadata without reading any firmware bytes."""
    all_files = {}
    plans = {}
    for value in vendors:
        identity = component_identity(value)
        transport, vendor = identity.split(':', 1)
        if identity in plans: continue
        bundle = manifest.get(identity)
        if not isinstance(bundle, dict): raise FileNotFoundError('Firmware payload unavailable: ' + vendor)
        files = validate_metadata(bundle, vendor, transport)
        # Content locks detect collisions without requiring an offline full copy.
        for target, data in files.items():
            if target in all_files and (all_files[target]['size'], all_files[target]['sha256']) != (data['size'], data['sha256']):
                raise ValueError('Conflicting firmware payloads: ' + target)
            all_files.setdefault(target, data)
        requirements, modules = module_requirements(root, vendor, probe, transport)
        required = set()
        existing = []
        alternatives = {}
        unavailable = []
        resolved_targets = set()
        for requirement in requirements:
            satisfied = False
            for candidate in (requirement, *ALTERNATIVES.get(requirement, ())):
                pattern = 'usr/lib/firmware/' + candidate
                matches = {target for target in files if fnmatch.fnmatchcase(target, pattern)}
                # Existing firmware has precedence and is never overwritten,
                # including compressed files from the selected SteamOS image.
                installed = native_matches(root, pattern)
                for suffix in ('.xz', '.zst'):
                    installed.extend(native_matches(root, pattern + suffix))
                aliases_present = bool(installed)
                installed = [item for item in installed if existing_firmware(root, item.relative_to(root).as_posix())]
                if installed:
                    existing.extend(p.relative_to(root).as_posix() for p in installed)
                    satisfied = True
                elif matches and not aliases_present:
                    required.update(matches); satisfied = True
                if satisfied:
                    resolved_targets.update(matches)
                    if candidate != requirement: alternatives[requirement] = candidate
                    break
            if not satisfied: unavailable.append(requirement)
        if transport == 'wifi' and len(unavailable) == len(requirements):
            raise ValueError('Pinned bundle cannot satisfy any target firmware requirements: ' + vendor)
        plans[identity] = dict(modules=modules, requirements=requirements,
                                      preserved=sorted(set(existing)), files=sorted(required), alternatives=alternatives,
                                      unavailable_requirements=unavailable,
                                      resolved_requirements=[item for item in requirements if item not in unavailable],
                                      coverage='partial' if unavailable else 'complete', runtime='not_validated',
                                      payload_version=bundle['version'], provenance=bundle.get('provenance', {}))
        if transport == 'bluetooth':
            # A matching installed helper justifies only this reviewed filename
            # family. Controller identity is unavailable offline. Stage missing
            # candidates without claiming they are the runtime-selected blob.
            blocked = []
            for target in files:
                native = [item.relative_to(root).as_posix() for suffix in ('', '.xz', '.zst')
                          for item in native_matches(root, target + suffix)]
                if native:
                    if all(existing_firmware(root, item) for item in native): existing.extend(native)
                    else: blocked.append(target)
                else: required.add(target)
            plans[identity].update(files=sorted(required), preserved=sorted(set(existing)),
                                   coverage='runtime_dependent',
                                   declared_coverage='partial' if unavailable else 'complete' if requirements else 'not_declared',
                                   runtime_candidates=sorted(files), unavailable_candidates=blocked,
                                   runtime_reason='Controller-derived firmware names require boot-time hardware validation',
                                   firmware_scope='reviewed-driver-filename-families', transport=transport)
        if transport == 'wifi' and vendor == 'intel':
            # Intel's PNVM name is derived at runtime and is not consistently
            # present in MODULE_FIRMWARE. Carry only companions of the firmware
            # selected by this installed module, preserving existing PNVM too.
            for target in resolved_targets:
                companion = re.sub(r'-(?:c)?\d+\.ucode$', '.pnvm', target)
                if companion == target or companion not in files: continue
                native = [item.relative_to(root).as_posix() for suffix in ('', '.xz', '.zst')
                          for item in native_matches(root, companion + suffix)]
                if native:
                    if all(existing_firmware(root, item) for item in native):
                        existing.extend(native)
                    else:
                        plans[identity].setdefault('unavailable_companions', []).append(companion)
                        plans[identity]['coverage'] = 'partial'
                    continue
                required.add(companion)
            plans[identity]['files'] = sorted(required)
            plans[identity]['preserved'] = sorted(set(existing))
    owners = {}
    for identity, plan in plans.items():
        own, shared, dependencies = [], [], set()
        for target in plan['files']:
            if target in owners:
                shared.append(target); dependencies.add(owners[target])
            else:
                owners[target] = identity; own.append(target)
        plan.update(files=own, shared_files=shared, requires=sorted(dependencies))
    writes = {target: all_files[target] for target in owners}
    return writes, plans


def resolve(root, payload, manifest, vendors, probe=None):
    """Read and verify only missing firmware selected by the metadata plan."""
    records, plans = plan_required(root, manifest, vendors, probe)
    return {target: checked(payload, item) for target, item in records.items()}, plans
