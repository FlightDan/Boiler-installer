"""Fixed ONEXPLAYER Panther Lake early-boot transaction.

The package supplies immutable inputs; boot files are derived from the target,
and the Engine owns the derived files and their preimages as one transaction.
"""
import hashlib
from pathlib import Path
import struct

from protocol.manifest import confined
from .engine import MaintenanceError, _read

IDENTITY = 'onexplayer:intel-microcode'
BLOB_TARGET = 'usr/lib/firmware/intel-ucode/06-cc-02'
LICENSE_TARGET = 'usr/share/licenses/boiler-firmware/microcode/intel/Intel-microcode.txt'
INPUTS = {
    BLOB_TARGET: (165888, '2960a456b2c672614487f410e774012e386bddeaefb5a9899b307ef682cb1536'),
    LICENSE_TARGET: (1677, '03efb1491c7e899feb2665fa299363e64035e5444c1b8bc1f6ebed30de964e12'),
}
SIGNATURES = {0xc06c2, 0xc06c3}
REVISION = 0x11d


def check_component(component):
    rows = component.get('files', [])
    if (component.get('id') != IDENTITY
            or component.get('microcode_boot_policy') != {'kind': 'onexplayer-intel-microcode-v1'}
            or component.get('hooks') or component.get('dynamic_handler')
            or component.get('requires') or component.get('conflicts') or component.get('default') is not False
            or component.get('optional') is not True or len(rows) != len(INPUTS)):
        raise MaintenanceError('Invalid fixed ONEXPLAYER microcode component')
    actual = {row.get('target'): (row.get('size'), row.get('sha256')) for row in rows}
    if actual != INPUTS or any(row.get('mode', 0o644) != 0o644 for row in rows):
        raise MaintenanceError('Invalid fixed ONEXPLAYER microcode inputs')


def patch_matches(data):
    """Validate the complete Intel stream and return signature/flag/revision tuples."""
    from components import microcode_boot
    microcode_boot._intel_valid(data)
    records = []
    offset = 0
    while offset < len(data):
        h = struct.unpack_from('<12I', data, offset)
        total, payload = h[8] or 2048, h[7] or 2000
        records.append((h[3], h[6], h[1]))
        extra = offset + 48 + payload
        if extra < offset + total:
            count = struct.unpack_from('<I', data, extra)[0]
            for index in range(count):
                signature, flags, _ = struct.unpack_from('<3I', data, extra + 20 + 12 * index)
                records.append((signature, flags, h[1]))
        offset += total
    if not all(any(sig == expected and rev == REVISION and flags for sig, flags, rev in records)
               for expected in SIGNATURES):
        raise MaintenanceError('Pinned microcode lacks the reviewed signatures or revision')
    return records


def hardware_snapshot(cpuinfo=Path('/proc/cpuinfo'), sys_cpu=Path('/sys/devices/system/cpu')):
    """Read the running system only; guest image or Windows host is never inferred."""
    sections = cpuinfo.read_text(encoding='ascii').strip().split('\n\n')
    cpus = []
    for section in sections:
        values = {}
        for line in section.splitlines():
            if ':' in line:
                key, value = line.split(':', 1)
                values[key.strip()] = value.strip()
        try:
            number = int(values['processor'])
            family, model, stepping = (int(values[k]) for k in ('cpu family', 'model', 'stepping'))
            signature = ((family & 15) << 8) | ((model & 15) << 4) | stepping | ((model >> 4) << 16)
            flags = int((sys_cpu / ('cpu' + str(number)) / 'microcode/processor_flags').read_text().strip(), 0)
            revision = int(values['microcode'], 0)
        except (KeyError, ValueError, OSError) as error:
            raise MaintenanceError('Cannot establish every running CPU microcode identity') from error
        if (values.get('vendor_id') != 'GenuineIntel' or family != 6 or signature not in SIGNATURES
                or flags <= 0 or not 0 <= stepping <= 15):
            raise MaintenanceError('Running CPU does not match the reviewed Intel target')
        cpus.append(dict(cpu=number, signature=signature, flags=flags, revision=revision))
    if not cpus or len({cpu['cpu'] for cpu in cpus}) != len(cpus):
        raise MaintenanceError('Invalid running CPU inventory')
    return sorted(cpus, key=lambda cpu: cpu['cpu'])


def check_hardware(cpus, records, *, require_newer=True):
    for cpu in cpus:
        if not any(sig == cpu['signature'] and flags & cpu['flags'] and rev == REVISION
                   for sig, flags, rev in records):
            raise MaintenanceError('CPU signature and platform flags do not match the pinned patch')
        if require_newer and cpu['revision'] >= REVISION:
            raise MaintenanceError('Pinned microcode is not newer than the running revision')


