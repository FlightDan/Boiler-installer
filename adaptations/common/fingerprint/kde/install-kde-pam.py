#!/usr/bin/env python3
"""Install the pam_fprintd module used by the Plasma lock-screen authenticator.

The kscreenlocker package already installs /usr/lib/pam.d/kde-fingerprint, whose
stack calls pam_fprintd.so. SteamOS does not preinstall the Arch fprintd
package that provides that module, so the Plasma non-interactive authenticator
stays unavailable. This tool installs the single missing module and nothing
else; it never rewrites a PAM service file.

The module is a D-Bus client for net.reactivated.Fprint. It does not link
libfprint and does not select a driver, so it works with any service that owns
that bus name, including the Boiler boiler-fprintd.service.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

MODULE = 'pam_fprintd.so'
SECURITY_DIR = Path('/usr/lib/security')
PAM_DIR = Path('/usr/lib/pam.d')
DESTINATION = SECURITY_DIR / MODULE
SERVICE = PAM_DIR / 'kde-fingerprint'
RECORD = Path('/var/lib/boiler-fingerprint/kde-pam.json')


class Refused(Exception):
    pass


def euid():
    """Effective uid; a test hook on platforms without geteuid."""
    getter = getattr(os, 'geteuid', None)
    return getter() if getter is not None else -1


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _security_stat(path):
    """Stat hook so ownership checks stay testable on non-POSIX hosts."""
    return path.lstat()


def run_bytes(*args):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.returncode, result.stdout, result.stderr


def inspect_package(package):
    """Return the module bytes from an explicit package archive.

    GNU tar auto-detects the compression, so the same path handles the .zst
    packages the distribution publishes and an uncompressed fixture archive.
    """
    archive = Path(package)
    if archive.is_symlink() or not archive.is_file():
        raise Refused('Expected a regular package archive')
    member = 'usr/lib/security/' + MODULE
    code, listing, _ = run_bytes('tar', '-tf', str(archive))
    if code:
        raise Refused('Cannot list the package archive')
    names = [line for line in listing.decode('utf-8', 'surrogateescape').splitlines() if line]
    if member not in names:
        raise Refused('Package does not contain ' + member)
    code, data, _ = run_bytes('tar', '-xOf', str(archive), member)
    if code:
        raise Refused('Cannot extract ' + member)
    return data


def check_module(data):
    if not data.startswith(b'\x7fELF'):
        raise Refused('Module is not an ELF object')
    if len(data) < 20 or data[4:6] != b'\x02\x01' or int.from_bytes(data[18:20], 'little') != 62:
        raise Refused('Module is not x86-64 ELF')
    return {'size': len(data), 'sha256': sha(data)}


def preflight():
    if euid() != 0:
        raise Refused('Run as root')
    for directory in (SECURITY_DIR, PAM_DIR):
        info = _security_stat(directory)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise Refused('Unsafe directory: ' + str(directory))
    if SERVICE.is_symlink() or not SERVICE.is_file():
        raise Refused('kscreenlocker does not install ' + str(SERVICE))
    if MODULE not in SERVICE.read_text(encoding='utf-8'):
        raise Refused(str(SERVICE) + ' does not call ' + MODULE)


def read_record():
    if not RECORD.exists():
        return None
    if RECORD.is_symlink() or not RECORD.is_file():
        raise Refused('Ownership record is not a regular file')
    try:
        data = json.loads(RECORD.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise Refused('Invalid ownership record') from exc
    if data.get('format') != 1 or not isinstance(data.get('sha256'), str) or len(data['sha256']) != 64:
        raise Refused('Invalid ownership record')
    return data


def write_record(data):
    RECORD.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    RECORD.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def install(package):
    preflight()
    data = inspect_package(package)
    facts = check_module(data)
    current = read_record()
    if DESTINATION.exists() or DESTINATION.is_symlink():
        if DESTINATION.is_symlink() or not DESTINATION.is_file():
            raise Refused('Refusing to replace a non-regular ' + str(DESTINATION))
        existing = DESTINATION.read_bytes()
        if current is None:
            raise Refused('Module already exists and was not installed by this tool: ' + str(DESTINATION))
        if sha(existing) != current['sha256']:
            raise Refused('Installed module changed since it was recorded; refusing to overwrite')
    handle, temporary = tempfile.mkstemp(prefix='.pam-fprintd-', dir=str(DESTINATION.parent))
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(data)
        os.chmod(temporary, 0o755)
        os.replace(temporary, DESTINATION)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    write_record({'format': 1, 'sha256': facts['sha256'], 'size': facts['size'],
                  'source_package': Path(package).name})
    return facts


def check():
    preflight()
    current = read_record()
    if current is None:
        print('pam_fprintd is not installed by this tool')
        return 1
    if not DESTINATION.is_file() or sha(DESTINATION.read_bytes()) != current['sha256']:
        print('Installed pam_fprintd differs from the recorded digest')
        return 1
    print('pam_fprintd matches the recorded digest')
    return 0


def uninstall():
    if euid() != 0:
        raise Refused('Run as root')
    current = read_record()
    if current is None:
        raise Refused('pam_fprintd is not installed by this tool')
    if DESTINATION.is_file() and sha(DESTINATION.read_bytes()) != current['sha256']:
        raise Refused('Installed module changed; leaving it in place')
    if DESTINATION.exists() or DESTINATION.is_symlink():
        DESTINATION.unlink()
    RECORD.unlink()
    print('Removed pam_fprintd')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', choices=('install', 'check', 'uninstall'), default='install')
    parser.add_argument('--package', help='explicit fprintd package archive providing usr/lib/security/pam_fprintd.so')
    args = parser.parse_args()
    try:
        if args.action == 'install':
            if not args.package:
                raise Refused('install requires --package with a reviewed fprintd archive')
            print(json.dumps(install(args.package), indent=2))
        elif args.action == 'check':
            return check()
        else:
            uninstall()
    except (Refused, OSError) as exc:
        print('Error: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
