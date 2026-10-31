"""Host-committed unsigned development sessions; never production authentication."""
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import subprocess

from maintenance.resource_runtime import ResourceError, ResourcePool
from protocol.resource_pool import json_document, local, validate_pool

PURPOSE = 'resource-guest-local-test'
ANCHOR = Path('/run/boiler-resource-anchor.json')


def secure_anchor(path=ANCHOR):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0
                or info.st_mode & 0o077 or info.st_size > 4096):
            raise ResourceError('Untrusted resource session anchor')
        raw = stream.read(4097)
    if not raw or len(raw) > 4096:
        raise ResourceError('Invalid resource session anchor size')
    return json_document(raw)


def verify_binding(raw, anchor, reviewed, action):
    """anchor comes from the trusted launch channel, never from payload/options."""
    if (not isinstance(anchor, dict) or set(anchor) != {'schema_version', 'kind', 'purpose', 'session_id',
            'nonce', 'action', 'binding_sha256'} or anchor.get('schema_version') != 1
            or anchor.get('kind') != 'BoilerResourceGuestAnchor' or anchor.get('purpose') != PURPOSE
            or anchor.get('action') != action or action not in ('apply', 'verify')
            or any(not isinstance(anchor.get(k), str) or not re.fullmatch('[0-9a-f]{32}', anchor[k])
                   for k in ('session_id', 'nonce'))
            or not isinstance(anchor.get('binding_sha256'), str)
            or not re.fullmatch('[0-9a-f]{64}', anchor['binding_sha256'])
            or not isinstance(raw, bytes) or not 0 < len(raw) <= 65536):
        raise ResourceError('Invalid development-session launch anchor')
    actual = hashlib.sha256(('BoilerHelperRequest:v1:' + PURPOSE + '\0').encode() + raw).hexdigest()
    if not hmac.compare_digest(actual, anchor['binding_sha256']):
        raise ResourceError('Resource session differs from host launch commitment')
    value = json_document(raw)
    if (value.get('schema_version') != 1 or value.get('kind') != 'BoilerResourceGuestSession'
            or value.get('trust') != 'development-session' or value.get('selection') != reviewed
            or reviewed.get('trust') != 'local-test' or value.get('action') != action
            or any(value.get(k) != anchor[k] for k in ('session_id', 'nonce'))
            or not isinstance(value.get('source_sha256'), str)
            or not re.fullmatch('[0-9a-f]{64}', value['source_sha256'])
            or not isinstance(value.get('physical_facts'), dict)):
        raise ResourceError('Resource session purpose, selection or identity changed')
    return value


def verify_preparation(raw, binding):
    expected = binding.get('preparation_sha256')
    actual = hashlib.sha256(b'BoilerHelperRequest:v1:resource-preparation-local-test\0' + raw).hexdigest()
    if not isinstance(expected, str) or not hmac.compare_digest(expected, actual):
        raise ResourceError('Preparation receipt differs from the host-bound actual workflow')
    receipt = json_document(raw)
    if (receipt.get('schema_version') != 1 or receipt.get('kind') != 'ResourcePreparationReceipt'
            or receipt.get('trust') != 'development-session' or receipt.get('status') != 'prepared'
            or receipt.get('session_id') != binding['session_id']
            or receipt.get('source_sha256') != binding['source_sha256']
            or not isinstance(receipt.get('prepared_sha256'), str)
            or not re.fullmatch('[0-9a-f]{64}', receipt['prepared_sha256'])):
        raise ResourceError('Preparation receipt source/session/generation is invalid')
    image = receipt.get('prepared_image', {})
    if any(image.get(key) != binding['image'].get(key) for key in ('length', 'volume', 'file_id', 'disk_guid')):
        raise ResourceError('Preparation receipt belongs to another prepared image')
    operations = [step.get('operation') for step in receipt.get('steps', [])]
    while operations and operations[-1] == 'Backend.GuestAsync:integration': operations.pop()
    copy = ['RawImageStaging.ConvertAsync:reviewed-prepared-copy']
    plain = ['Backend.ImportAsync', 'Backend.GuestAsync:deployment-test', 'RawImageStaging.ConvertAsync:deployed-fixture']
    upgraded = ['Backend.ImportAsync', 'OfficialSystem.PrepareAsync', 'Backend.GuestAsync:deployment-test', 'RawImageStaging.ConvertAsync:deployed-fixture']
    if operations not in (copy, plain, upgraded):
        raise ResourceError('Preparation receipt has an unrecognized or incomplete actual stage chain')
    if len(receipt['steps']) == 1 and operations == copy and receipt['prepared_sha256'] != receipt['source_sha256']:
        raise ResourceError('Prepared copy does not preserve reviewed source bytes')
    return receipt


