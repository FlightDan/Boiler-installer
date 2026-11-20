"""Standalone sleep preferences. Saving a preference never starts a sleep operation.

The fixed systemd-suspend.service drop-in calls run() only for an existing
system sleep request. No timer or background polling daemon is installed.
"""
import contextlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile

ROOT = Path('/')
POLICY = 'home/.boiler-s2s4/sleep-policy.json'
LOCK = 'run/lock/boiler-sleep.lock'
RUNTIME = 'run/systemd/sleep.conf.d/zz-boiler-sleep.conf'
ROUTER = 'etc/systemd/system/systemd-suspend.service.d/90-boiler-sleep.conf'
HELPER = 'usr/lib/holo/hibernate-swap-helper.sh'
SWAP_DIR = 'home/.boiler-s2s4'
SWAP_FILE = SWAP_DIR + '/hibernation.swapfile'
STATE = SWAP_DIR + '/swap-state.json'
NATIVE_METADATA = 'home/.hibernation.metadata'
RESERVE_BYTES = 500 * 1024 * 1024
SLEEP = 'usr/lib/systemd/systemd-sleep'
MODES = ('system', 's2idle', 'hibernate', 'auto')
DELAY_OPTIONS = (20, 60, 120, 720, 1440)
LEGACY_DELAYS = (15, 30, 180, 240)
DELAYS = DELAY_OPTIONS + LEGACY_DELAYS
DEFAULT = {'mode': 'system', 'delay_minutes': 120}
ROUTER_TEXT = '''# Boiler Sleep: route the existing suspend request.
[Service]
ExecStart=
ExecStart=/usr/bin/python3 -I -B /var/lib/boiler-sleep/sleep_control.py run
'''


def path(relative):
    return ROOT / relative


def validate(value):
    if (not isinstance(value, dict) or set(value) != set(DEFAULT)
            or value['mode'] not in MODES
            or type(value['delay_minutes']) is not int
            or value['delay_minutes'] not in DELAYS):
        raise ValueError('Invalid sleep mode or hibernation delay')
    return dict(value)


def trusted_file(target):
    info = target.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError('Sleep settings must be regular files')
    if os.name == 'posix' and (info.st_uid != 0 or info.st_mode & 0o022):
        raise RuntimeError('Sleep settings must be owned by root and not writable by other users')


def read_policy():
    owned_directory(missing_ok=True)
    target = path(POLICY)
    if not os.path.lexists(target):
        return dict(DEFAULT)
    trusted_file(target)
    return validate(json.loads(target.read_text(encoding='utf-8')))


def read_tokens(relative):
    try:
        return path(relative).read_text().replace('[', '').replace(']', '').split()
    except OSError:
        return []


