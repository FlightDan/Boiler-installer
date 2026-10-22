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
    values = values + [value for value in options.get('repairs', []) if value.startswith('wifi:')]
    normalized = [value.removeprefix('wifi:') for value in values]
    if any(value not in VENDORS for value in normalized): raise ValueError('Unknown wireless vendor')
    return sorted(set(normalized))


def module_requirements(root, vendor, probe=None):
    """Ask modinfo about each installed module, including compressed modules."""
    root = Path(root)
    directory = root / 'usr/lib/modules'
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
        if '.ko' not in path.name or not any(fnmatch.fnmatchcase(name, pattern) for pattern in MODULES[vendor]): continue
        if vendor == 'realtek' and 'wireless' not in path.parts and name.startswith('rtl81'):
            continue  # rtl8169/rtl8152 Ethernet are outside a Wi-Fi selection.
        regular(root, path.relative_to(root).as_posix())
        found.append(path.relative_to(root).as_posix())
        for value in probe(path):
            value = value.strip()
            if value:
                relative(value)
                requirements.add(value)
    if not found: raise ValueError('Selected vendor has no existing target driver: ' + vendor)
    if not requirements: raise ValueError('Existing driver declares no firmware requirements: ' + vendor)
    return sorted(requirements), found


def validate_bundle(bundle, payload, vendor):
    if (bundle.get('schema') != 1 or bundle.get('vendor') != vendor or
            bundle.get('kind') != 'firmware-only' or not bundle.get('version') or
            not isinstance(bundle.get('files'), list) or not bundle['files']):
        raise ValueError('Missing or invalid pinned firmware bundle: ' + vendor)
    result = {}
    for item in bundle['files']:
        target = relative(item['target']).as_posix()
        if not target.startswith('usr/lib/firmware/') or target.endswith(('.ko', '.ko.xz', '.ko.zst')):
            raise ValueError('Wireless payload must contain firmware only')
        if target in result: raise ValueError('Duplicate firmware destination')
        result[target] = checked(payload, item)
    return result


def resolve(root, payload, manifest, vendors, probe=None):
    """Validate every bundle and every collision before returning writes."""
    all_files = {}
    plans = {}
    for vendor in vendors:
        if vendor not in VENDORS: raise ValueError('Unknown wireless vendor')
        bundle = manifest.get('wifi:' + vendor)
        if not isinstance(bundle, dict): raise FileNotFoundError('Firmware payload unavailable: ' + vendor)
        files = validate_bundle(bundle, payload, vendor)
        # Compare actual bytes, including files irrelevant to this target kernel.
        for target, data in files.items():
            if target in all_files and all_files[target] != data:
                raise ValueError('Conflicting firmware payloads: ' + target)
            all_files[target] = data
        requirements, modules = module_requirements(root, vendor, probe)
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
        if len(unavailable) == len(requirements):
            raise ValueError('Pinned bundle cannot satisfy any target firmware requirements: ' + vendor)
        plans['wifi:' + vendor] = dict(modules=modules, requirements=requirements,
                                      preserved=sorted(set(existing)), files=sorted(required), alternatives=alternatives,
                                      unavailable_requirements=unavailable,
                                      resolved_requirements=[item for item in requirements if item not in unavailable],
                                      coverage='partial' if unavailable else 'complete', runtime='not_validated',
                                      payload_version=bundle['version'], provenance=bundle.get('provenance', {}))
        if vendor == 'intel':
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
                        plans['wifi:' + vendor].setdefault('unavailable_companions', []).append(companion)
                        plans['wifi:' + vendor]['coverage'] = 'partial'
                    continue
                required.add(companion)
            plans['wifi:' + vendor]['files'] = sorted(required)
            plans['wifi:' + vendor]['preserved'] = sorted(set(existing))
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
