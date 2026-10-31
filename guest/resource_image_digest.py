"""Read-only consume-point image commitment; fixed owned QEMU image only."""
import hashlib
import hmac
import os
from pathlib import Path
import re
import stat
import time


def verify_prepared_stream(stream, expected_size, expected_sha256, *, progress=None):
    if (type(expected_size) is not int or expected_size <= 0 or not isinstance(expected_sha256, str)
            or not re.fullmatch('[0-9a-f]{64}', expected_sha256)):
        raise RuntimeError('Invalid prepared image byte commitment')
    started = time.monotonic(); digest = hashlib.sha256(); remaining = expected_size
    last = started
    if progress is not None: progress(0, expected_size, False)
    while remaining:
        block = stream.read(min(8 * 1024 * 1024, remaining))
        if not block: raise RuntimeError('Prepared image ended before its committed size')
        digest.update(block); remaining -= len(block)
        now = time.monotonic()
        if progress is not None and now - last >= 1:
            progress(expected_size - remaining, expected_size, False); last = now
    if stream.read(1): raise RuntimeError('Prepared image exceeds its committed size')
    if not hmac.compare_digest(digest.hexdigest(), expected_sha256):
        raise RuntimeError('Prepared image bytes differ from the host-committed preparation receipt')
    if progress is not None: progress(expected_size, expected_size, True)
    return time.monotonic() - started


def verify_fixed_image(expected_size, expected_sha256, *, readonly, progress=None):
    if Path('/sys/class/dmi/id/sys_vendor').read_text().strip() != 'QEMU':
        raise RuntimeError('Prepared image byte verification requires the managed guest')
    serials = {p.read_text().strip() for p in (Path('/sys/class/block/vdc/serial'),
        Path('/sys/class/block/vdc/device/serial')) if p.is_file()}
    if (serials != {'boiler-image'} or Path('/sys/class/block/vdc/ro').read_text().strip() != ('1' if readonly else '0')
            or int(Path('/sys/class/block/vdc/size').read_text()) * 512 != expected_size):
        raise RuntimeError('Prepared image consume-point identity/protection differs')
    descriptor = os.open('/dev/vdc', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        device = Path('/sys/class/block/vdc/dev').read_text().strip()
        if not stat.S_ISBLK(info.st_mode) or device != str(os.major(info.st_rdev)) + ':' + str(os.minor(info.st_rdev)):
            raise RuntimeError('Unexpected prepared image device handle')
        return verify_prepared_stream(stream, expected_size, expected_sha256, progress=progress)


if __name__ == '__main__':
    import sys
    elapsed = verify_fixed_image(int(sys.argv[2]), sys.argv[1], readonly=True)
    print('OXP_RESOURCE_IMAGE_DIGEST=verified:seconds=' + format(elapsed, '.6f'), flush=True)