def prepare(root, component, payload, *, live=False):
    """Derive a fixed Engine component; caller binds these results to its review."""
    from components import microcode_boot
    check_component(component)
    data = {}
    for row in component['files']:
        path = confined(Path(payload), row['source'])
        if path.is_symlink() or not path.is_file():
            raise MaintenanceError('Missing microcode input')
        blob = path.read_bytes()
        if (len(blob), hashlib.sha256(blob).hexdigest()) != INPUTS[row['target']]:
            raise MaintenanceError('Microcode input differs from its fixed lock')
        data[row['target']] = blob
    records = patch_matches(data[BLOB_TARGET])
    hardware = None
    if live:
        if Path(root).resolve() != Path('/'):
            raise MaintenanceError('Live microcode identity requires the running root')
        hardware = hardware_snapshot()
        check_hardware(hardware, records)
    writes, proof = microcode_boot.prepare(root, 'microcode:intel', {BLOB_TARGET: data[BLOB_TARGET]})
    writes[LICENSE_TARGET] = data[LICENSE_TARGET]
    files = [dict(source='derived/' + target, target=target, size=len(content),
                  sha256=hashlib.sha256(content).hexdigest(),
                  mode=proof['artifacts'][target]['mode'] if target in proof['artifacts'] else 0o644)
             for target, content in sorted(writes.items())]
    derived = dict(schema_version=1, kind='ComponentManifest', id=IDENTITY, version=component['version'],
                   files=files, requires=['core:maintenance'], conflicts=[], early_boot_proof=proof)
    return derived, writes, proof, hardware


def review(engine, component, payload, *, live=False):
    """Bind target preimages and the complete derived transaction to review."""
    from .adoption_blobs import observe
    derived, _, proof, hardware = prepare(engine.root, component, payload, live=live)
    state = engine._state()
    if IDENTITY in state['components']:
        raise MaintenanceError('Restore or verify the existing microcode transaction first')
    targets = {}
    for row in derived['files']:
        name = row['target']
        folded = name.casefold()
        if any(folded == other.casefold() or folded.startswith(other.casefold() + '/')
               or other.casefold().startswith(folded + '/') for other in state['owners']):
            raise MaintenanceError('Microcode target belongs to an existing transaction: ' + name)
        observation = observe(engine, engine._target(name))
        if name == LICENSE_TARGET and observation['exists']:
            raise MaintenanceError('Existing microcode license requires explicit review')
        targets[name] = observation
    return dict(derived=derived, proof=proof, hardware=hardware, targets=targets)


def apply_locked(engine, component, payload, expected, *, live=False):
    """Apply only the freshly revalidated fixed transaction under Engine's lock."""
    import tempfile
    import uuid
    from .adoption_blobs import _directory
    fresh = review(engine, component, payload, live=live)
    if fresh != expected:
        raise MaintenanceError('Microcode source, hardware, or boot preimage changed after review')
    derived, writes, proof, hardware = prepare(engine.root, component, payload, live=live)
    if (derived, proof, hardware) != (fresh['derived'], fresh['proof'], fresh['hardware']):
        raise MaintenanceError('Microcode derivation changed before staging')
    state = engine._state()
    from .core_retention import guard_apply
    from .ssd_boot import reject_conflicting_component
    from .model_efi import reject_conflicting_component as reject_model_efi_conflict
    if 'core:maintenance' not in state['components'] or not type(engine)(engine.root, engine.state_dir, read_only=True).verify_readonly('core:maintenance')['ok']:
        raise MaintenanceError('Early microcode requires verified retained Boiler Maintenance')
    guard_apply(engine, derived, state)
    reject_conflicting_component(engine, derived)
    reject_model_efi_conflict(engine, derived)
    with tempfile.TemporaryDirectory(prefix='boiler-model-microcode-') as temporary:
        staged = Path(temporary)
        for target, data in writes.items():
            destination = staged / 'derived' / target
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        with _directory(engine._target(LICENSE_TARGET).parent, create=True, create_mode=0o755):
            pass
        return engine._apply_locked(derived, staged, {}, state, uuid.uuid4().hex, None, False,
                                    expected_preimages=expected['targets'])


def verify(root, engine):
    """Verify the committed Engine inventory and its early-boot composition."""
    from components import microcode_boot
    result = engine.verify_readonly(IDENTITY)
    if not result['ok']:
        return result
    state = engine._state()
    journal = _read(engine._journal(state['components'][IDENTITY]['transaction']))
    component = journal.get('component', {})
    proof = component.get('early_boot_proof')
    microcode_boot.verify(root, proof)
    artifacts = proof['artifacts']
    rows = component.get('files', [])
    if (component.get('id') != IDENTITY or proof.get('identity') != 'microcode:intel'
            or {row['target'] for row in rows} != set(artifacts) | {LICENSE_TARGET}
            or len(rows) != len(artifacts) + 1):
        raise MaintenanceError('Unexpected microcode transaction inventory')
    license_row = next(row for row in rows if row['target'] == LICENSE_TARGET)
    if (license_row['size'], license_row['sha256']) != INPUTS[LICENSE_TARGET]:
        raise MaintenanceError('Microcode license differs from the reviewed input')
    operations = {row['target']: row for row in journal['operations']}
    if set(operations) != set(artifacts) | {LICENSE_TARGET}:
        raise MaintenanceError('Unexpected microcode transaction operations')
    for target, artifact in artifacts.items():
        before = operations[target]['before']
        if (not before.get('exists') or before.get('sha256') != artifact['original_sha256']
                or before.get('size') != artifact['original_size']):
            raise MaintenanceError('Microcode boot proof differs from its retained preimage')
        content = (Path(root) / target).read_bytes()
        merged = microcode_boot._native_member(content, 'kernel/x86/microcode/GenuineIntel.bin')
        microcode_boot._intel_valid(merged)
        offset = 0
        found = False
        while offset < len(merged):
            header = struct.unpack_from('<12I', merged, offset)
            size = header[8] or 2048
            record = merged[offset:offset + size]
            found |= (len(record), hashlib.sha256(record).hexdigest()) == INPUTS[BLOB_TARGET]
            offset += size
        if not found:
            raise MaintenanceError('The reviewed microcode record is missing from a boot artifact')
    return result
