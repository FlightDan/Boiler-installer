"""Durable SteamOS live-root protection windows, separate from file transactions.

Lock order is updater cache -> Engine -> optional legacy store -> platform.
An interrupted protection window never authorizes or completes a file journal.
"""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import uuid

from .engine import MaintenanceError, _json_write, _sync_dir
from .adoption_blobs import _directory, _file, _legacy_lock

READONLY_PROGRAM = Path('/usr/bin/steamos-readonly')
FINDMNT_PROGRAM = Path('/usr/bin/findmnt')
PROTECTED_PREFIX = Path('/usr/local')
STATES = ('enabled','disabled')
SYSEXT_DISABLED = 'disabled, but loaded sysexts may result in some parts of the filesystem being read-only'
ENVIRONMENT = {'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C','LC_ALL':'C'}
IDENTITY = re.compile(r'[0-9a-fA-F][0-9a-fA-F-]{7,79}\Z')


def is_live_root(root):
    return os.name == 'posix' and Path(root).resolve() == Path('/')


def check_state_directory(root,state_dir=None):
    if is_live_root(root) and state_dir is not None:
        if Path(state_dir).absolute() != Path('/var/lib/boiler/maintenance'):
            raise MaintenanceError('Live root maintenance requires the canonical state directory')


def _check_host(root):
    if os.name != 'posix' or os.geteuid() != 0:
        raise MaintenanceError('Live root writes require Linux root')
    path=Path(root)/'etc/os-release'
    if path.is_symlink():
        if path.resolve(strict=True) != Path(root)/'usr/lib/os-release':
            raise MaintenanceError('Unsupported live OS release metadata link')
        path=Path(root)/'usr/lib/os-release'
    with _file(path) as (stream,info):
        if info.st_size > 65536: raise MaintenanceError('Live OS metadata exceeds size limit')
        lines=stream.read(65537).decode('utf-8').splitlines()
    values=[line.split('=',1)[1].strip().strip('"\'') for line in lines if line.startswith('ID=')]
    if values != ['steamos']:
        raise MaintenanceError('Protected live-root writes are supported only on SteamOS')


class RootCommands:
    """Only fixed OS tools and a fixed environment are used in production."""
    @staticmethod
    def _run(program,arguments,*,accepted=(0,),status=False):
        program=Path(program)
        target=program
        for _ in range(16):
            with _directory(target.parent): pass
            link=target.lstat()
            if not stat.S_ISLNK(link.st_mode): break
            if os.name != 'nt' and link.st_uid != 0:
                raise MaintenanceError('Untrusted root protection command link')
            destination=Path(os.readlink(target))
            target=Path(os.path.normpath(destination if destination.is_absolute() else target.parent/destination))
        else:
            raise MaintenanceError('Root protection command link chain exceeds limit')
        with _file(target) as (stream,info):
            if os.name != 'nt' and not info.st_mode & 0o111:
                raise MaintenanceError('Root protection command is not executable')
            result=subprocess.run([str(target),*arguments],env=dict(ENVIRONMENT),cwd='/',
                                  stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                  timeout=60,check=False)
        if len(result.stdout)>4096 or len(result.stderr)>4096:
            raise MaintenanceError('Root protection command output exceeds limit')
        if result.returncode not in accepted:
            raise MaintenanceError('Root protection OS command failed')
        output=result.stdout.decode('utf-8').strip()
        if status:
            if result.stderr.strip(): raise MaintenanceError('Cannot determine root protection state')
            return result.returncode,output
        return output

    def status(self):
        code,value=self._run(READONLY_PROGRAM,['status'],accepted=(0,1),status=True)
        if (code,value)==(0,'enabled'): return 'enabled'
        if code==1 and value in ('disabled',SYSEXT_DISABLED): return 'disabled'
        raise MaintenanceError('Unknown SteamOS root protection status')

    def set(self,value):
        if value not in STATES: raise MaintenanceError('Invalid root protection state')
        self._run(READONLY_PROGRAM,['enable' if value == 'enabled' else 'disable'])

    def identity(self):
        result={}
        for field in ('PARTUUID','UUID'):
            value=self._run(FINDMNT_PROGRAM,['-n','-o',field,'--target','/'])
            if not IDENTITY.fullmatch(value):
                raise MaintenanceError('Current SteamOS root partition identity is unknown')
            result[field.lower()]=value.lower()
        return result

    def confirm_writable(self):
        # SteamOS explicitly warns that loaded system extensions can keep /usr
        # read-only after the btrfs root property has changed. Never unmerge or
        # otherwise alter extensions implicitly.
        with _directory(PROTECTED_PREFIX) as directory:
            if os.statvfs(PROTECTED_PREFIX).f_flag & os.ST_RDONLY:
                raise MaintenanceError('Protected /usr/local remains read-only after root protection was disabled')


