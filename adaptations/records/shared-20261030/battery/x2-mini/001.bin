"""Battery sleep preferences. Saving a preference never starts a sleep operation.

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
POLICY = 'var/lib/oxp3-battery/sleep-policy.json'
LOCK = 'run/lock/oxp3-battery-sleep.lock'
RUNTIME = 'run/systemd/sleep.conf.d/zz-oxp3-battery.conf'
ROUTER = 'etc/systemd/system/systemd-suspend.service.d/90-oxp3-battery.conf'
HELPER = 'usr/lib/holo/hibernate-swap-helper.sh'
SLEEP = 'usr/lib/systemd/systemd-sleep'
MODES = ('system', 's2idle', 'hibernate', 'auto')
DELAY_OPTIONS = (20, 60, 120, 720, 1440)
LEGACY_DELAYS = (15, 30, 180, 240)
DELAYS = DELAY_OPTIONS + LEGACY_DELAYS
DEFAULT = {'mode': 'system', 'delay_minutes': 120}
ROUTER_TEXT = '''# OXP3 Battery: keep the existing sleep request and select its operation.
[Service]
ExecStart=
ExecStart=/usr/bin/python3 -I -B /var/lib/oxp3-battery/sleep_control.py run
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


def available_modes():
    result = ['system']
    if 's2idle' in read_tokens('sys/power/mem_sleep'):
        result.append('s2idle')
    helper = path(HELPER)
    # This adapter deliberately uses the SteamOS temporary-swap preparation
    # and installed EFI-capable resume hook, rather than inventing a new one.
    try:
        resume = path('usr/lib/initcpio/hooks/resume').read_text()
    except OSError:
        resume = ''
    hibernate = ('disk' in read_tokens('sys/power/state')
                 and helper.is_file() and os.access(helper, os.X_OK)
                 and 'systemd-hibernate-resume' in resume
                 and (path('sys/firmware/efi').is_dir()
                      or 'resume=' in ' '.join(read_tokens('proc/cmdline'))))
    if hibernate:
        result.append('hibernate')
        if 's2idle' in result and path('sys/class/rtc/rtc0').is_dir():
            result.append('auto')
    return result


def status():
    policy = read_policy()
    modes = available_modes()
    try:
        installed = path(ROUTER).read_text() == ROUTER_TEXT
    except OSError:
        installed = False
    return {**policy, 'available_modes': modes, 'router_installed': installed,
            'ready': installed and policy['mode'] in modes,
            'delay_options': list(DELAY_OPTIONS), 'hibernate_on_ac': False}


@contextlib.contextmanager
def locked():
    import fcntl
    target = path(LOCK)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'w') as stream:
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
            raise RuntimeError('Update the Battery sleep integration before changing sleep settings')
        if mode not in current['available_modes']:
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
    text = '# Temporary OXP3 Battery sleep settings; removed after wake.\n[Sleep]\n'
    if policy['mode'] in ('s2idle', 'auto'):
        text += 'SuspendState=mem\nMemorySleepMode=s2idle\n'
    if policy['mode'] in ('hibernate', 'auto'):
        text += 'AllowHibernation=yes\n'
    if policy['mode'] == 'auto':
        text += ('AllowSuspendThenHibernate=yes\n'
                 f"HibernateDelaySec={policy['delay_minutes']}min\n"
                 'HibernateOnACPower=no\n')
    return text


def command(relative, *args):
    subprocess.run([str(path(relative)), *args], check=True)


def run():
    """Called only by systemd for a real suspend request, never by a Decky RPC."""
    with locked():
        policy = read_policy()
        mode = policy['mode']
        if mode == 'system':
            command(SLEEP, 'suspend')
            return
        if mode not in available_modes():
            raise RuntimeError('Selected sleep mode is no longer available; choose System default in Battery')
        runtime = path(RUNTIME)
        runtime.parent.mkdir(parents=True, exist_ok=True)
        # An existing file is evidence of another owner/unfinished operation.
        # Never overwrite it while changing a global sleep operation.
        prepare = mode in ('hibernate', 'auto')
        prepared = False
        restore = {}
        if mode in ('s2idle', 'auto'):
            selected = [x[1:-1] for x in path('sys/power/mem_sleep').read_text().split()
                        if x.startswith('[') and x.endswith(']')]
            if len(selected) != 1:
                raise RuntimeError('Cannot read the current memory sleep mode')
            restore['sys/power/mem_sleep'] = selected[0]
        if prepare:
            for name in ('resume', 'resume_offset'):
                restore['sys/power/' + name] = path('sys/power/' + name).read_text().strip()
        stream = runtime.open('x', encoding='utf-8', newline='\n')
        try:
            with stream:
                stream.write(configuration(policy))
            if prepare:
                command(HELPER, 'create')
                prepared = True
            operation = {'s2idle': 'suspend', 'hibernate': 'hibernate',
                         'auto': 'suspend-then-hibernate'}[mode]
            command(SLEEP, operation)
        finally:
            try:
                if prepared:
                    # Also cleans up when auto mode wakes before the S4 timer,
                    # for which the vendor's hibernate post-hook never runs.
                    command(HELPER, 'cleanup')
            finally:
                try:
                    for relative, value in restore.items():
                        path(relative).write_text(value + '\n')
                finally:
                    runtime.unlink()


if __name__ == '__main__':
    import sys
    if sys.argv[1:] == ['status']:
        print(json.dumps(status()))
    elif sys.argv[1:] == ['run']:
        run()
    else:
        raise SystemExit('Usage: sleep_control.py status|run')
