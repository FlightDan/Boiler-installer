"""Fingerprint verification bridge; PIN and template bytes are never handled here."""
import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import tempfile

ENROLLMENT_LIMIT = 6
ENROLL_RETRY_TIMEOUT_SECONDS = 12
FINGER_SLOTS = tuple(
    side + '-' + finger for side in ('right', 'left')
    for finger in ('index-finger', 'middle-finger', 'ring-finger', 'little-finger', 'thumb')
)
AUTH_AGENT_NAME = 'org.kde.polkit-kde-authentication-agent-1'
STOCK_AUTH_AGENT = '/usr/lib/polkit-kde-authentication-agent-1'


PACKAGES = ('fprintd', 'libfprint', 'libgusb')
PACMAN_CACHE = Path('/var/cache/pacman/pkg')
CACHE_ROOT = Path('/home/.boiler-fingerprint')
TEMP_ROOT = Path('/tmp')
KEYRING_SOURCES = Path('/usr/share/pacman/keyrings')
KEYRING_NAMES = tuple(f'{name}{suffix}' for name in ('archlinux', 'holo')
                      for suffix in ('.gpg', '-trusted', '-revoked'))
KEYRING_FILES = ('pubring.gpg', 'trustdb.gpg')


class Plugin:
    async def _main(self):
        config = json.loads((Path(__file__).parent / 'profile.json').read_text())
        self.verify_path = config['verify_command']
        self.list_path = config['list_command']
        self.enroll_command = config['enroll_command']
        self.game_mode_auth_agent = config.get('game_mode_auth_agent')
        self.user = os.environ.get('DECKY_USER', 'deck')
        self.lock = asyncio.Lock()
        self.proc = None
        self.cancelled = False
        self.enroll_state = None
        self.enroll_profile = {'total': None, 'guided': False, 'scan_type': 'unknown'}
        self.enroll_unit = None
        self.package_lock = asyncio.Lock()
        self.repairing = False
        self.repair_task = None
        self.cache_root = CACHE_ROOT
        self.packages = self.cache_root / 'current'
        self.fresh_task = asyncio.create_task(self._keep_fresh())

    async def _stop(self, proc):
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), 3)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                return
            try:
                await asyncio.wait_for(proc.wait(), 3)
            except asyncio.TimeoutError:
                pass

    async def _run(self, executable, timeout, *flags):
        try:
            spawn = asyncio.create_task(asyncio.create_subprocess_exec(
                executable, *flags, self.user, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, env={'PATH': '/usr/bin:/bin', 'LANG': 'C'}))
            try:
                self.proc = await asyncio.shield(spawn)
            except asyncio.CancelledError:
                self.proc = await spawn
                raise
            if self.cancelled:
                await self._stop(self.proc)
                return None, '', 'cancelled'
            try:
                output, _ = await asyncio.wait_for(self.proc.communicate(), timeout)
            except asyncio.TimeoutError:
                await self._stop(self.proc)
                return None, '', 'timeout'
            return self.proc.returncode, output.decode('utf-8', errors='replace'), None
        except asyncio.CancelledError:
            if self.proc is not None:
                await asyncio.shield(self._stop(self.proc))
            raise
        except (OSError, ProcessLookupError):
            return None, '', 'error'
        finally:
            self.proc = None

    async def verify(self, timeout_s=30):
        if self.lock.locked():
            return {'result': 'busy'}
        async with self.lock:
            self.cancelled = False
            # fprintd-verify without -f tries only the first enrolled finger; 'any' matches all of them.
            code, text, reason = await self._run(self.verify_path, max(1, min(int(timeout_s), 30)), '-f', 'any')
            # Exact result line and successful process completion are both required.
            results = re.findall(r'^Verify result: ([^\r\n]+)[ \t]*$', text, re.M)
            if self.cancelled:
                return {'result': 'unavailable', 'reason': 'cancelled'}
            if code == 0 and len(results) == 1 and results[0].strip() == 'verify-match (done)':
                return {'result': 'match'}
            if code == 1 and len(results) == 1 and results[0].strip() == 'verify-no-match (done)':
                reason = 'no-match'
            elif (reason is None and code == 1 and len(results) == 1
                  and results[0].strip() == 'verify-unknown-error (done)'
                  and not re.search(r'^(?:VerifyStart|VerifyStop) failed:', text, re.M)):
                reason = 'device-error'
            return {'result': 'unavailable', 'reason': reason or 'error'}

    async def cancel(self):
        self.cancelled = True
        unit = getattr(self, 'enroll_unit', None)
        stopped = await self._stop_unit(unit) if unit else True
        if stopped and unit and self.enroll_unit == unit:
            self.enroll_unit = None
        proc = self.proc
        if proc is not None:
            await self._stop(proc)
        return {'cancelled': stopped}

    async def status(self):
        if self.lock.locked():
            return {'available': False, 'busy': True, 'repairable': self.verify_path == '/usr/bin/fprintd-verify'}
        async with self.lock:
            self.cancelled = False
            code, text, _ = await self._run(self.list_path, 10)
            return {'available': code == 0, 'enrolled': bool(re.search(r'#\d+:\s*\S+', text)) if code == 0 else False,
                    'enroll_command': self.enroll_command,
                    'busy': self.repairing or (self.verify_path == '/usr/bin/fprintd-verify'
                                               and not os.access(self.verify_path, os.X_OK)
                                               and self.package_lock.locked()),
                    'repairable': self.verify_path == '/usr/bin/fprintd-verify' and not os.access(self.verify_path, os.X_OK)}

    def _account(self):
        # Decky is privileged; the enrollment client must belong to the Steam user.
        import pwd, os
        name = os.environ.get('DECKY_USER', '')
        if not name or name == 'root':
            raise ValueError('missing Steam user')
        account = pwd.getpwnam(name)
        if account.pw_uid == 0:
            raise ValueError('root account')
        return account

    async def _user_process(self, *argv):
        import os
        account = self._account()
        runtime = '/run/user/' + str(account.pw_uid)
        return await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            user=account.pw_uid, group=account.pw_gid,
            extra_groups=os.getgrouplist(account.pw_name, account.pw_gid),
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'HOME': account.pw_dir,
                 'USER': account.pw_name, 'LOGNAME': account.pw_name,
                 'XDG_RUNTIME_DIR': runtime, 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=' + runtime + '/bus'})

    async def _user_capture(self, *argv):
        proc = await self._user_process(*argv)
        try:
            output, _ = await asyncio.wait_for(proc.communicate(), 10)
            return proc.returncode, output.decode('utf-8', errors='replace')
        except BaseException:
            await self._stop(proc)
            raise

    async def _stop_unit(self, unit):
        try:
            code, _ = await self._user_capture('/usr/bin/systemctl', '--user', 'stop', unit)
            if code == 0:
                return True
            code, state = await self._user_capture('/usr/bin/systemctl', '--user', 'show', unit,
                '--property=LoadState', '--property=ActiveState')
            values = dict(line.split('=', 1) for line in state.splitlines() if '=' in line)
            return code == 0 and (values.get('LoadState') == 'not-found' or values.get('ActiveState') in ('inactive', 'failed'))
        except (OSError, ValueError, KeyError, asyncio.TimeoutError):
            return False

    async def _list_fingerprints(self):
        code, text = await self._user_capture('/usr/bin/busctl', '--system', 'call',
            'net.reactivated.Fprint', '/net/reactivated/Fprint/Manager',
            'net.reactivated.Fprint.Manager', 'GetDefaultDevice')
        match = re.fullmatch(r'o "(/net/reactivated/Fprint/Device/\d+)"\s*', text)
        if code != 0 or not match:
            return None
        path = match.group(1)
        args = ('/usr/bin/busctl', '--system', 'get-property', 'net.reactivated.Fprint', path,
                'net.reactivated.Fprint.Device')
        ncode, name = await self._user_capture(*args, 'name')
        scode, stages = await self._user_capture(*args, 'num-enroll-stages')
        name_match = re.fullmatch(r's "([^"\r\n]+)"\s*', name)
        stage_match = re.fullmatch(r'i (-?\d+)\s*', stages)
        if ncode != 0 or scode != 0 or not name_match or not stage_match:
            return None
        device_name = name_match.group(1)
        stage_count = int(stage_match.group(1))
        if stage_count == 0 or stage_count < -1:
            return None
        account = self._account()
        code, listed = await self._user_capture('/usr/bin/systemd-run', '--user', '--quiet', '--pipe', '--wait', '--collect',
            '--property=RuntimeMaxSec=10', '--setenv=LC_ALL=C', '--', self.list_path, account.pw_name)
        lines = listed.splitlines()
        if code != 0 or lines[:3] != ['found 1 devices', 'Device at ' + path, 'Using device ' + path]:
            return None
        empty = 'User ' + account.pw_name + ' has no fingers enrolled for ' + device_name + '.'
        header = 'Fingerprints for user ' + account.pw_name + ' on ' + device_name + ' ('
        if lines[3:] == [empty]:
            entries = []
            tcode, scan = await self._user_capture(*args, 'scan-type')
            scan_type = {'s "press"': 'press', 's "swipe"': 'swipe'}.get(scan.strip(), 'unknown') if tcode == 0 else 'unknown'
        elif len(lines) > 4 and lines[3] in (header + 'press):', header + 'swipe):'):
            scan_type = 'press' if lines[3] == header + 'press):' else 'swipe'
            entries = []
            for index, line in enumerate(lines[4:]):
                match = re.fullmatch(r' - #' + str(index) + r': ((?:right|left)-(?:thumb|(?:index|middle|ring|little)-finger))', line)
                if not match or match.group(1) in entries:
                    return None
                entries.append(match.group(1))
        else:
            return None
        # The BRISK backend's nine stages include fprintd's duplicate check.
        guided = device_name == 'FocalTech FTE3600 (host BRISK)' and stage_count in (-1, 9)
        self.enroll_profile = {'total': 8 if guided else (stage_count if stage_count > 0 else None),
                               'guided': guided, 'scan_type': scan_type}

        return entries

    async def fingerprints(self):
        if self.lock.locked():
            return {'result': 'busy'}
        async with self.lock:
            try:
                entries = await self._list_fingerprints()
                if entries is not None:
                    return {'result': 'available', 'count': len(entries), 'limit': ENROLLMENT_LIMIT}
            except (OSError, ValueError, KeyError, asyncio.TimeoutError):
                pass
            return {'result': 'unavailable', 'reason': 'list'}

    async def _prepare_game_mode_auth(self):
        code, _ = await self._user_capture('/usr/bin/systemctl', '--user', 'is-active', '--quiet',
                                           'gamescope-session.target')
        if code != 0:
            # The desktop session provides its own authentication agent.
            return None

        runtime = '/run/user/' + str(self._account().pw_uid)
        code, environment = await self._user_capture('/usr/bin/cat', runtime + '/gamescope-environment')
        displays = re.findall(r'^DISPLAY=(:[0-9]+)$', environment, re.M)
        if code != 0 or len(displays) != 1:
            return False
        display = displays[0]
        owner = ('/usr/bin/busctl', '--user', '--no-pager', 'status', AUTH_AGENT_NAME)
        code, status = await self._user_capture(*owner)
        if code != 0:
            # Start the KDE agent as the Steam user on Gamescope's active display.
            agent = self.game_mode_auth_agent or STOCK_AUTH_AGENT
            keyboard_env = ()
            if self.game_mode_auth_agent:
                keyboard_env = ('--setenv=BOILER_FINGERPRINT_GAMESCOPE_KEYBOARD=1',
                                '--setenv=QT_IM_MODULE=qtvirtualkeyboard',
                                '--setenv=QT_VIRTUALKEYBOARD_DESKTOP_DISABLE=1')
            code, _ = await self._user_capture(
                '/usr/bin/systemd-run', '--user', '--quiet', '--collect',
                '--unit=boiler-fingerprint-gamemode-auth.service',
                '--property=Type=dbus', '--property=BusName=' + AUTH_AGENT_NAME,
                '--property=BindsTo=gamescope-session.target', '--property=After=gamescope-session.target',
                '--property=TimeoutStartSec=5s', '--setenv=DISPLAY=' + display,
                '--setenv=QT_QPA_PLATFORM=xcb', '--setenv=QT_SCALE_FACTOR=1.6',
                *keyboard_env, '--', agent)
            if code != 0:
                return False
            code, status = await self._user_capture(*owner)
        pid = re.search(r'^PID=([1-9][0-9]*)$', status, re.M)
        return (display, pid.group(1)) if code == 0 and pid else False

    async def _focus_game_mode_auth(self, display, pid):
        # Gamescope leaves ordinary X11 windows under Steam. Ask it to show
        # the system agent's window and give it keyboard focus during auth.
        env = ('/usr/bin/env', 'DISPLAY=' + display)
        try:
            code, result = await self._user_capture(*env, '/usr/bin/xdotool', 'search',
                                                     '--sync', '--onlyvisible', '--pid', pid)
            windows = result.splitlines()
            if code != 0 or len(windows) != 1 or not re.fullmatch(r'[1-9][0-9]*', windows[0]):
                return
            window_id = windows[0]
            tool = (*env, '/usr/bin/xdotool')
            screen_code, screen = await self._user_capture(*tool, 'getdisplaygeometry')
            size_code, size = await self._user_capture(*tool, 'getwindowgeometry', '--shell', window_id)
            display_size = re.fullmatch(r'([1-9][0-9]*) ([1-9][0-9]*)\s*', screen)
            geometry = dict(re.findall(r'^(WIDTH|HEIGHT)=([1-9][0-9]*)$', size, re.M))
            if screen_code == 0 and size_code == 0 and display_size and geometry.keys() == {'WIDTH', 'HEIGHT'}:
                screen_width, screen_height = map(int, display_size.groups())
                width, height = (int(geometry[key]) for key in ('WIDTH', 'HEIGHT'))
                x = max(0, (screen_width - width) // 2)
                y = max(0, min(screen_height // 10, screen_height // 2 - height))
                await self._user_capture(*tool, 'windowmove', window_id, str(x), str(y))
            window = (*env, '/usr/bin/xprop', '-id', window_id)
            code, _ = await self._user_capture(*window, '-f', 'STEAM_OVERLAY', '32c',
                                               '-set', 'STEAM_OVERLAY', '1')
            if code == 0:
                await self._user_capture(*window, '-f', 'STEAM_INPUT_FOCUS', '32c',
                                         '-set', 'STEAM_INPUT_FOCUS', '1')
        except (OSError, ValueError, KeyError, asyncio.TimeoutError):
            pass

    async def enroll(self):
        if self.lock.locked() or self.enroll_unit:
            return {'result': 'busy'}
        async with self.lock:
            import uuid
            self.cancelled = False
            self.enroll_state = {'result': 'starting', 'samples': 0}
            unit = None
            auth_focus = None
            try:
                self.enroll_profile = {'total': None, 'guided': False, 'scan_type': 'unknown'}
                entries = await self._list_fingerprints()
                if self.cancelled:
                    return {'result': 'cancelled'}
                if entries is None:
                    return {'result': 'unavailable', 'reason': 'list'}
                if len(entries) >= ENROLLMENT_LIMIT:
                    return {'result': 'full'}
                # fprintd requires a slot ID. Users only see their saved prints.
                finger = next(slot for slot in FINGER_SLOTS if slot not in entries)
                command = self.enroll_command.removesuffix(' -f right-index-finger')
                command_path = PurePosixPath(command)
                if not command_path.is_absolute() or command_path.name != 'fprintd-enroll':
                    return {'result': 'unavailable', 'reason': 'session'}
                auth = await self._prepare_game_mode_auth()
                if auth is False:
                    return {'result': 'unavailable', 'reason': 'agent'}
                if self.cancelled:
                    return {'result': 'cancelled'}
                profile = self.enroll_profile
                self.enroll_state = {'result': 'identifying', 'samples': 0, **profile}
                unit = 'boiler-fingerprint-enroll-' + uuid.uuid4().hex + '.service'
                self.enroll_unit = unit
                self.proc = await self._user_process('/usr/bin/systemd-run', '--user', '--pipe', '--wait', '--collect',
                    '--unit=' + unit, '--property=RuntimeMaxSec=180', '--property=TimeoutStopSec=3s',
                    '--setenv=LC_ALL=C', '--', '/usr/bin/stdbuf', '-oL', '-eL',
                    command, '-f', finger, self._account().pw_name)
                if self.cancelled:
                    return {'result': 'cancelled'}

                async def read_output():
                    nonlocal auth_focus
                    identified = False
                    samples = 0
                    complete = False
                    authorization_denied = False
                    retry_deadline = None
                    clock = asyncio.get_running_loop().time
                    while True:
                        if retry_deadline is None:
                            line = await self.proc.stdout.readline()
                        else:
                            try:
                                line = await asyncio.wait_for(self.proc.stdout.readline(), retry_deadline - clock())
                            except asyncio.TimeoutError:
                                return 'retry-timeout'
                        if not line:
                            break
                        decoded = line.decode('utf-8', errors='replace')
                        match = re.fullmatch(r'Enroll result: (enroll-[a-z-]+)\s*', decoded)
                        if not match:
                            if auth and auth_focus is None and decoded.rstrip() == f'Enrolling {finger} finger.':
                                auth_focus = asyncio.create_task(self._focus_game_mode_auth(*auth))
                            if decoded.startswith('Enroll result:'):
                                return 'capture'
                            if 'net.reactivated.Fprint.Error.PermissionDenied' in decoded or 'Not Authorized:' in decoded:
                                authorization_denied = True
                            continue
                        event = match.group(1)
                        if complete:
                            return 'capture'
                        if event == 'enroll-stage-passed':
                            retry_deadline = None
                            if profile['guided'] and not identified:
                                identified = True
                            elif not profile['guided'] or samples < 7:
                                samples += 1
                            else:
                                return 'capture'
                            self.enroll_state = {'result': 'sampling', 'samples': samples, **profile}
                        elif event == 'enroll-completed':
                            complete = not profile['guided'] or (identified and samples == 7)
                            if not complete:
                                return 'capture'
                            samples = 8 if profile['guided'] else samples + 1
                            retry_deadline = None
                            self.enroll_state = {'result': 'sampling', 'samples': samples, **profile}
                        elif event in ('enroll-retry-scan', 'enroll-swipe-too-short', 'enroll-finger-not-centered', 'enroll-remove-and-retry', 'enroll-too-fast'):
                            self.enroll_state = {'result': 'retry', 'samples': samples, **profile}
                            retry_deadline = clock() + ENROLL_RETRY_TIMEOUT_SECONDS
                        elif event == 'enroll-duplicate':
                            return 'duplicate'
                        else:
                            return 'capture'
                    await self.proc.wait()
                    if authorization_denied:
                        return 'authorization'
                    return None if complete and self.proc.returncode == 0 else 'capture'
                reason = await asyncio.wait_for(read_output(), 185)
                if self.cancelled:
                    return {'result': 'cancelled'}
                if reason is None:
                    return {'result': 'complete', 'samples': self.enroll_state['samples'], **profile}
                return {'result': 'unavailable', 'reason': reason, 'samples': self.enroll_state.get('samples', 0)}
            except (OSError, ValueError, KeyError, asyncio.TimeoutError):
                return {'result': 'cancelled'} if self.cancelled else {'result': 'unavailable', 'reason': 'session'}
            finally:
                if auth_focus is not None:
                    auth_focus.cancel()
                    await asyncio.gather(auth_focus, return_exceptions=True)
                if unit is not None:
                    stopped = await self._stop_unit(unit)
                    if not stopped:
                        stopped = await self._stop_unit(unit)
                    if self.proc is not None:
                        await self._stop(self.proc)
                        self.proc = None
                    self.enroll_unit = None if stopped else unit
                self.enroll_state = None

    async def enroll_progress(self):
        return self.enroll_state or {'result': 'idle'}

    async def _stop_command(self, proc, completion):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, sig)
            try:
                await asyncio.wait_for(asyncio.shield(completion), 1)
                return
            except asyncio.TimeoutError:
                continue
        logging.error('fingerprint package command did not stop')

    async def _cmd(self, timeout, *args):
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, start_new_session=True,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C'}))
        try:
            try:
                proc = await asyncio.shield(spawn)
            except asyncio.CancelledError:
                proc = await spawn
                await self._stop_command(proc, asyncio.create_task(proc.communicate()))
                raise
            completion = asyncio.create_task(proc.communicate())
            try:
                out, _ = await asyncio.wait_for(asyncio.shield(completion), timeout)
            except asyncio.TimeoutError:
                await self._stop_command(proc, completion)
                return None, 'timeout'
            except asyncio.CancelledError:
                await self._stop_command(proc, completion)
                raise
            return proc.returncode, out.decode('utf-8', errors='replace')
        except OSError as exc:
            return None, str(exc)

    def _cache_ready(self):
        """Only use the fixed private cache when its entire ancestry is trusted."""
        try:
            for path in reversed(self.cache_root.parents):
                st = path.lstat()
                if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
                    return False
            try:
                self.cache_root.mkdir(mode=0o700)
            except FileExistsError:
                pass
            st = self.cache_root.lstat()
            return stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and stat.S_IMODE(st.st_mode) == 0o700
        except OSError:
            return False

    def _publish(self, stage):
        previous = None
        if self.packages.is_symlink():
            name = os.readlink(self.packages)
            if re.fullmatch(r'set-[a-z0-9_]+', name):
                candidate = self.cache_root / name
                st = candidate.lstat()
                if stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o022:
                    previous = candidate
        target = self.cache_root / ('set-' + next(tempfile._get_candidate_names()))
        stage.rename(target)
        link = self.cache_root / ('link-' + next(tempfile._get_candidate_names()))
        switched = False
        try:
            link.symlink_to(target.name, target_is_directory=True)
            os.replace(link, self.packages)
            switched = True
        finally:
            link.unlink(missing_ok=True)
            if not switched:
                shutil.rmtree(target)
        if previous is not None:
            shutil.rmtree(previous)

    def _saved(self):
        if not self._cache_ready():
            return []
        try:
            if not self.packages.is_symlink():
                return []
            target = os.readlink(self.packages)
            if not re.fullmatch(r'set-[a-z0-9_]+', target):
                return []
            directory = self.cache_root / target
            st = directory.lstat()
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
                return []
            files = list(directory.iterdir())
            result = []
            for name in PACKAGES:
                matches = [p for p in files if re.fullmatch(re.escape(name) + r'-[0-9][^/]*\.pkg\.tar\.(?:zst|xz|gz)', p.name)]
                if len(matches) != 1:
                    return []
                package = matches[0]
                for path in (package, package.with_name(package.name + '.sig')):
                    st = path.lstat()
                    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022 or st.st_size == 0:
                        return []
                result.append(str(package))
            return result
        except OSError:
            return []

    async def _keep_fresh(self):
        if self.verify_path != '/usr/bin/fprintd-verify':
            return
        def missing():
            return not os.access(self.verify_path, os.X_OK)
        await self._seed_cache()
        if missing():
            result = await self.repair(offline_only=True)
            if not result['ok']: logging.error('fingerprint restore failed: %s', result['error'])
        while True:
            fetched = await self._download_latest()
            if fetched and missing():
                result = await self.repair(offline_only=True)
                if not result['ok']: logging.error('fingerprint restore failed: %s', result['error'])
            await asyncio.sleep(12 * 3600 if fetched else 600)

    async def _seed_cache(self):
        async with self.package_lock:
            if not self._cache_ready() or self._saved():
                return False
            code, out = await self._cmd(10, 'pacman', '-Q', *PACKAGES)
            rows = [line.split() for line in out.splitlines()]
            if code != 0 or len(rows) != len(PACKAGES) or {r[0] for r in rows if len(r) == 2} != set(PACKAGES):
                return False
            with tempfile.TemporaryDirectory(dir=self.cache_root, prefix='stage-') as tmp:
                stage = Path(tmp)
                try:
                    copied = []
                    for name, version in rows:
                        matches = [p for p in PACMAN_CACHE.glob(f'{name}-{version}-*.pkg.tar.*') if not p.name.endswith('.sig')]
                        if len(matches) != 1 or not matches[0].with_name(matches[0].name + '.sig').is_file():
                            return False
                        copied.append((name, version, matches[0].name))
                        for source in (matches[0], matches[0].with_name(matches[0].name + '.sig')):
                            shutil.copyfile(source, stage / source.name)
                    metadata = [[name, matches_name, version, ''] for name, version, matches_name in copied]
                    with tempfile.TemporaryDirectory(dir=self.cache_root, prefix='keys-') as keys:
                        gpg = Path(keys) / 'gnupg'; gpg.mkdir(mode=0o700)
                        if not await self._prepare_keyring(gpg) or not await self._authenticate(stage, metadata, gpg):
                            return False
                    self._publish(stage)
                    return bool(self._saved())
                except (OSError, ValueError):
                    return False

    async def _download_latest(self):
        async with self.package_lock:
            return await self._download_locked()

    async def _download_locked(self):
        if not self._cache_ready():
            return False
        try:
            temp_root = TEMP_ROOT
            st = temp_root.lstat()
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or not st.st_mode & stat.S_ISVTX:
                return False
            with tempfile.TemporaryDirectory(dir=temp_root, prefix='boiler-fprint-') as tmp, tempfile.TemporaryDirectory(dir=self.cache_root, prefix='fetch-') as private:
                root = Path(tmp); root.chmod(0o711)
                db = root / 'db'; stage = root / 'packages'; gpg = Path(private) / 'gnupg'
                (db / 'sync').mkdir(parents=True); stage.mkdir(); gpg.mkdir(mode=0o700)
                if not await self._prepare_keyring(gpg):
                    return False
                base = ('pacman', '--dbpath', str(db), '--cachedir', str(stage),
                        '--gpgdir', str(gpg), '--logfile', str(Path(private) / 'pacman.log'))
                if (await self._cmd(120, *base, '-Sy', '--noconfirm'))[0] != 0:
                    return False
                code, metadata = await self._cmd(30, *base, '-Sp', '--nodeps', '--print-format', '%n\t%f\t%v\t%g', *PACKAGES)
                if code != 0:
                    return False
                rows = [line.split('\t') for line in metadata.splitlines()]
                if len(rows) != len(PACKAGES) or any(len(r) != 4 for r in rows) or {r[0] for r in rows} != set(PACKAGES):
                    return False
                for name, filename, version, signature in rows:
                    if not re.fullmatch(re.escape(name) + r'-[0-9][^/]*\.pkg\.tar\.(?:zst|xz|gz)', filename) or not version:
                        return False
                if (await self._cmd(300, *base, '-Sw', '--nodeps', '--noconfirm', *PACKAGES))[0] != 0:
                    return False
                for name, filename, version, signature in rows:
                    package = stage / filename; sig = stage / (filename + '.sig')
                    if not package.is_file() or package.stat().st_size == 0:
                        return False
                    if not sig.is_file() and signature and signature != '(null)':
                        sig.write_bytes(base64.b64decode(signature, validate=True))
                    if not sig.is_file() or sig.stat().st_size == 0:
                        return False
                keep = {r[1] for r in rows} | {r[1] + '.sig' for r in rows}
                protected = Path(private) / 'packages'; protected.mkdir()
                for filename in keep:
                    source = stage / filename
                    st = source.lstat()
                    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0:
                        return False
                    shutil.copyfile(source, protected / filename)
                if not await self._authenticate(protected, rows, gpg):
                    return False
                self._publish(protected)
                return bool(self._saved())
        except (OSError, ValueError):
            return False

    async def _authenticate(self, stage, rows, gpg=None):
        config_path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=self.cache_root, prefix='pacman-', delete=False) as f:
                config_path = f.name
                f.write(await self._strict_config())
            paths = [str(stage / row[1]) for row in rows]
            key_args = ('--gpgdir', str(gpg)) if gpg else ()
            code, out = await self._cmd(120, 'pacman', '--config', config_path, *key_args, '-U', '--print',
                                        '--print-format', '%n\t%v', *paths)
            expected = {(r[0], r[2]) for r in rows}
            found = {tuple(line.split('\t')) for line in out.splitlines()}
            return code == 0 and {row for row in found if len(row) == 2} == expected
        finally:
            if config_path:
                Path(config_path).unlink(missing_ok=True)

    async def _strict_config(self):
        code, text = await self._cmd(10, 'pacman-conf')
        if code != 0:
            raise ValueError('cannot resolve pacman configuration')
        match = re.search(r'^\s*\[options\]\s*$', text, re.M | re.I)
        if not match:
            raise ValueError('missing pacman options')
        end = re.search(r'^\s*\[', text[match.end():], re.M)
        pos = match.end() + end.start() if end else len(text)
        options = re.sub(r'^\s*LocalFileSigLevel\s*=.*$', '', text[match.end():pos], flags=re.M | re.I)
        return '[options]\n' + options + '\nLocalFileSigLevel = Required TrustedOnly\n'

    async def repair(self, offline_only=False):
        if self.verify_path != '/usr/bin/fprintd-verify':
            return {'ok': False, 'error': 'unsupported profile'}
        if os.access(self.verify_path, os.X_OK):
            return {'ok': False, 'error': 'fingerprint service is already installed'}
        if self.package_lock.locked():
            return {'ok': False, 'error': 'busy'}
        self.repairing = True
        self.repair_task = asyncio.current_task()
        try:
            return await self._repair(offline_only)
        finally:
            self.repairing = False
            self.repair_task = None

    async def _repair(self, offline_only):
        async with self.package_lock:
            saved = self._saved()
            if not saved and not offline_only:
                await self._download_locked()
                saved = self._saved()
            if not saved:
                return {'ok': False, 'error': 'no saved packages'}
            result = await self._install_locked(saved)
            if not result['ok'] and not offline_only and result.get('retryable'):
                if await self._download_locked():
                    result = await self._install_locked(self._saved())
            result.pop('retryable', None)
            return result

    def _keyring_generation(self):
        digest = hashlib.sha256()
        for name in KEYRING_NAMES:
            path = KEYRING_SOURCES / name
            st = path.lstat()
            if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
                raise ValueError('untrusted bundled keyring source')
            data = path.read_bytes()
            digest.update(len(name).to_bytes(4, 'big'))
            digest.update(name.encode('ascii'))
            digest.update(len(data).to_bytes(8, 'big'))
            digest.update(data)
        return digest.hexdigest()

    def _keyring_valid(self, directory):
        try:
            st = directory.lstat()
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or stat.S_IMODE(st.st_mode) != 0o700:
                return False
            if {p.name for p in directory.iterdir()} != set(KEYRING_FILES):
                return False
            for name in KEYRING_FILES:
                st = (directory / name).lstat()
                if (not stat.S_ISREG(st.st_mode) or st.st_uid != 0
                        or stat.S_IMODE(st.st_mode) != 0o600 or st.st_size == 0):
                    return False
            return True
        except OSError:
            return False

    async def _prepare_keyring(self, gpg):
        # Callers hold package_lock and have checked the private cache ancestry.
        try:
            generation = self._keyring_generation()
            baseline = self.cache_root / ('keyring-' + generation)
            if not self._keyring_valid(baseline):
                if baseline.exists() or baseline.is_symlink():
                    return False
                with tempfile.TemporaryDirectory(dir=self.cache_root, prefix='key-build-') as tmp:
                    build = Path(tmp) / 'gnupg'; build.mkdir(mode=0o700)
                    for timeout, args in ((120, ('pacman-key', '--gpgdir', str(build), '--init')),
                                          (600, ('pacman-key', '--gpgdir', str(build), '--populate', 'archlinux', 'holo'))):
                        code, out = await self._cmd(timeout, *args)
                        if code != 0:
                            logging.error('cannot prepare fingerprint keyring: %s', out[-300:])
                            return False
                    if self._keyring_generation() != generation:
                        return False
                    stage = Path(tmp) / 'baseline'; stage.mkdir(mode=0o700)
                    for name in KEYRING_FILES:
                        source = build / name
                        st = source.lstat()
                        if not stat.S_ISREG(st.st_mode) or st.st_size == 0:
                            return False
                        shutil.copyfile(source, stage / name)
                        (stage / name).chmod(0o600)
                    if not self._keyring_valid(stage):
                        return False
                    stage.rename(baseline)
            for name in KEYRING_FILES:
                shutil.copyfile(baseline / name, gpg / name)
                (gpg / name).chmod(0o600)
            return True
        except (OSError, ValueError):
            logging.exception('cannot prepare fingerprint keyring')
            return False

    async def _install_locked(self, saved):
        with tempfile.TemporaryDirectory(dir=self.cache_root, prefix='install-') as tmp:
            gpg = Path(tmp) / 'gnupg'; gpg.mkdir(mode=0o700)
            if not await self._prepare_keyring(gpg):
                return {'ok': False, 'error': 'cannot prepare trusted keyring'}
            return await self._install_transaction(saved, gpg)

    async def _check_install(self, command, saved):
        code, output = await self._cmd(120, *command, '--print', '--print-format', '%n\t%v', *saved)
        planned = [line.split('\t') for line in output.splitlines() if '\t' in line]
        if code != 0 or len(planned) != len(PACKAGES) or any(len(row) != 2 for row in planned):
            return 'cannot verify cached package transaction: ' + output[-250:]
        versions = dict(planned)
        if set(versions) != set(PACKAGES):
            return 'cached package transaction has unexpected targets'

        code, output = await self._cmd(10, 'pacman', '-Q', *PACKAGES)
        if code not in (0, 1):
            return 'cannot read installed package versions'
        for line in output.splitlines():
            fields = line.split()
            if len(fields) != 2 or fields[0] not in versions:
                continue
            name, installed = fields
            code, comparison = await self._cmd(10, 'vercmp', versions[name], installed)
            if code != 0:
                return 'cannot compare installed package version: ' + name
            try:
                if int(comparison) < 0:
                    return 'cached package is older than installed package: ' + name
            except ValueError:
                return 'cannot compare installed package version: ' + name
        return None

    async def _install_transaction(self, saved, gpg):
        try:
            config = await self._strict_config()
            with tempfile.NamedTemporaryFile(mode='w', dir=self.cache_root, prefix='pacman-', delete=False) as f:
                f.write(config); config_path = f.name
        except (OSError, ValueError) as exc:
            return {'ok': False, 'error': str(exc)}
        try:
            code, state = await self._cmd(10, 'steamos-readonly', 'status')
            status = state.strip()
            was_readonly = code == 0 and status == 'enabled'
            disabled = code == 1 and status in ('disabled', 'disabled, but loaded sysexts may result in some parts of the filesystem being read-only')
            if not (was_readonly or disabled):
                return {'ok': False, 'error': 'cannot determine readonly state'}
            command = ('pacman', '--config', config_path, '--gpgdir', str(gpg), '-U', '--noconfirm')
            error = await self._check_install(command, saved)
            if error:
                return {'ok': False, 'error': error, 'retryable': True}
            result = {'ok': False, 'error': 'installation failed'}
            try:
                if was_readonly and (await self._cmd(30, 'steamos-readonly', 'disable'))[0] != 0:
                    result = {'ok': False, 'error': 'cannot unlock the system partition'}
                else:
                    code, out = await self._cmd(120, *command, *saved)
                    result = {'ok': True} if code == 0 and os.access(self.verify_path, os.X_OK) else {'ok': False, 'error': out[-300:], 'retryable': True}
            finally:
                if was_readonly:
                    restore, _ = await asyncio.shield(self._cmd(30, 'steamos-readonly', 'enable'))
                    check, state = await asyncio.shield(self._cmd(10, 'steamos-readonly', 'status'))
                    if restore != 0 or check != 0 or state.strip() != 'enabled':
                        result = {'ok': False, 'error': 'failed to restore readonly state'}
            return result
        finally:
            Path(config_path).unlink(missing_ok=True)

    async def _unload(self):
        self.fresh_task.cancel()
        await self.cancel()
        repair_task = self.repair_task
        if repair_task is not None and repair_task is not self.fresh_task:
            repair_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.fresh_task
        if repair_task is not None and repair_task is not self.fresh_task:
            with contextlib.suppress(asyncio.CancelledError):
                await repair_task
