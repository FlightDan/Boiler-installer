"""Construct the x86 early-microcode archive before the original initramfs."""
import hashlib
from pathlib import Path
import re
import stat
import struct

if __package__:
    from . import graphics_boot
    from .payload_lock import regular, relative
else:
    import graphics_boot
    from payload_lock import regular, relative

STRATEGY = 'prepend-cpu-newc-preserve-initramfs-v1'
NAMES = {'microcode:intel': 'GenuineIntel.bin', 'microcode:amd': 'AuthenticAMD.bin'}


def _sha(data):
    return hashlib.sha256(data).hexdigest()



def _native_member(data, member):
    # Linux scans consecutive uncompressed CPIO archives, including zero
    # padding and trailers, and stops at the first compressed member.
    entries = graphics_boot.inspect(data)
    if member not in entries:
        return b''
    offset = 0
    while offset < len(data):
        while offset < len(data) and data[offset] == 0:
            offset += 1
        if data[offset:offset + 6] not in (b'070701', b'070702'):
            break
        archive_entries = {}
        end_archive = graphics_boot._cpio(data, offset, archive_entries)
        cursor = offset
        while cursor < end_archive:
            header = data[cursor:cursor + 110]
            fields = [int(header[pos:pos + 8], 16) for pos in range(6, 110, 8)]
            mode, size, namesize = fields[1], fields[6], fields[11]
            end = cursor + 110 + namesize
            start = (end + 3) & ~3
            name = graphics_boot._name(data[cursor + 110:end], mode)
            if name == member:
                # Standalone microcode images can use nlink=0. Only counts
                # above one indicate a newc hard-link entry.
                if not stat.S_ISREG(mode) or fields[4] > 1 or not size:
                    raise ValueError('Unsafe native microcode member')
                return data[start:start + size]
            if name == 'TRAILER!!!':
                break
            cursor = (start + size + 3) & ~3
        offset = end_archive
    raise ValueError('Native microcode is not in a visible uncompressed early archive')


