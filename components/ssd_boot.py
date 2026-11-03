"""Pure renderer derived from public OXP3 boot_repairs.py.

Source: FlightDan/OneXPlayer3-SteamOS commit 72f864e0498c10366493c74608c5ffed62cc99ca,
SHA-256 3084c2cfceddd6e4545597a702290caeae063eaed1bdf345fdb291b3aa78ea8d.
See adaptations/common/scripts/licenses/upstream-fixes-MIT.txt. This module does
not select devices or write files; callers must establish trusted ownership.
"""
import re

PARAMETER = b'nvme.noacpi=1'
MAX_CONFIG = 1024 * 1024
_BOOT = re.compile(rb'^[ \t]*(?:steamenv_boot|linux|linuxefi)[ \t]+')
_KERNEL = re.compile(rb'(?:^|[ \t])[^ \t]*?/boot/vmlinuz-[^ \t]+(?:[ \t]|$)')


def classify_reviewed_device(bus, model):
    """Classify already authenticated facts; this function does not bind them.

    Missing or malformed facts are errors, never evidence of non-applicability.
    """
    if bus not in ('NVMe', 'SATA', 'USB', 'SD', 'MMC'):
        raise ValueError('Missing or unsupported reviewed SSD bus')
    if not isinstance(model, str) or not model.strip() or len(model) > 128:
        raise ValueError('Missing or invalid reviewed SSD model')
    if any(ord(c) < 32 or ord(c) > 126 for c in model):
        raise ValueError('Invalid reviewed SSD model characters')
    normalized = ' '.join(model.split()).casefold()
    return 'match' if bus == 'NVMe' and normalized == 'predator ssd gm7 1tb' else 'not_applicable'


def render_grub_config(original):
    """Preserve every byte except an added parameter on supported entries."""
    if not isinstance(original, bytes) or not original or len(original) > MAX_CONFIG:
        raise ValueError('Invalid EFI configuration size or type')
    if b'\0' in original or b'\r' in original.replace(b'\r\n', b''):
        raise ValueError('Malformed EFI configuration')
    original.decode('utf-8', 'strict')
    out = []
    matched = 0
    for line in original.splitlines(keepends=True):
        body = line.rstrip(b'\r\n')
        ending = line[len(body):]
        if body.lstrip().startswith(b'#'):
            out.append(line)
            continue
        if _BOOT.match(body) and b'/boot/vmlinuz-' in body:
            if not _KERNEL.search(body) or any(c in body for c in (b';', b'#', b'\\', b"'", b'"', b'`')):
                raise ValueError('Unsupported kernel boot entry')
            tuning = [word for word in body.split() if word.startswith((b'nvme.noacpi', b'nvme.quirks'))]
            if any(word != PARAMETER for word in tuning) or tuning.count(PARAMETER) > 1:
                raise ValueError('Conflicting NVMe tuning')
            if not tuning:
                body += b' ' + PARAMETER
            matched += 1
        elif b'nvme.noacpi' in body or b'nvme.quirks' in body or b'/boot/vmlinuz-' in body:
            raise ValueError('Unsupported kernel entry or NVMe tuning')
        out.append(body + ending)
    if not matched:
        raise ValueError('No supported Valve kernel boot entry')
    return b''.join(out)
