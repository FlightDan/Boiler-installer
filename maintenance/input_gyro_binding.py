"""Fixed-device BMI260 binding with recoverable per-boot ownership.

Installed as a standalone, root-owned helper by the maintenance gyro adapter.
The command line accepts only start/stop; no path or driver is user supplied.
Only a module loaded by this per-boot operation is unloaded after restoration.
Other I2C devices are never unbound.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
sys.path.insert(0,'/usr/local/lib/boiler')

KERNEL = '7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9'
DEVICE = 'i2c-10EC5280:00'
STOCK = 'bmi160_i2c'
CUSTOM = 'oxp3_bmi260'
DEVICES = Path('/sys/bus/i2c/devices')
DRIVERS = Path('/sys/bus/i2c/drivers')
MODULE = Path('/var/lib/oxp3-gyro-v1/oxp3_bmi260.ko')
STATE = Path('/run/oxp3-gyro-binding')


class BindingError(RuntimeError):
    pass


def validate_state(record, expected, *, active=False):
    from maintenance.input_ownership import validate
    try:validate(record,expected,active=active)
    except (ValueError,TypeError) as error:raise BindingError('Invalid or stale gyro binding recovery record') from error
    if record['previous'] not in (None, STOCK):
        raise BindingError('Invalid or stale gyro binding recovery record')
    return record


class BindingManager:
    def __init__(self, backend):
        self.backend = backend

    def _restore(self, record):
        backend, previous = self.backend, record['previous']
        current = backend.driver()
        if current not in (None, STOCK, CUSTOM):
            raise BindingError('Gyro driver changed externally; preserve the recovery record')
        if current not in (previous, CUSTOM, None):
            raise BindingError('Gyro binding no longer belongs to this operation')
        if current != previous:
            backend.save(dict(record, phase='restoring'))
            if current == CUSTOM:
                backend.unbind(CUSTOM)
            if previous is not None and backend.driver() is None:
                backend.bind(previous)
        if backend.driver() != previous:
            raise BindingError('Original gyro binding could not be restored; retry stop')
        if not record['loaded_before']:
            backend.unload_owned()
        backend.clear()
        return {'status': 'restored', 'driver': previous}

    def start(self):
        backend = self.backend
        backend.guard(input_version=True)
        self._preflight()
        with backend.locked():
            backend.guard(input_version=True)
            boot = backend.boot()
            record = backend.read()
            if record is not None:
                self._classify(record)
                if record['phase'] == 'restoring':
                    self._restore(record)
                    record = None
            current = backend.driver()
            if current not in (None, STOCK, CUSTOM):
                raise BindingError('Unrecognized gyro driver; refusing to detach it')
            if current == CUSTOM:
                if record is None:raise BindingError('Unknown preloaded custom gyro requires inspection')
                if record['phase']=='prepared':backend.save(dict(record,phase='active'))
                return {'status': 'active', 'driver': CUSTOM}
            if record is None:
                if backend.loaded():raise BindingError('Preloaded custom gyro requires inspection')
                record = dict(backend.ownership(),previous=current,phase='prepared',loaded_before=False)
                backend.save(record)
            elif current not in (None, record['previous']):
                raise BindingError('Gyro binding changed during interrupted start')
            try:
                # Record intent first: insmod may immediately probe an unbound
                # target. Register before disrupting a stock-bound target.
                backend.ensure_custom()
                current = backend.driver()
                if current not in (None, CUSTOM, record['previous']):
                    raise BindingError('Gyro driver changed during module registration')
                if current == STOCK:
                    backend.unbind(STOCK)
                if backend.driver() is None:
                    backend.bind(CUSTOM)
                if backend.driver() != CUSTOM:
                    raise BindingError('Custom gyro driver did not claim the exact target')
                backend.save(dict(record, phase='active'))
                return {'status': 'active', 'driver': CUSTOM}
            except Exception as error:
                try:
                    self._restore(record)
                except Exception as recovery:
                    raise BindingError('Gyro start failed and original binding needs recovery: ' + str(recovery)) from error
                raise BindingError('Gyro start failed; original binding restored') from error

    def stop(self):
        backend = self.backend
        self._preflight()
        with backend.locked():
            record = backend.read()
            if record is None:
                return {'status': 'unchanged'}
            self._classify(record)
            # Cleanup must remain possible if InputPlumber was upgraded/stopped.
            # Exact kernel and the unique hardware interface are still required.
            backend.guard(input_version=False)
            return self._restore(record)

    def _classify(self,record):
        from maintenance.input_ownership import classify
        try:
            return classify(record,self.backend.ownership() if record is not None else None,
                            loaded=self.backend.loaded(),driver=self.backend.driver(),stock=STOCK,custom=CUSTOM)
        except (ValueError,TypeError) as error:raise BindingError('Invalid or stale gyro binding recovery record') from error

    def _preflight(self):
        return self._classify(self.backend.read_protected())


def _protected(path, *, directory=False):
    for current in (path, *path.parents):
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise BindingError('Untrusted gyro helper or recovery path')
    info = path.lstat()
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise BindingError('Gyro recovery path is not a directory')
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise BindingError('Gyro helper must be a single regular file')


class SystemBackend:
    def __init__(self):
        self.directory = None

    def command(self, *args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=8,
                                env={'PATH': '/usr/bin:/usr/sbin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C'})
        if result.returncode:
            raise BindingError('Gyro dependency command failed: ' + Path(args[0]).name)
        return result.stdout.strip()

    def guard(self, *, input_version):
        if os.uname().release != KERNEL:
            raise BindingError('Gyro requires the exact tested kernel')
        if input_version and self.command('/usr/bin/inputplumber', '--version') != 'inputplumber 0.78.0':
            raise BindingError('Gyro requires InputPlumber 0.78.0')
        matches = [path.name for path in DEVICES.iterdir() if path.name.startswith('i2c-10EC5280:')]
        if matches != [DEVICE]:
            raise BindingError('Expected exactly the known OXP3 ACPI gyro device')
        resolved = (DEVICES / DEVICE).resolve(strict=True)
        if not resolved.is_relative_to('/sys/devices') or resolved.name != DEVICE:
            raise BindingError('Unexpected gyro sysfs device location')

    def boot(self):
        value = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value):
            raise BindingError('Cannot identify this gyro binding boot')
        return value

    def ownership(self):
        from maintenance.input_ownership import context
        return context('resource:bosch-bmi260-gyro',DEVICE)

    def read_protected(self):
        from maintenance.input_ownership import protected_record
        try:return protected_record(STATE/'state.json')
        except FileNotFoundError:return None

    def driver(self):
        path = DEVICES / DEVICE / 'driver'
        if not os.path.lexists(path):
            return None
        target = path.resolve(strict=True)
        name = target.name
        if name not in (STOCK, CUSTOM) or target != (DRIVERS / name).resolve(strict=True):
            raise BindingError('Unknown gyro driver; refusing sysfs writes')
        return name

    def loaded(self):
        return Path('/sys/module/oxp3_bmi260').exists()

    def unload_owned(self):
        if self.loaded():
            self.command('/usr/bin/rmmod', 'oxp3_bmi260')

    def ensure_custom(self):
        _protected(MODULE)
        self.command('/usr/bin/modprobe', 'bmi270_core')
        if not (DRIVERS / CUSTOM).is_dir():
            self.command('/usr/bin/insmod', str(MODULE))
        if not (DRIVERS / CUSTOM).is_dir():
            raise BindingError('Custom gyro driver did not register')

    def unbind(self, driver):
        if driver not in (STOCK, CUSTOM) or self.driver() != driver:
            raise BindingError('Gyro driver changed before unbind')
        (DRIVERS / driver / 'unbind').write_text(DEVICE + '\n')
        if self.driver() is not None:
            raise BindingError('Exact gyro device did not unbind')

    def bind(self, driver):
        if driver not in (STOCK, CUSTOM) or self.driver() is not None:
            raise BindingError('Gyro device is not unbound before bind')
        (DRIVERS / driver / 'bind').write_text(DEVICE + '\n')
        if self.driver() != driver:
            raise BindingError('Exact gyro device did not bind')

    @contextmanager
    def locked(self):
        if os.name != 'posix' or os.geteuid() != 0:
            raise BindingError('Gyro binding requires the root-owned system service')
        import fcntl
        _protected(STATE.parent, directory=True)
        try:
            STATE.mkdir(mode=0o700)
        except FileExistsError:
            pass
        _protected(STATE, directory=True)
        if stat.S_IMODE(STATE.stat().st_mode) != 0o700:
            raise BindingError('Gyro recovery directory must be private')
        directory = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptor = None
        try:
            descriptor = os.open('lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid or stat.S_IMODE(info.st_mode) != 0o600:
                raise BindingError('Untrusted gyro recovery lock')
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            self.directory = directory
            yield
        finally:
            self.directory = None
            if descriptor is not None:
                os.close(descriptor)
            os.close(directory)

    def read(self):
        try:
            descriptor = os.open('state.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.directory)
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 4096):
                raise BindingError('Untrusted gyro recovery record')
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise BindingError('Oversized gyro recovery record')
        from maintenance.input_ownership import _json
        return _json(raw)

    def save(self, record):
        temporary = '.state-' + os.urandom(12).hex()
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.directory)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write((json.dumps(record, sort_keys=True) + '\n').encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, 'state.json', src_dir_fd=self.directory, dst_dir_fd=self.directory)
            os.fsync(self.directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=self.directory)
            except FileNotFoundError:
                pass

    def clear(self):
        os.unlink('state.json', dir_fd=self.directory)
        os.fsync(self.directory)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv not in (['start'], ['stop']):
        raise BindingError('Choose only gyro start or stop')
    manager = BindingManager(SystemBackend())
    return manager.start() if argv == ['start'] else manager.stop()


if __name__ == '__main__':
    try:
        print(json.dumps(main()))
    except Exception as error:
        print('OXP3 gyro binding failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