def _intel_valid(data):
    offset = 0
    while offset < len(data):
        if len(data) - offset < 48: raise ValueError('Truncated Intel microcode header')
        header = struct.unpack_from('<12I', data, offset)
        size, payload = header[8] or 2048, header[7] or 2000
        if header[0] != 1 or header[5] != 1 or size < 48 + payload or size % 4 or offset + size > len(data):
            raise ValueError('Invalid Intel microcode record')
        main = data[offset:offset + 48 + payload]
        if len(main) % 4 or sum(struct.unpack('<%dI' % (len(main)//4), main)) & 0xffffffff:
            raise ValueError('Invalid Intel microcode checksum')
        extra = data[offset + 48 + payload:offset + size]
        if extra:
            if len(extra) < 20 or (len(extra)-20) % 12 or struct.unpack_from('<I', extra)[0] != (len(extra)-20)//12:
                raise ValueError('Invalid Intel extended signature table')
            if sum(struct.unpack('<%dI' % (len(extra)//4), extra)) & 0xffffffff:
                raise ValueError('Invalid Intel extended signature checksum')
            original_sum = (header[3] + header[6] + header[4]) & 0xffffffff
            for index in range(struct.unpack_from('<I', extra)[0]):
                signature = struct.unpack_from('<3I', extra, 20 + index * 12)
                if (sum(signature) & 0xffffffff) != original_sum:
                    raise ValueError('Invalid Intel individual extended signature checksum')
        offset += size
    if not offset: raise ValueError('Empty Intel microcode')


def _amd_merge(blobs):
    # Each output container contains all patches for one CPU family. Linux's
    # early loader chooses the last matching patch within the first matching
    # container; ascending patch IDs therefore put the newest match last.
    families = {}
    for blob in blobs:
        pos = 0
        while pos < len(blob):
            if len(blob)-pos < 12: raise ValueError('Truncated AMD container')
            magic, kind, table_size = struct.unpack_from('<3I', blob, pos)
            if magic != 0x00414d44 or kind != 0 or table_size % 16 or pos + 12 + table_size > len(blob):
                raise ValueError('Invalid AMD equivalence table')
            table = blob[pos+12:pos+12+table_size]
            pos += 12 + table_size
            sections = []
            while pos < len(blob):
                if len(blob)-pos < 8: raise ValueError('Truncated AMD section')
                section, size = struct.unpack_from('<2I', blob, pos)
                if section == 0x00414d44: break
                if section != 1 or size < 64 or pos + 8 + size > len(blob):
                    raise ValueError('Invalid AMD patch section')
                patch = blob[pos+8:pos+8+size]
                revision = struct.unpack_from('<I', patch, 4)[0]
                processor = struct.unpack_from('<H', patch, 24)[0]
                if struct.unpack_from('<II', patch, 16) != (0, 0):
                    raise ValueError('AMD chipset-specific microcode is unsupported')
                family = 15 + (processor >> 12)
                sections.append((family, revision, blob[pos:pos+8+size]))
                pos += 8 + size
            if not sections: raise ValueError('AMD container has no patches')
            for family, revision, section in sections:
                item = families.setdefault(family, {'entries': {}, 'patches': {}})
                for i in range(0, len(table), 16):
                    entry = table[i:i+16]
                    cpu = struct.unpack_from('<I', entry)[0]
                    if not cpu: continue
                    base = (cpu >> 8) & 15
                    actual = base + ((cpu >> 20) & 255) if base == 15 else base
                    if actual != family: continue
                    previous = item['entries'].setdefault(cpu, entry)
                    if previous != entry: raise ValueError('Conflicting AMD equivalence mappings')
                key = (revision, struct.unpack_from('<H', section, 8+24)[0])
                previous = item['patches'].setdefault(key, section)
                if previous != section: raise ValueError('Conflicting AMD patch revision')
    output = []
    for family, item in sorted(families.items()):
        table = b''.join(entry for _, entry in sorted(item['entries'].items())) + b'\0'*16
        sections = b''.join(section for _, section in sorted(item['patches'].items()))
        output.append(struct.pack('<3I', 0x00414d44, 0, len(table)) + table + sections)
    if not output: raise ValueError('Empty AMD microcode')
    return b''.join(output)

def prepare(root, identity, candidates, graphics_writes=None, graphics_proof=None):
    if identity not in NAMES or not isinstance(candidates, dict) or not candidates:
        raise ValueError('CPU microcode candidates required')
    prefix = 'usr/lib/firmware/' + ('intel-ucode/' if identity == 'microcode:intel' else 'amd-ucode/')
    for name, data in candidates.items():
        if not relative(name).as_posix().startswith(prefix) or not isinstance(data, bytes) or not data:
            raise ValueError('Invalid CPU microcode candidate')
    cpu_data = b''.join(data for name, data in sorted(candidates.items()))
    if len(cpu_data) > graphics_boot.MAX_FRAGMENT:
        raise ValueError('CPU microcode archive exceeds size limit')
    member = 'kernel/x86/microcode/' + NAMES[identity]
    root = Path(root)
    directory = root / 'boot'
    if directory.is_symlink() or not directory.is_dir() or (hasattr(directory, 'is_junction') and directory.is_junction()):
        raise ValueError('Missing or unsafe target boot directory')
    paths = sorted(path for path in directory.iterdir() if re.fullmatch(r'initramfs-[A-Za-z0-9._+-]+\.img', path.name))
    if not paths or len(paths) > graphics_boot.MAX_IMAGES:
        raise ValueError('Missing or excessive target initramfs images')
    graphics_writes = graphics_writes or {}
    if graphics_writes and not graphics_proof: raise ValueError('Graphics boot proof required')
    proof = dict(schema=1, strategy=STRATEGY, identity=identity, runtime='not_validated', artifacts={},
                 graphics_proof=graphics_proof)
    writes = {}
    standalone = directory / (identity.split(':')[1] + '-ucode.img')
    if standalone.exists() or standalone.is_symlink(): paths.append(standalone)
    if len(paths) > graphics_boot.MAX_IMAGES:
        raise ValueError('Excessive combined CPU boot artifacts')
    originals = {}
    native_blobs = []
    for path in paths:
        target = path.relative_to(root).as_posix()
        checked_path = regular(root, target)
        if checked_path.stat().st_size > graphics_boot.MAX_IMAGE: raise ValueError('Target initramfs exceeds inspection limit')
        original = checked_path.read_bytes()
        originals[target] = original
        native = _native_member(original, member)
        if native and native not in native_blobs: native_blobs.append(native)
    if identity == 'microcode:amd':
        merged = _amd_merge(native_blobs + [cpu_data])
    else:
        merged = b''.join(native_blobs) + cpu_data
        _intel_valid(merged)
    fragment = graphics_boot._fragment({member: merged}, {})
    for path in paths:
        target = path.relative_to(root).as_posix()
        path = regular(root, target)
        if path.stat().st_size > graphics_boot.MAX_IMAGE: raise ValueError('Target initramfs exceeds inspection limit')
        original = originals[target]
        native = _native_member(original, member)
        base = graphics_writes.get(target, original)
        if not isinstance(base, bytes) or not base.startswith(original):
            raise ValueError('Graphics boot output does not preserve the original initramfs')
        updated = fragment + base
        if len(updated) > graphics_boot.MAX_IMAGE: raise ValueError('Updated initramfs exceeds size limit')
        writes[target] = updated
        proof['artifacts'][target] = dict(size=len(updated), sha256=_sha(updated), mode=path.stat().st_mode & 0o777,
            prefix_size=len(fragment), prefix_sha256=_sha(fragment), original_size=len(original),
            original_sha256=_sha(original), remainder_size=len(base), remainder_sha256=_sha(base),
            member=member, microcode_size=len(merged), microcode_sha256=_sha(merged), native_size=len(native), native_sha256=_sha(native))
    if set(graphics_writes) - set(writes): raise ValueError('Unexpected graphics boot output')
    return writes, proof


def verify(root, proof):
    if (not isinstance(proof, dict) or proof.get('schema') != 1 or proof.get('strategy') != STRATEGY
            or proof.get('identity') not in NAMES or proof.get('runtime') != 'not_validated'):
        raise ValueError('Invalid CPU early boot proof')
    artifacts = proof.get('artifacts')
    if not isinstance(artifacts, dict) or not 1 <= len(artifacts) <= graphics_boot.MAX_IMAGES:
        raise ValueError('Missing CPU early boot artifacts')
    for name, record in artifacts.items():
        if not re.fullmatch(r'boot/(?:initramfs-[A-Za-z0-9._+-]+|intel-ucode|amd-ucode)\.img', name): raise ValueError('Invalid initramfs target')
        data = regular(Path(root), name).read_bytes()
        size = record['prefix_size']
        if (len(data) != record['size'] or _sha(data) != record['sha256']
                or _sha(data[:size]) != record['prefix_sha256']
                or _sha(data[size:]) != record['remainder_sha256']
                or _sha(data[size:size + record['original_size']]) != record['original_sha256']):
            raise ValueError('CPU initramfs composition changed')
        entries = graphics_boot.inspect(data[:size])
        items = entries.get(record['member'], [])
        if (record['member'] != 'kernel/x86/microcode/' + NAMES[proof['identity']] or len(items) != 1
                or not stat.S_ISREG(items[0]['mode']) or items[0]['size'] != record['microcode_size']
                or items[0]['sha256'] != record['microcode_sha256']):
            raise ValueError('CPU early archive changed')
    return True
