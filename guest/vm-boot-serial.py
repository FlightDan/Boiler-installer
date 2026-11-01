"""Instrument generated SteamOS GRUB entries on a disposable VM overlay only."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SUFFIX = b' console=ttyS0,115200n8 oxp3.serial-console=1'
BOOT = re.compile(rb'^[ \t]*steamenv_boot[ \t]+linux[ \t]+(/boot/vmlinuz-[A-Za-z0-9_.+-]+)(?:[ \t]+[^\r\n]*)?$')
COMMAND = re.compile(rb'^[ \t]*(?:steamenv_boot|linux|linuxefi|linux16)(?:[ \t]|$)')
LIMIT = 1024 * 1024
OVERLAY_SERIAL = 'boiler-vm-serial'


def validate_overlay(serial, readonly, size):
    if serial != OVERLAY_SERIAL or readonly != '0' or size != 80 * 1024**3:
        raise RuntimeError('Unexpected VM overlay: serial=' + repr(serial) + ' readonly=' + repr(readonly) + ' size=' + str(size))


def transform(data, kernels, root_uuid=None, filesystem_uuid=None):
    if len(data) > LIMIT or b'\x00' in data:
        raise ValueError('Invalid or oversized GRUB config')
    output, count, last_root = [], 0, None
    for line in data.splitlines(keepends=True):
        body = line.rstrip(b'\r\n')
        ending = line[len(body):]
        search = re.fullmatch(rb'[ \t]*search[ \t]+--no-floppy[ \t]+--(part-uuid|fs-uuid)[ \t]+--set=root[ \t]+([a-zA-Z0-9-]+)[ \t]*', body)
        if search:
            last_root = (search.group(1), search.group(2).lower())
        match = BOOT.fullmatch(body)
        if match:
            if not ending or match.group(1).decode('ascii') not in kernels:
                raise ValueError('Missing newline or referenced kernel')
            if root_uuid is not None:
                identities = {b'part-uuid': root_uuid.lower()}
                if filesystem_uuid is not None:
                    identities[b'fs-uuid'] = filesystem_uuid.lower()
                if last_root is None or identities.get(last_root[0]) != last_root[1]:
                    raise ValueError('Boot entry does not reference its slot root')
            tokens = body.split()
            serial = [t for t in tokens if t.startswith(b'console=ttyS0') or t.startswith(b'oxp3.serial-console=')]
            if serial and not (serial == SUFFIX.split() and body.endswith(SUFFIX)):
                raise ValueError('Conflicting serial options')
            if not serial:
                body += SUFFIX
            count += 1
        elif COMMAND.match(body):
            raise ValueError('Unsupported boot command')
        output.append(body + ending)
    if not count:
        raise ValueError('No supported SteamOS boot entries')
    return b''.join(output), count


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def run():
    if Path('/sys/class/dmi/id/sys_vendor').read_text().strip() != 'QEMU':
        raise RuntimeError('Managed QEMU required')
    disk = '/dev/vdc'
    validate_overlay(Path('/sys/block/vdc/serial').read_text().strip(), command('blockdev', '--getro', disk), int(command('blockdev', '--getsize64', disk)))
    table = json.loads(command('sfdisk', '--json', disk))['partitiontable']
    rows = table['partitions']
    required = ('esp', 'efi-A', 'efi-B', 'rootfs-A', 'rootfs-B', 'var-A', 'var-B', 'home')
    if table.get('label') != 'gpt' or any(sum(p.get('name') == name for p in rows) != 1 for name in required):
        raise RuntimeError('Unexpected SteamOS GPT layout')
    parts = {p['name']: p for p in rows if p.get('name') in required}
    if any(not re.fullmatch(r'/dev/vdc[0-9]+', p['node']) for p in parts.values()):
        raise RuntimeError('Unexpected partition device')
    mounts, prepared, receipt = [], {}, {}
    try:
        for slot in ('A', 'B'):
            root = Path('/mnt/vm-serial-root-' + slot)
            efi = Path('/mnt/vm-serial-efi-' + slot)
            root.mkdir(parents=True, exist_ok=True); efi.mkdir(parents=True, exist_ok=True)
            fs = command('blkid', '-s', 'TYPE', '-o', 'value', parts['rootfs-' + slot]['node'])
            if fs not in ('btrfs', 'ext4'):
                raise RuntimeError('Unsupported root filesystem')
            subprocess.run(['mount', '-t', fs, '-o', 'ro,rescue=nologreplay' if fs == 'btrfs' else 'ro,noload', parts['rootfs-' + slot]['node'], str(root)], check=True)
            mounts.append(root)
            subprocess.run(['mount', '-t', 'vfat', parts['efi-' + slot]['node'], str(efi)], check=True)
            mounts.append(efi)
            path = efi / 'EFI/steamos/grub.cfg'
            with path.open('rb') as stream:
                before = stream.read(LIMIT + 1)
            # Generated 3.8/3.10 configs select the mounted slot by FSUUID or PARTUUID.
            root_uuid = parts['rootfs-' + slot]['uuid'].lower().encode('ascii')
            filesystem_uuid = command('blkid', '-s', 'UUID', '-o', 'value', parts['rootfs-' + slot]['node']).lower().encode('ascii')
            kernels = {'/boot/' + p.name for p in (root / 'boot').glob('vmlinuz-*') if p.is_file()}
            after, count = transform(before, kernels, root_uuid, filesystem_uuid)
            prepared[slot] = (path, before, after, count)
        # Validate both preimages before changing either EFI partition.
        for slot in ('A', 'B'):
            path, before, after, count = prepared[slot]
            if before != after:
                with path.open('wb') as stream:
                    stream.write(after); stream.flush(); os.fsync(stream.fileno())
            if path.read_bytes() != after:
                raise RuntimeError('GRUB readback mismatch')
            receipt[slot] = {'path': 'efi-' + slot + '/EFI/steamos/grub.cfg', 'before_sha256': hashlib.sha256(before).hexdigest(), 'after_sha256': hashlib.sha256(after).hexdigest(), 'entries': count}
        subprocess.run(['sync'], check=True)
    finally:
        original, errors = sys.exc_info()[1], []
        for mount in reversed(mounts):
            try:
                subprocess.run(['umount', str(mount)], check=True)
            except Exception as error:
                errors.append(str(mount) + ': ' + str(error))
        if errors:
            raise RuntimeError('Unmount failed: ' + '; '.join(errors)) from original
    print('OXP_VM_SERIAL_RECEIPT=' + json.dumps(receipt, sort_keys=True, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    run()