class LocalSessionAuthorization:
    mode = 'development-session'
    def __init__(self, payload, reviewed, action):
        payload = Path(payload)
        descriptor = os.open(local(payload, 'resource-session.json'),
                             os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 65536:
                raise ResourceError('Resource session must be a bounded ordinary file')
            raw = stream.read(65537)
        self.binding = verify_binding(raw, secure_anchor(), reviewed, action)
        with local(payload, 'resource-preparation.json').open('rb') as stream:
            if not 0 < os.fstat(stream.fileno()).st_size <= 65536:
                raise ResourceError('Preparation receipt must be a bounded ordinary file')
            preparation = stream.read(65537)
        self.preparation = verify_preparation(preparation, self.binding)
        self.reviewed, self.action = reviewed, action
        root = payload / 'integration/resource-pool' if self.binding.get('execution')=='firstboot-install-v1' else payload/'resource-pool'
        files = self.binding.get('files')
        if not isinstance(files, list) or not files or len(files) > 8192:
            raise ResourceError('Invalid host resource inventory')
        known = set()
        for row in files:
            if not isinstance(row, dict) or set(row) != {'path', 'size', 'sha256'} or row['path'] in known:
                raise ResourceError('Duplicate or invalid host resource inventory member')
            data = local(root, row['path']).read_bytes(); known.add(row['path'])
            if (type(row['size']) is not int or row['size'] != len(data)
                    or row['sha256'] != hashlib.sha256(data).hexdigest()):
                raise ResourceError('Host-committed resource bytes changed')
        actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
        if actual != known:
            raise ResourceError('Host resource inventory is incomplete')
        runtime = self.binding.get('runtime_files')
        if not isinstance(runtime, list) or not runtime:
            raise ResourceError('Trusted parser runtime inventory is missing')
        known_runtime = set()
        for row in runtime:
            if (not isinstance(row, dict) or set(row) != {'path', 'size', 'sha256'}
                    or row['path'] in known_runtime or not row['path'].startswith('adaptations/')):
                raise ResourceError('Invalid trusted parser runtime member')
            data = local(payload, row['path']).read_bytes(); known_runtime.add(row['path'])
            if type(row['size']) is not int or row['size'] != len(data) or row['sha256'] != hashlib.sha256(data).hexdigest():
                raise ResourceError('Host-committed parser runtime changed')
        catalog = validate_pool(root, expected_catalog_sha256=reviewed['catalog_sha256'])
        self.pool = ResourcePool(root, catalog, local_test=True)
        self.hardware = self.binding['physical_facts']
        self.filesystems = None
        self.image_digest_seconds = None

    def revalidate(self, disk, selection, table):
        from resource_coordinator import checked_parts
        if disk != '/dev/vdc' or selection != self.reviewed:
            raise ResourceError('Development-session target or selection changed')
        image = self.binding['image']; parts = checked_parts(table)
        serials = {path.read_text().strip() for path in (Path('/sys/class/block/vdc/serial'),
            Path('/sys/class/block/vdc/device/serial')) if path.is_file()}
        if (table.get('sectorsize') != 512 or table.get('id', '').lower() != image['disk_guid'].lower()
                or int(subprocess.check_output(['blockdev', '--getsize64', disk])) != image['length']
                or serials != {'boiler-image'}):
            raise ResourceError('Prepared image differs from host-owned image binding')
        expected = {p['name']: p for p in image['partitions']}
        if len(expected) != len(image['partitions']) or len(table['partitions']) != len(expected):
            raise ResourceError('Prepared partition set differs from host binding')
        for row in table['partitions']:
            old = expected.get(row['name'])
            if (old is None or row['node'] != disk + str(old['number'])
                    or row['start'] * 512 != old['offset'] or row['size'] * 512 != old['size']
                    or row.get('uuid', '').lower() != old['id'].lower()
                    or row.get('type', '').lower() != old['type'].lower()):
                raise ResourceError('Prepared partition identity changed')
        seen, current = set(), {}
        roles=['rootfs-A','rootfs-B','var-A','var-B','home']
        if self.binding.get('execution')=='firstboot-install-v1':roles+=['efi-A','efi-B']
        for label in roles:
            values = {}
            for line in subprocess.check_output(['blkid', '-p', '-o', 'export', parts[label]], text=True).splitlines():
                key, separator, value = line.partition('=')
                if separator: values[key] = value
            wanted = 'btrfs' if label.startswith('rootfs-') else 'vfat' if label.startswith('efi-') else 'ext4'
            identity = values.get('UUID')
            if values.get('TYPE') != wanted or not identity or identity in seen:
                raise ResourceError('Prepared filesystem type/unique UUID is not established: ' + label)
            seen.add(identity); current[label] = (wanted, identity)
        if self.filesystems is not None and current != self.filesystems:
            raise ResourceError('Prepared filesystem identity changed between mount phases')
        self.filesystems = current
        if self.image_digest_seconds is None:
            # Consume the host's actual byte commitment before the first
            # mount/unlock/write. This closes the host-hash→QEMU-open handoff;
            # later authorized writes intentionally change the original hash.
            from resource_image_digest import verify_fixed_image
            self.image_digest_seconds = verify_fixed_image(image['length'], self.preparation['prepared_sha256'],
                readonly=(self.action == 'verify'), progress=lambda done,total,verified: print(
                    'BOILER_RESOURCE_INTEGRITY='+json.dumps(dict(action=self.action,phase='guest',
                        bytes=done,total=total,verified=verified)),flush=True))


def local_authorizer(*, readonly):
    """Only the fixed local-test entrypoint may select this provider."""
    action = 'verify' if readonly else 'apply'
    @contextmanager
    def authorize(payload, reviewed, disk):
        if disk != '/dev/vdc': raise ResourceError('Only the owned prepared image is accepted')
        yield LocalSessionAuthorization(payload, reviewed, action)
    return authorize