def _pairs(entries):
    result={}
    for key,value in entries:
        if key in result: raise MaintenanceError('Duplicate root protection intent key')
        result[key]=value
    return result


def _intent(path):
    with _file(path,private=True) as (stream,info):
        if info.st_size > 16384: raise MaintenanceError('Root protection intent exceeds size limit')
        raw=stream.read(16385)
    try:
        value=json.loads(raw,object_pairs_hook=_pairs,
                         parse_constant=lambda item: (_ for _ in ()).throw(ValueError('Invalid constant')))
    except (ValueError,UnicodeError,RecursionError) as error:
        raise MaintenanceError('Invalid root protection intent') from error
    required={'schema_version','kind','id','root_identity','original','phase'}
    if (not isinstance(value,dict) or not required <= value.keys() or set(value)-required-{'error'} or
            type(value['schema_version']) is not int or value['schema_version'] != 1 or
            value['kind'] != 'SteamOSRootWriteIntent' or not isinstance(value['id'],str) or not re.fullmatch(r'[0-9a-f]{32}',value['id']) or
            value['original'] not in STATES or value['phase'] not in ('prepared','writable','restoring','restore-failed') or
            not isinstance(value['root_identity'],dict) or set(value['root_identity']) != {'partuuid','uuid'} or
            any(not isinstance(item,str) or not IDENTITY.fullmatch(item) for item in value['root_identity'].values()) or
            ('error' in value and (not isinstance(value['error'],str) or len(value['error']) > 80))):
        raise MaintenanceError('Unsupported root protection intent')
    return value


def pending(root,state_dir=None):
    """Read-only inspection never invokes a tool or creates state."""
    root=Path(root).resolve()
    if not is_live_root(root): return None
    check_state_directory(root,state_dir)
    state=Path(state_dir) if state_dir is not None else Path(root)/'var/lib/boiler/maintenance'
    path=state/'platform/intent.json'
    if not os.path.lexists(path): return None
    value=_intent(path)
    return {'id':value['id'],'original':value['original'],'phase':value['phase'],
            'root_identity':value['root_identity'],'recovery_required':True}


@contextlib.contextmanager
def _platform_lock(directory):
    with _directory(directory,create=True) as parent:
        if os.name != 'nt' and os.fstat(parent).st_mode & 0o077:
            raise MaintenanceError('Root protection state directory must be private')
        # The intent's parent chain can have been created by the first Engine
        # invocation. Persist those directory entries before any RO change.
        for ancestor in (directory,*directory.parents): _sync_dir(ancestor)
        flags=os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)
        fd=os.open(directory/'lock' if os.name == 'nt' else 'lock',flags,0o600,
                   **({} if os.name == 'nt' else {'dir_fd':parent}))
        with os.fdopen(fd,'a+b') as stream:
            info=os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    (os.name != 'nt' and (info.st_uid != 0 or info.st_mode & 0o077))):
                raise MaintenanceError('Unsafe root protection lock')
            try:
                if os.name == 'nt':
                    import msvcrt
                    if stream.seek(0,2)==0: stream.write(b'0');stream.flush()
                    stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError as error:
                raise MaintenanceError('Another live root protection operation is active') from error
            try: yield
            finally:
                if os.name == 'nt':
                    stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                else: fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


