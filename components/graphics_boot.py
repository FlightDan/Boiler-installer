"""Add selected firmware to initramfs without rebuilding the image's drivers.

Only return transaction inputs; never write the mounted target. Linux accepts
concatenated compressed/uncompressed newc archives, so the original byte stream
(including microcode, modules, hooks, and metadata) remains an exact prefix.
https://www.kernel.org/doc/html/latest/driver-api/early-userspace/buffer-format.html
"""
import bz2
import ctypes
import ctypes.util
import hashlib
import lzma
from pathlib import Path, PurePosixPath
import re
import stat
import zlib

from payload_lock import regular, relative

MAX_IMAGE = 128 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
MAX_FRAGMENT = 64 * 1024 * 1024
MAX_ENTRIES = 200000
MAX_IMAGES = 16
STRATEGY = 'append-newc-preserve-original-v1'


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _zstd(data, maximum):
    # Load the installation runtime's library, never one from the mounted OS.
    name = ctypes.util.find_library('zstd')
    if not name:
        name = next((value for value in ('/usr/lib/libzstd.so.1', '/lib/libzstd.so.1') if Path(value).is_file()), None)
    if not name:
        raise ValueError('Runtime libzstd is required to inspect this initramfs')
    library = ctypes.CDLL(name)
    class Buffer(ctypes.Structure):
        _fields_ = [('data', ctypes.c_void_p), ('size', ctypes.c_size_t), ('pos', ctypes.c_size_t)]
    library.ZSTD_createDStream.restype = ctypes.c_void_p
    library.ZSTD_freeDStream.argtypes = [ctypes.c_void_p]
    library.ZSTD_isError.argtypes = [ctypes.c_size_t]
    library.ZSTD_isError.restype = ctypes.c_uint
    library.ZSTD_DCtx_setParameter.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    library.ZSTD_DCtx_setParameter.restype = ctypes.c_size_t
    library.ZSTD_decompressStream.argtypes = [ctypes.c_void_p, ctypes.POINTER(Buffer), ctypes.POINTER(Buffer)]
    library.ZSTD_decompressStream.restype = ctypes.c_size_t
    context = library.ZSTD_createDStream()
    if not context:
        raise ValueError('Cannot allocate initramfs decompressor')
    try:
        # ZSTD_d_windowLogMax=100; cap decoder window allocation as well as output.
        if library.ZSTD_isError(library.ZSTD_DCtx_setParameter(context, 100, 27)):
            raise ValueError('Cannot bound initramfs decompressor window')
        source = ctypes.create_string_buffer(data)
        incoming = Buffer(ctypes.cast(source, ctypes.c_void_p), len(data), 0)
        output = []
        size = 0
        while True:
            block = ctypes.create_string_buffer(min(65536, maximum - size + 1))
            outgoing = Buffer(ctypes.cast(block, ctypes.c_void_p), len(block), 0)
            before = incoming.pos
            result = library.ZSTD_decompressStream(context, ctypes.byref(outgoing), ctypes.byref(incoming))
            if library.ZSTD_isError(result):
                raise ValueError('Invalid zstd initramfs frame')
            size += outgoing.pos
            if size > maximum:
                raise ValueError('Decoded initramfs exceeds inspection limit')
            output.append(block.raw[:outgoing.pos])
            if result == 0:
                return b''.join(output), incoming.pos
            if incoming.pos == before and not outgoing.pos:
                raise ValueError('Truncated zstd initramfs frame')
    finally:
        library.ZSTD_freeDStream(context)


def _decompress(data, maximum):
    if data.startswith(b'\x28\xb5\x2f\xfd'):
        return _zstd(data, maximum)
    if data.startswith(b'\x1f\x8b'):
        decoder = zlib.decompressobj(31)
    elif data.startswith(b'\xfd7zXZ\x00'):
        decoder = lzma.LZMADecompressor(memlimit=MAX_EXPANDED)
    elif data.startswith(b'BZh'):
        decoder = bz2.BZ2Decompressor()
    else:
        raise ValueError('Unsupported initramfs compression or trailing data')
    try:
        expanded = decoder.decompress(data, maximum + 1)
    except (OSError, EOFError, lzma.LZMAError, zlib.error) as error:
        raise ValueError('Invalid compressed initramfs') from error
    if len(expanded) > maximum:
        raise ValueError('Decoded initramfs exceeds inspection limit')
    if not decoder.eof:
        raise ValueError('Truncated or oversized compressed initramfs')
    return expanded, len(data) - len(decoder.unused_data)