def host_policy():
    """Read systemd's ordered, masked drop-in view; unknown means unavailable."""
    try:
        result = subprocess.run([str(path('usr/bin/systemd-analyze')), 'cat-config',
                                 'systemd/sleep.conf'], check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    values = {'AllowHibernation': True, 'AllowSuspendThenHibernate': True}
    section = ''
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith(('#', ';')):
            continue
        if line.startswith('[') and line.endswith(']'):
            section = line[1:-1].strip()
            continue
        if section != 'Sleep' or '=' not in line:
            continue
        key, value = (part.strip() for part in line.split('=', 1))
        if key not in values:
            continue
        value = value.lower()
        if value in ('1', 'yes', 'true', 'on'):
            values[key] = True
        elif value in ('0', 'no', 'false', 'off'):
            values[key] = False
        elif not value:
            values[key] = True
        else:
            return None
    return values


def capabilities():
    """Structural eligibility only; this does not verify a physical S4 resume."""
    import re
    reasons = {}
    if 's2idle' not in read_tokens('sys/power/mem_sleep'):
        reasons['s2idle'] = 's2idle_missing'
    failures = []
    try:
        cmdline = path('proc/cmdline').read_text().split()
    except OSError:
        cmdline = []
        failures.append('cmdline_unavailable')
    if any(x.startswith(('resume=', 'resume_offset=')) for x in cmdline):
        failures.append('external_resume')
    if 'nohibernate' in cmdline or 'noresume' in cmdline:
        failures.append('boot_disabled')
    if not supported_systemd():
        failures.append('systemd_unsupported')
    policy = host_policy()
    if policy is None:
        failures.append('policy_unknown')
    elif not policy['AllowHibernation']:
        failures.append('policy_disabled')
    if 'disk' not in read_tokens('sys/power/state'):
        failures.append('disk_state_missing')
    disk_modes = read_tokens('sys/power/disk')
    try:
        disk_raw = path('sys/power/disk').read_text().split()
    except OSError:
        disk_raw = []
    if not disk_modes or '[disabled]' in disk_raw or not any(x != 'disabled' for x in disk_modes):
        failures.append('disk_disabled')
    if os.path.lexists(path(NATIVE_METADATA)):
        failures.append('native_metadata')
    if filesystem_type() != 'ext4':
        failures.append('filesystem_unsupported')
    if not path('sys/firmware/efi').is_dir():
        failures.append('efi_required')
    efivars = path('sys/firmware/efi/efivars')
    try:
        if not efivars.is_dir():
            failures.append('efivars_unavailable')
        else:
            with os.scandir(efivars) as entries:
                if any(entry.name.startswith('HibernateLocation-') for entry in entries):
                    failures.append('pending_efi_location')
    except OSError:
        failures.append('efivars_unavailable')
    try:
        if (path('sys/power/resume').read_text().strip() != '0:0' or
                path('sys/power/resume_offset').read_text().strip() != '0'):
            failures.append('resume_configured')
    except OSError:
        failures.append('resume_unavailable')
    try:
        require_compatible_swaps(allow_owned=False)
    except (OSError, RuntimeError):
        failures.append('other_swap')
    try:
        resume = path('usr/lib/initcpio/hooks/resume').read_text()
    except OSError:
        resume = ''
    if not re.search(r'(?<![\w./-])(?:/usr/lib/systemd/)?systemd-hibernate-resume(?![\w./-])', resume):
        failures.append('resume_hook_missing')
    if failures:
        reasons['hibernate'] = failures
    auto_failures = list(failures)
    if policy is not None and not policy['AllowSuspendThenHibernate']:
        auto_failures.append('auto_policy_disabled')
    if 's2idle' in reasons:
        auto_failures.append('s2idle_missing')
    if not path('sys/class/rtc/rtc0').is_dir():
        auto_failures.append('rtc_missing')
    if auto_failures:
        reasons['auto'] = auto_failures
    modes = ['system'] + [m for m in ('s2idle', 'hibernate', 'auto') if m not in reasons]
    return modes, reasons


def available_modes():
    return capabilities()[0]


def status():
    policy = read_policy()
    modes, reasons = capabilities()
    try:
        trusted_file(path(ROUTER))
        trusted_file(path('var/lib/boiler-sleep/sleep_control.py'))
        installed = (path(ROUTER).read_text() == ROUTER_TEXT and
                     not os.path.lexists(path('etc/systemd/system/systemd-suspend.service.d/90-oxp3-battery.conf')))
    except (OSError, RuntimeError):
        installed = False
    state = read_swap_state()
    enabled = bool(state and state['enabled'] and owned_swap(state, missing_ok=True))
    swap_ready = enabled
    if enabled:
        try:
            swap_signature()
            if swap_active(): raise RuntimeError('Owned swap remains active')
            swap_location()
        except (OSError, RuntimeError, subprocess.CalledProcessError):
            swap_ready = False
            reasons.setdefault(policy['mode'], []).append('swap_unready')
    try:
        required_bytes = required_swap_bytes()
    except (OSError, RuntimeError, ValueError):
        required_bytes = None
    return {**policy, 'enabled': enabled, 'swap_state': state['phase'] if state else 'absent',
            'required_swap_bytes': required_bytes, 'reserve_bytes': RESERVE_BYTES,
            'available_modes': modes, 'router_installed': installed,
            'ready': installed and (not enabled or (policy['mode'] in modes and swap_ready)),
            'delay_options': list(DELAY_OPTIONS), 'hibernate_on_ac': False,
            'reasons': reasons, 'resume_verified': False}


@contextlib.contextmanager
def locked():
    import fcntl
    target = path(LOCK)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'w') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or (os.name == 'posix' and (info.st_uid != 0 or info.st_mode & 0o077)):
            raise RuntimeError('Untrusted sleep lock')
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('A sleep operation or settings change is in progress; try again after wake') from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def save(mode, delay_minutes):
    policy = validate({'mode': mode, 'delay_minutes': delay_minutes})
    with locked():
        current = status()
        if not current['router_installed']:
            raise RuntimeError('Activate the Boiler Sleep integration before changing sleep settings')
        if not current['enabled']:
            raise RuntimeError('Enable S4 before changing the sleep mode')
        if mode not in ('hibernate', 'auto') or mode not in current['available_modes']:
            raise RuntimeError('This sleep mode is unavailable on the current system')
        target = path(POLICY)
        target.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(target):
            trusted_file(target)
        fd, temporary = tempfile.mkstemp(prefix='.sleep-policy-', dir=target.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
                json.dump(policy, stream)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            if os.name == 'posix':
                directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return status()


def configuration(policy):
    text = '# Temporary Boiler Sleep settings; removed after wake.\n[Sleep]\n'
    if policy['mode'] in ('s2idle', 'auto'):
        text += 'SuspendState=mem\nMemorySleepMode=s2idle\n'
    if policy['mode'] == 'auto':
        text += (f"HibernateDelaySec={policy['delay_minutes']}min\n"
                 'HibernateOnACPower=no\n')
    return text


def command(relative, *args):
    subprocess.run([str(path(relative)), *args], check=True)


def owned_directory(missing_ok=False):
    home = path('home').lstat()
    if not stat.S_ISDIR(home.st_mode) or home.st_uid != 0 or home.st_mode & 0o022:
        raise RuntimeError('Untrusted /home directory')
    directory = path(SWAP_DIR)
    try:
        info = directory.lstat()
    except FileNotFoundError:
        if missing_ok: return False
        raise RuntimeError('Owned swap directory is missing')
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise RuntimeError('Untrusted owned swap directory')
    return True


def filesystem_uuid():
    try:
        result = subprocess.run([str(path('usr/bin/findmnt')), '-n', '-o', 'UUID',
                                 '-T', str(path('home'))], check=True, capture_output=True, text=True)
        value = result.stdout.strip()
        if not value or any(c.isspace() for c in value): raise RuntimeError('Missing filesystem UUID')
        return value
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError('Cannot determine filesystem UUID') from error


def supported_systemd():
    import re
    try:
        result = subprocess.run([str(path('usr/bin/systemd-analyze')), '--version'],
                                check=True, capture_output=True, text=True)
        return bool(re.match(r'^systemd\s+261(?:\s|\.|$)', result.stdout))
    except (OSError, subprocess.CalledProcessError):
        return False


def filesystem_type():
    try:
        result = subprocess.run([str(path('usr/bin/findmnt')), '-n', '-o', 'TARGET,FSTYPE',
                                 '-T', str(path('home'))], check=True, capture_output=True, text=True)
        fields = result.stdout.split()
        return fields[1] if len(fields) == 2 and fields[0] == '/home' else ''
    except (OSError, subprocess.CalledProcessError):
        return ''


def write_json(relative, data):
    target = path(relative)
    owned_directory()
    if os.path.lexists(target):
        trusted_file(target)
    fd, temporary = tempfile.mkstemp(prefix='.boiler-', dir=target.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def read_swap_state():
    owned_directory(missing_ok=True)
    target = path(STATE)
    if not os.path.lexists(target): return None
    trusted_file(target)
    state = json.loads(target.read_text())
    if (not isinstance(state, dict) or set(state) != {'filesystem_uuid', 'ino', 'enabled', 'phase'}
            or not isinstance(state['filesystem_uuid'], str) or not state['filesystem_uuid'] or type(state['ino']) is not int
            or type(state['enabled']) is not bool or state['phase'] not in ('creating', 'ready', 'cleaning')):
        raise ValueError('Invalid swap ownership state')
    return state


def owned_swap(state, missing_ok=False):
    if not owned_directory(missing_ok=missing_ok): return False
    try:
        info = path(SWAP_FILE).lstat()
    except FileNotFoundError:
        if missing_ok: return False
        raise RuntimeError('Owned swap file is missing')
    if (filesystem_type() != 'ext4' or filesystem_uuid() != state['filesystem_uuid']
            or not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077
            or info.st_nlink != 1 or info.st_ino != state['ino']):
        raise RuntimeError('Swap ownership, filesystem, or identity changed')
    return True


def swap_signature():
    page = os.sysconf('SC_PAGE_SIZE')
    with path(SWAP_FILE).open('rb') as stream:
        stream.seek(page - 10)
        signature = stream.read(10)
    if signature != b'SWAPSPACE2':
        raise RuntimeError('Swap signature is not clean; possible pending S4 image; inspect before retry')


def active_swaps():
    try:
        lines = path('proc/swaps').read_text().splitlines()[1:]
    except OSError as error:
        raise RuntimeError('Cannot inspect active swap') from error
    entries = []
    for line in lines:
        fields = line.split()
        if len(fields) != 5:
            raise RuntimeError('Cannot parse active swap')
        entries.append((fields[0], fields[1]))
    return entries


def swap_active():
    return any(name == str(path(SWAP_FILE)) for name, _ in active_swaps())


def require_compatible_swaps(allow_owned):
    import re
    owned = 0
    for name, kind in active_swaps():
        if kind == 'partition' and re.fullmatch(r'/dev/zram[0-9]+', name):
            continue
        if allow_owned and kind == 'file' and name == str(path(SWAP_FILE)):
            owned += 1
            continue
        raise RuntimeError('Another active disk swap prevents safe S4 selection')
    if allow_owned and owned != 1:
        raise RuntimeError('Owned swap is not the sole active disk swap')


def required_swap_bytes():
    import re
    match = re.search(r'^MemTotal:\s*(\d+)\s+kB\s*$', path('proc/meminfo').read_text(), re.M)
    if not match: raise RuntimeError('Cannot read physical memory size')
    total = int(match.group(1)) * 1024
    seen = set()
    for device in path('sys/class/drm').glob('card[0-9]*/device'):
        try:
            real = device.resolve(strict=True)
            if real in seen or (device / 'vendor').read_text().strip().lower() != '0x1002': continue
            seen.add(real)
            total += int((device / 'mem_info_vram_total').read_text().strip())
        except (OSError, ValueError):
            continue
    mib = 1024 * 1024
    return ((total + mib - 1) // mib) * mib


def swap_location():
    import re
    result = subprocess.run([str(path('usr/bin/filefrag')), '-v', str(path(SWAP_FILE))],
                            check=True, capture_output=True, text=True)
    block = re.search(r'\((?:\d+) blocks of (\d+) bytes\)', result.stdout)
    extent = re.search(r'^\s*0:\s*0\.\.\s*\d+:\s*(\d+)\.\.', result.stdout, re.M)
    if not block or int(block.group(1)) != os.sysconf('SC_PAGE_SIZE') or not extent:
        raise RuntimeError('Cannot determine ext4 page-sized swap offset')
    info = path(SWAP_FILE).stat()
    return f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}', int(extent.group(1))


def set_enabled(enabled):
    if type(enabled) is not bool: raise ValueError('Expected a boolean')
    with locked():
        current = status()
        state = read_swap_state()
        if enabled:
            if not current['router_installed']: raise RuntimeError('Activate the system integration first')
            if 'hibernate' not in current['available_modes']: raise RuntimeError('S4 is unavailable on this system')
            if state: raise RuntimeError('Owned swap state exists; disable or resolve it first')
            directory_exists = owned_directory(missing_ok=True)
            if os.path.lexists(path(SWAP_FILE)): raise RuntimeError('Unowned swap file already exists')
            uuid = filesystem_uuid()
            size = required_swap_bytes()
            free = os.statvfs(path('home')).f_bavail * os.statvfs(path('home')).f_frsize
            if free < size + RESERVE_BYTES: raise RuntimeError('Insufficient free space for swap and 500 MiB reserve')
            if not directory_exists: path(SWAP_DIR).mkdir(mode=0o700)
            owned_directory()
            fd = os.open(path(SWAP_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            info = path(SWAP_FILE).stat()
            state = {'filesystem_uuid': uuid, 'ino': info.st_ino, 'enabled': False, 'phase': 'creating'}
            try:
                write_json(STATE, state)
            except Exception:
                path(SWAP_FILE).unlink()
                if not directory_exists: path(SWAP_DIR).rmdir()
                raise
            command('usr/bin/dd', 'if=/dev/zero', 'of=' + str(path(SWAP_FILE)), 'bs=1M',
                    'count=' + str(size // (1024 * 1024)), 'conv=fsync')
            command('usr/bin/mkswap', str(path(SWAP_FILE)))
            owned_swap(state); swap_signature(); swap_location()
            command('usr/bin/swapon', str(path(SWAP_FILE)))
            if not swap_active(): raise RuntimeError('Swap activation was not confirmed')
            command('usr/bin/swapoff', str(path(SWAP_FILE)))
            if swap_active(): raise RuntimeError('Swap deactivation was not confirmed')
            mode = current['mode'] if current['mode'] in ('hibernate', 'auto') and current['mode'] in current['available_modes'] else ('auto' if 'auto' in current['available_modes'] else 'hibernate')
            write_json(POLICY, {'mode': mode, 'delay_minutes': current['delay_minutes']})
            state.update(enabled=True, phase='ready'); write_json(STATE, state)
        elif state:
            present = owned_swap(state, missing_ok=state['phase'] == 'cleaning')
            if not present:
                # Only our committed cleanup phase permits a missing file.
                if (filesystem_type() != 'ext4' or filesystem_uuid() != state['filesystem_uuid']
                        or swap_active()):
                    raise RuntimeError('Cannot confirm cleanup ownership or swap state')
                path(STATE).unlink()
            else:
                if state['phase'] in ('ready', 'cleaning'):
                    swap_signature()
                    try:
                        with os.scandir(path('sys/firmware/efi/efivars')) as entries:
                            if any(entry.name.startswith('HibernateLocation-') for entry in entries):
                                raise RuntimeError('EFI HibernateLocation exists; inspect before cleanup')
                    except OSError as error:
                        raise RuntimeError('Cannot inspect EFI HibernateLocation before cleanup') from error
                if swap_active():
                    command('usr/bin/swapoff', str(path(SWAP_FILE)))
                    if swap_active(): raise RuntimeError('Swap deactivation was not confirmed')
                owned_swap(state)
                state.update(enabled=False, phase='cleaning'); write_json(STATE, state)
                path(SWAP_FILE).unlink()
                path(STATE).unlink()

    return status()


def run():
    """Systemd entry point for an existing suspend request; never called by RPC."""
    with locked():
        try:
            policy = read_policy()
            state = read_swap_state()
        except (OSError, RuntimeError, ValueError):
            command(SLEEP, 'suspend'); return
        mode = policy['mode']
        if not state or not state['enabled'] or state['phase'] != 'ready' or mode not in ('hibernate', 'auto'):
            command(SLEEP, 'suspend'); return
        try:
            if mode not in available_modes(): raise RuntimeError('Mode is unavailable')
            owned_swap(state); swap_signature()
            if swap_active(): raise RuntimeError('Owned swap is unexpectedly active')
            swap_location()  # Validate the current page-unit extent before systemd selects it.
            require_compatible_swaps(allow_owned=False)
            restore = {name: path(name).read_text().strip() for name in ('sys/power/resume_offset', 'sys/power/resume')}
            if restore['sys/power/resume'] != '0:0' or restore['sys/power/resume_offset'] != '0':
                raise RuntimeError('A resume device is already configured')
            if mode == 'auto':
                selected = [x[1:-1] for x in path('sys/power/mem_sleep').read_text().split()
                            if x.startswith('[') and x.endswith(']')]
                if len(selected) != 1: raise RuntimeError('Cannot read memory sleep mode')
                restore['sys/power/mem_sleep'] = selected[0]
        except (OSError, RuntimeError, subprocess.CalledProcessError):
            command(SLEEP, 'suspend'); return
        runtime = path(RUNTIME)
        runtime.parent.mkdir(parents=True, exist_ok=True)
        active_attempted = False
        fallback = False
        stream = runtime.open('x', encoding='utf-8', newline='\n')
        try:
            with stream:
                stream.write(configuration(policy))
            owned_swap(state); swap_signature()
            active_attempted = True
            command('usr/bin/swapon', str(path(SWAP_FILE)))
            if not swap_active(): raise RuntimeError('Swap activation was not confirmed')
            try:
                require_compatible_swaps(allow_owned=True)
                if path('sys/power/resume').read_text().strip() != '0:0' or path('sys/power/resume_offset').read_text().strip() != '0':
                    raise RuntimeError('A resume device became configured')
            except (OSError, RuntimeError):
                fallback = True
            if not fallback:
                command(SLEEP, 'hibernate' if mode == 'hibernate' else 'suspend-then-hibernate')
        finally:
            restore_safe = True
            try:
                if active_attempted and swap_active():
                    owned_swap(state)
                    try:
                        swap_signature()
                    except (OSError, RuntimeError):
                        # Preserve resume parameters and active swap for inspection.
                        restore_safe = False
                        raise
                    command('usr/bin/swapoff', str(path(SWAP_FILE)))
                    if swap_active(): raise RuntimeError('Swap deactivation was not confirmed')
            finally:
                try:
                    if restore_safe:
                        for name, value in restore.items():
                            if path(name).read_text().strip() != value:
                                path(name).write_text(value + '\n')
                finally:
                    runtime.unlink()
        if fallback:
            command(SLEEP, 'suspend')



if __name__ == '__main__':
    import sys
    if sys.argv[1:] == ['status']:
        print(json.dumps(status()))
    elif sys.argv[1:] == ['run']:
        run()
    elif sys.argv[1:] == ['disable']:
        if os.geteuid() != 0: raise SystemExit('Run as root')
        print(json.dumps(set_enabled(False)))
    else:
        raise SystemExit('Usage: sleep_control.py status|run|disable')