class ProtectionWindow:
    def __init__(self,state_dir,commands=None):
        self.directory=Path(state_dir)/'platform'
        self.path=self.directory/'intent.json'
        self.commands=RootCommands() if commands is None else commands

    def _identity(self):
        value=self.commands.identity()
        if (not isinstance(value,dict) or set(value) != {'partuuid','uuid'} or
                any(not isinstance(item,str) or not IDENTITY.fullmatch(item) for item in value.values())):
            raise MaintenanceError('Current SteamOS root partition identity is unknown')
        return {key:item.lower() for key,item in value.items()}

    def _restore(self,record,root_anchor=None):
        try: identity=self._identity()
        except Exception:
            # A transient findmnt failure after disabling protection must not
            # suppress an enable attempt on this same, still-held live root.
            # Stale intents never receive this in-process continuity fallback.
            current=os.stat('/')
            if root_anchor is None or (current.st_dev,current.st_ino) != root_anchor: raise
            identity=None
        if identity is not None and identity != record['root_identity']:
            raise MaintenanceError('Return to the original SteamOS root partition to recover protection')
        record['phase']='restoring'
        try: _json_write(self.path,record)
        except OSError:
            # The original durable prepared intent already exists. A full
            # state filesystem must not prevent restoring root protection.
            pass
        try:
            try: current=self.commands.status()
            except Exception: current=None
            if current != record['original']: self.commands.set(record['original'])
            if self.commands.status() != record['original']:
                raise MaintenanceError('Root protection restoration did not take effect')
            if self._identity() != record['root_identity']:
                raise MaintenanceError('Root partition changed during protection restoration')
        except BaseException as error:
            record.update(phase='restore-failed',error=type(error).__name__)
            try: _json_write(self.path,record)
            except OSError: pass
            raise MaintenanceError('Root protection restoration failed; explicit recover is required') from error
        self.path.unlink();_sync_dir(self.directory)

    @contextlib.contextmanager
    def open(self,*,recover=False):
        with _platform_lock(self.directory), _directory(Path('/')) as root_descriptor:
            root_info=os.stat('/') if root_descriptor is None else os.fstat(root_descriptor)
            root_anchor=(root_info.st_dev,root_info.st_ino)
            if os.path.lexists(self.path):
                if not recover:
                    raise MaintenanceError('Interrupted root protection window requires explicit recover')
                # Restore only protection first. Engine/adoption/hook barriers
                # remain authoritative and cannot trap their own resume route.
                self._restore(_intent(self.path))
            identity=self._identity()
            original=self.commands.status()
            if original not in STATES: raise MaintenanceError('Unknown SteamOS root protection status')
            record=dict(schema_version=1,kind='SteamOSRootWriteIntent',id=uuid.uuid4().hex,
                        root_identity=identity,original=original,phase='prepared')
            _json_write(self.path,record)
            try:
                if original == 'enabled': self.commands.set('disabled')
                if self.commands.status() != 'disabled':
                    raise MaintenanceError('SteamOS root did not become writable')
                if self._identity() != identity:
                    raise MaintenanceError('Root partition changed before live write')
                self.commands.confirm_writable()
                record['phase']='writable';_json_write(self.path,record)
                yield
            finally:
                self._restore(record,root_anchor)


@contextlib.contextmanager
def write_scope(engine,*,recover=False):
    if not is_live_root(engine.root):
        yield
        return
    check_state_directory(engine.root,engine.state_dir)
    _check_host(engine.root)
    legacy=engine.root/'home/.oxp3-maintenance/lock'
    if not os.path.lexists(legacy) and os.path.lexists(legacy.parent):
        raise MaintenanceError('Existing legacy maintenance store has no protected lock')
    with _legacy_lock(engine,blocking=False) if os.path.lexists(legacy) else contextlib.nullcontext():
        with ProtectionWindow(engine.state_dir).open(recover=recover):
            yield