def _name(raw, mode):
    if not raw or raw[-1:] != b'\0' or b'\0' in raw.rstrip(b'\0'):
        raise ValueError('Invalid initramfs pathname')
    try:
        name = raw.rstrip(b'\0').decode('utf-8')
    except UnicodeDecodeError as error:
        raise ValueError('Invalid initramfs pathname encoding') from error
    while name.startswith('./'):
        name = name[2:]
    if stat.S_ISDIR(mode):
        name = name.rstrip('/')
    if name in ('', '.'):
        return ''
    return relative(name).as_posix()


def _cpio(data, start, entries):
    offset = start
    count = sum(map(len, entries.values()))
    while offset < len(data):
        if data[offset:offset + 6] not in (b'070701', b'070702'):
            raise ValueError('Invalid newc initramfs header')
        header = data[offset:offset + 110]
        if len(header) != 110 or not re.fullmatch(b'[0-9a-fA-F]{104}', header[6:]):
            raise ValueError('Invalid newc initramfs fields')
        values = [int(header[pos:pos + 8], 16) for pos in range(6, 110, 8)]
        inode, mode, uid, gid, links, mtime, size, major, minor, rmajor, rminor, namesize, checksum = values
        if not 1 <= namesize <= 4096 or size > MAX_EXPANDED:
            raise ValueError('Initramfs entry exceeds inspection limit')
        name_end = offset + 110 + namesize
        data_start = (name_end + 3) & ~3
        data_end = data_start + size
        end = (data_end + 3) & ~3
        if end > len(data) or name_end > len(data):
            raise ValueError('Truncated newc initramfs entry')
        name = _name(data[offset + 110:name_end], mode)
        content = data[data_start:data_end]
        if header[:6] == b'070702' and (sum(content) & 0xffffffff) != checksum:
            raise ValueError('Initramfs entry checksum differs')
        if name == 'TRAILER!!!':
            if size:
                raise ValueError('Invalid initramfs trailer')
            return end
        if name:
            if count >= MAX_ENTRIES:
                raise ValueError('Initramfs entry count exceeds inspection limit')
            item = dict(mode=mode, links=links, size=size, sha256=_sha(content))
            if stat.S_ISLNK(mode):
                if size > 4096:
                    raise ValueError('Initramfs link exceeds inspection limit')
                try:
                    item['link'] = content.decode('utf-8')
                except UnicodeDecodeError as error:
                    raise ValueError('Invalid initramfs link encoding') from error
            entries.setdefault(name, []).append(item)
        offset = end
        count += 1
        if count > MAX_ENTRIES:
            raise ValueError('Initramfs entry count exceeds inspection limit')
    raise ValueError('Initramfs archive requires a trailer')


def inspect(data):
    """Bounded, extraction-free inspection of mixed initramfs archive members."""
    if not data or len(data) > MAX_IMAGE:
        raise ValueError('Initramfs size exceeds inspection limit or is empty')
    entries, offset, expanded_size, members = {}, 0, 0, 0
    while offset < len(data):
        if data[offset] == 0:
            offset += 1
            continue
        members += 1
        if members > 64:
            raise ValueError('Initramfs member count exceeds inspection limit')
        if data[offset:offset + 6] in (b'070701', b'070702'):
            end = _cpio(data, offset, entries)
            expanded_size += end - offset
            offset = end
        else:
            expanded, consumed = _decompress(data[offset:], MAX_EXPANDED - expanded_size)
            expanded_size += len(expanded)
            inner = 0
            while inner < len(expanded):
                if expanded[inner] == 0:
                    inner += 1
                    continue
                inner = _cpio(expanded, inner, entries)
            offset += consumed
        if expanded_size > MAX_EXPANDED:
            raise ValueError('Decoded initramfs exceeds inspection limit')
    if not entries:
        raise ValueError('Initramfs contains no filesystem entries')
    return entries


def _entry(name, data, mode, inode):
    raw = name.encode('utf-8') + b'\0'
    values = (inode, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(raw), 0)
    result = b'070701' + ''.join(f'{value:08x}' for value in values).encode('ascii') + raw
    result += b'\0' * (-len(result) % 4)
    result += data
    return result + b'\0' * (-len(data) % 4)


def _fragment(files, entries):
    parents = set()
    for name in files:
        path = PurePosixPath(name)
        parents.update(parent.as_posix() for parent in path.parents if parent.as_posix() != '.')
    records = []
    for parent in sorted(parents, key=lambda value: (value.count('/'), value)):
        existing = entries.get(parent, [])
        if existing and any(not stat.S_ISDIR(item['mode']) for item in existing):
            raise ValueError('Unsafe initramfs firmware parent: ' + parent)
        if not existing:
            records.append((parent, b'', stat.S_IFDIR | 0o755))
    records.extend((name, data, stat.S_IFREG | 0o644) for name, data in sorted(files.items()))
    records.append(('TRAILER!!!', b'', 0))
    fragment = b''.join(_entry(name, data, mode, index) for index, (name, data, mode) in enumerate(records, 1))
    if len(fragment) > MAX_FRAGMENT:
        raise ValueError('Firmware initramfs fragment exceeds size limit')
    return fragment


def _firmware_prefix(entries):
    # Support the ordinary merged-usr layout and legacy /lib directory. Other
    # namespace aliases are refused rather than risking extraction via a link.
    lib = entries.get('lib', [])
    # mkinitcpio can repeat the same merged-usr link in early and main CPIO.
    # Only identical conventional aliases are safe; firmware-parent aliases
    # remain forbidden and no existing member is rewritten.
    if lib and all(item == lib[0] for item in lib) and stat.S_ISLNK(lib[0]['mode']) and lib[0].get('link') in ('usr/lib', '/usr/lib'):
        return 'usr/lib/firmware/'
    if lib and all(stat.S_ISDIR(item['mode']) for item in lib):
        return 'lib/firmware/'
    raise ValueError('Unsupported initramfs /lib firmware layout')


def prepare(root, profile, candidate_writes):
    """Return boot writes and preservation proof for the component transaction.

    candidate_writes contains all selected firmware, including preserved native
    compressed variants, with already verified bytes. The caller must place
    returned boot writes in that same component
    transaction. No target files are created or changed here.
    """
    if not isinstance(profile, str) or not re.fullmatch(r'graphics:(?:amd|intel)-[a-z0-9-]{1,80}', profile):
        raise ValueError('Invalid graphics early-boot profile')
    selected = {}
    for name, data in candidate_writes.items():
        name = relative(name).as_posix()
        prefixes = ('usr/lib/firmware/amdgpu/',) if profile.startswith('graphics:amd-') else ('usr/lib/firmware/xe/', 'usr/lib/firmware/i915/')
        if not name.startswith(prefixes) or not isinstance(data, bytes) or not data:
            raise ValueError('Invalid selected early-boot firmware candidate')
        selected[name.removeprefix('usr/lib/firmware/')] = data
    if len(selected) > 4096 or sum(map(len, selected.values())) > MAX_FRAGMENT:
        raise ValueError('Selected early-boot firmware exceeds inspection limit')
    proof = dict(schema=1, strategy=STRATEGY, profile=profile, runtime='not_validated', artifacts={})
    if not selected:
        raise ValueError('Graphics early-boot candidates are missing')
    root = Path(root)
    directory = root / 'boot'
    if directory.is_symlink() or not directory.is_dir() or (hasattr(directory, 'is_junction') and directory.is_junction()):
        raise ValueError('Missing or unsafe target boot directory')
    paths = sorted(path for path in directory.iterdir() if re.fullmatch(r'initramfs-[A-Za-z0-9._+-]+\.img', path.name))
    if not paths or len(paths) > MAX_IMAGES:
        raise ValueError('Missing or excessive target initramfs images')
    writes = {}
    for path in paths:
        target = path.relative_to(root).as_posix()
        path = regular(root, target)
        if path.stat().st_size > MAX_IMAGE:
            raise ValueError('Target initramfs exceeds inspection limit')
        original = path.read_bytes()
        entries = inspect(original)
        prefix = _firmware_prefix(entries)
        additions, preserved = {}, {}
        for name, data in sorted(selected.items()):
            destination = prefix + name
            stem = destination
            for suffix in ('.xz', '.zst', '.gz'):
                if stem.endswith(suffix):
                    stem = stem[:-len(suffix)]
                    break
            for parent in PurePosixPath(destination).parents:
                if parent.as_posix() == '.': continue
                if any(not stat.S_ISDIR(item['mode']) for item in entries.get(parent.as_posix(), [])):
                    raise ValueError('Unsafe initramfs firmware parent: ' + parent.as_posix())
            existing = []
            for suffix in ('', '.xz', '.zst', '.gz'):
                member = stem + suffix
                records = entries.get(member, [])
                if records:
                    if (any(item != records[0] for item in records) or not stat.S_ISREG(records[0]['mode'])
                            or records[0]['links'] > 1 or not records[0]['size']):
                        raise ValueError('Unsafe or duplicate initramfs firmware member: ' + member)
                    existing.append((member, records[0]))
            if existing:
                preserved.update(existing)
            else:
                additions[destination] = data
        fragment = _fragment(additions, entries) if additions else b''
        # An uncompressed CPIO header must start on a four-byte boundary in the
        # complete initramfs buffer, including after a compressed member.
        padding = b'\0' * (-len(original) % 4) if fragment else b''
        updated = original + padding + fragment
        if len(updated) > MAX_IMAGE:
            raise ValueError('Updated initramfs exceeds size limit')
        if fragment:
            writes[target] = updated
        proof['artifacts'][target] = dict(original_size=len(original), original_sha256=_sha(original),
            size=len(updated), sha256=_sha(updated), mode=path.stat().st_mode & 0o777,
            fragment_size=len(fragment), fragment_sha256=_sha(fragment), padding_size=len(padding),
            included={name: dict(size=len(data), sha256=_sha(data)) for name, data in additions.items()},
            preserved=preserved)
    proof['status'] = 'firmware-appended-original-preserved' if writes else 'existing-initramfs-firmware-preserved'
    return writes, proof


def verify(root, proof):
    """Verify committed boot files and their exact original byte prefixes."""
    if proof.get('schema') != 1 or proof.get('strategy') != STRATEGY or proof.get('runtime') != 'not_validated':
        raise ValueError('Invalid graphics early-boot proof')
    artifacts = proof.get('artifacts')
    if not isinstance(artifacts, dict) or not 1 <= len(artifacts) <= MAX_IMAGES:
        raise ValueError('Missing graphics early-boot proof artifacts')
    for name, record in artifacts.items():
        if not re.fullmatch(r'boot/initramfs-[A-Za-z0-9._+-]+\.img', name):
            raise ValueError('Invalid graphics early-boot proof target')
        path = regular(root, name)
        if path.stat().st_size != record['size'] or path.stat().st_size > MAX_IMAGE:
            raise ValueError('Graphics initramfs size changed')
        data = path.read_bytes()
        if _sha(data) != record['sha256'] or _sha(data[:record['original_size']]) != record['original_sha256']:
            raise ValueError('Graphics initramfs or original prefix changed')
        entries = inspect(data)
        for member, expected in record['included'].items():
            observed = entries.get(member, [])
            if len(observed) != 1 or any(observed[0][key] != expected[key] for key in ('size', 'sha256')):
                raise ValueError('Graphics early-boot firmware changed')
    return True
