"""Durable file transactions, explicit ownership, and conservative crash recovery.

All file mutations are write-ahead journaled and recover by rollback. Hooks are
an explicit privileged extension: a crash during a hook requires inspection,
because arbitrary script side effects cannot be truthfully rolled back.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import uuid

from protocol import validate_manifest, verify_file, validate_package, compatible
from protocol.manifest import confined, relative_path, ProtocolError


class MaintenanceError(RuntimeError):
    pass


def _overlapping_targets(names):
    folded=[name.casefold() for name in names]
    if len(set(folded)) != len(folded): return True
    names=set(folded)
    return any('/'.join(name.split('/')[:index]) in names
               for name in names for index in range(1,len(name.split('/'))))


def _legacy_lock_required(journal):
    """V1 inline adoption predates a store-lock contract; binary adoption has one."""
    if journal.get('legacy_lock_required') is True: return True
    # Also recognize early binary v2 journals written before the explicit flag.
    return bool(journal.get('adoption_receipt') and any(
        item.get(phase,{}).get('storage') == 'blob-v1'
        for item in journal.get('operations',[]) for phase in ('before','after')))


RUNTIME_FILES = (
    'maintenance/__init__.py', 'maintenance/__main__.py', 'maintenance/engine.py',
    'maintenance/plugins.py', 'maintenance/updates.py', 'maintenance/adoption_blobs.py', 'maintenance/live_root.py',
    'protocol/__init__.py', 'protocol/__main__.py', 'protocol/fetch.py',
    'protocol/manifest.py', 'protocol/trusted-public-key.pem',
)


def runtime_files():
    root=Path(__file__).resolve().parents[1]
    expected=set(RUNTIME_FILES)
    for name in ('maintenance','protocol'):
        with os.scandir(root/name) as entries:
            for entry in entries:
                if Path(entry.name).suffix in ('.py','.pem') and name+'/'+entry.name not in expected:
                    raise MaintenanceError('Unreviewed maintenance runtime source: '+name+'/'+entry.name)
    return [root/name for name in RUNTIME_FILES]


def _sync_dir(path):
    if os.name != "nt":
        fd=os.open(path,os.O_RDONLY | getattr(os,"O_DIRECTORY",0))
        try: os.fsync(fd)
        finally: os.close(fd)


def _atomic(path,data,mode=None):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix=".boiler-",dir=path.parent)
    try:
        with os.fdopen(fd,"wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        if mode is not None: os.chmod(name,mode)
        os.replace(name,path); _sync_dir(path.parent)
    finally:
        if os.path.exists(name): os.unlink(name)


def _json_write(path,obj):
    _atomic(path,(json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n").encode(),0o600)


def _read(path):
    if Path(path).stat().st_size>512*1024**2: raise MaintenanceError("State exceeds size limit")
    def pairs(entries):
        result={}
        for k,v in entries:
            if k in result: raise MaintenanceError("Duplicate state key")
            result[k]=v
        return result
    try: return json.loads(Path(path).read_text(encoding="utf-8"),object_pairs_hook=pairs)
    except (ValueError,UnicodeError) as exc: raise MaintenanceError("Invalid maintenance state") from exc


def _adoption_receipt(path):
    """Read one protected receipt inode once; parsing and identity share its bytes."""
    path = Path(path).absolute()
    if '..' in path.parts or not path.name:
        raise MaintenanceError('Unsafe adoption receipt path')
    maximum = 512*1024**2
    directory = descriptor = None
    try:
        if os.name != 'nt':
            if os.geteuid() != 0:
                raise MaintenanceError('Adoption requires a root-owned private receipt')

            def protected_directory(fd):
                info = os.fstat(fd)
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
                    raise MaintenanceError('Adoption receipt has an untrusted ancestor')
                if stat.S_IMODE(info.st_mode) & 0o022 and not info.st_mode & stat.S_ISVTX:
                    raise MaintenanceError('Adoption receipt has a writable ancestor')

            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            directory = os.open(path.anchor, flags)
            protected_directory(directory)
            for part in path.parent.parts[1:]:
                child = os.open(part, flags, dir_fd=directory)
                try:
                    protected_directory(child)
                except BaseException:
                    os.close(child)
                    raise
                os.close(directory)
                directory = child
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        else:
            # Windows source tests have no POSIX ownership model. Linux is the
            # privileged adoption runtime; still reject known redirected paths.
            for current in (path, *path.parents):
                if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
                    raise MaintenanceError('Unsafe adoption receipt path')
            descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
            raise MaintenanceError('Unsafe or oversized adoption receipt')
        if os.name != 'nt' and (info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o077):
            raise MaintenanceError('Adoption requires a root-owned private receipt')
        with os.fdopen(descriptor, 'rb') as stream:
            descriptor = None
            raw = stream.read(maximum+1)
            after = os.fstat(stream.fileno())
        attributes = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if len(raw) != info.st_size or len(raw) > maximum or any(getattr(info, key) != getattr(after, key) for key in attributes):
            raise MaintenanceError('Adoption receipt changed while reading')
    except OSError as error:
        raise MaintenanceError('Unsafe adoption receipt path') from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)
    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise MaintenanceError('Duplicate adoption receipt key')
            result[key] = value
        return result
    def invalid_constant(value):
        raise MaintenanceError('Invalid adoption receipt constant')
    try:
        receipt = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise MaintenanceError('Invalid adoption receipt') from error
    if not isinstance(receipt, dict):
        raise MaintenanceError('Unsupported adoption receipt')
    return receipt, hashlib.sha256(raw).hexdigest()


def _snapshot(path):
    if not path.exists(): return {"exists":False}
    if path.is_symlink() or not path.is_file(): raise MaintenanceError("Target must be a regular file")
    info=path.stat()
    if info.st_size>128*1024**2: raise MaintenanceError("Target exceeds backup limit")
    data=path.read_bytes()
    return {"exists":True,"data":base64.b64encode(data).decode(),"sha256":hashlib.sha256(data).hexdigest(),
            "mode":stat.S_IMODE(info.st_mode),"uid":info.st_uid,"gid":info.st_gid}


def _same(left,right):
    return left.get("exists")==right.get("exists") and (not left.get("exists") or
        all(left.get(k)==right.get(k) for k in (("sha256",) if os.name=="nt" else ("sha256","mode","uid","gid"))) and
        (right.get('storage') != 'blob-v1' or left.get('size') == right.get('size')))


def build_hook_environment(root, transaction_id, extra=None):
    """No ambient shell startup, loader, Python, or executable search settings.

    Privileged installer integration may explicitly supply BOILER_* context;
    authenticated live updates supply none. Root and transaction identity cannot
    be overridden by an extension. This is execution hygiene, not a sandbox for
    an authenticated root script.
    """
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'HOME': '/root'}
    if extra is not None:
        if not isinstance(extra, dict):
            raise MaintenanceError('Invalid explicit hook environment')
        for name, value in extra.items():
            if (not isinstance(name, str) or not __import__('re').fullmatch(r'BOILER_[A-Z0-9_]+', name) or
                    name in ('BOILER_TARGET_ROOT', 'BOILER_TRANSACTION_ID') or not isinstance(value, str) or
                    '\x00' in value or len(value) > 4096):
                raise MaintenanceError('Invalid explicit hook environment')
            env[name] = value
    env.update(BOILER_TARGET_ROOT=str(root), BOILER_TRANSACTION_ID=transaction_id)
    return env


class Engine:
    def __init__(self,root,state_dir=None,*,read_only=False):
        self.root=Path(root).resolve()
        if not self.root.is_dir(): raise MaintenanceError("Target root is not mounted")
        from .live_root import check_state_directory
        check_state_directory(self.root,state_dir)
        self.state_dir=Path(state_dir).absolute() if state_dir else self.root/"var/lib/boiler/maintenance"
        self.read_only=read_only
        if os.name != 'nt':
            from .adoption_blobs import _directory
            for directory in (self.state_dir,self.state_dir/'transactions'):
                try:
                    with _directory(directory,create=not read_only) as descriptor:
                        if os.fstat(descriptor).st_mode & 0o022:
                            raise MaintenanceError('Maintenance state directories must not be writable by others')
                except FileNotFoundError:
                    if not read_only: raise
                except OSError as error:
                    raise MaintenanceError('Unsafe maintenance state path') from error
        else:
            cursor=self.state_dir
            while cursor != cursor.parent:
                if cursor.is_symlink() or (hasattr(cursor,"is_junction") and cursor.is_junction()): raise MaintenanceError("State path contains a link")
                cursor=cursor.parent
            if not read_only: self.state_dir.mkdir(parents=True,exist_ok=True)
            self.state_dir=self.state_dir.resolve()
        self.identity="mounted-target" if self.state_dir.is_relative_to(self.root) else str(self.root)
        self.journals=self.state_dir/"transactions"
        if not read_only and os.name == 'nt': self.journals.mkdir(exist_ok=True)

    @contextlib.contextmanager
    def _lock(self,*,live_recover=False):
        if self.read_only: raise MaintenanceError("Read-only verification cannot modify maintenance state")
        if os.name != 'nt':
            from .adoption_blobs import _directory
            import fcntl
            with _directory(self.state_dir) as directory, _directory(self.journals) as journals:
                if os.fstat(directory).st_mode & 0o022 or os.fstat(journals).st_mode & 0o022:
                    raise MaintenanceError('Maintenance state directories must not be writable by others')
                descriptor=os.open('lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=directory)
                with os.fdopen(descriptor,'a+b') as stream:
                    info=os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0 or info.st_mode & 0o022:
                        raise MaintenanceError('Unsafe maintenance lock file')
                    fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
                    try:
                        from .live_root import write_scope
                        with write_scope(self,recover=live_recover): yield
                    finally: fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
            return
        lock=self.state_dir/"lock"
        if lock.is_symlink(): raise MaintenanceError("Unsafe lock file")
        with lock.open("a+b") as stream:
            if os.name=="nt":
                import msvcrt
                if stream.seek(0,2)==0: stream.write(b"0"); stream.flush()
                stream.seek(0); msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK,1)
                try: yield
                finally: stream.seek(0); msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
                try: yield
                finally: fcntl.flock(stream.fileno(),fcntl.LOCK_UN)

    def _target(self,name):
        path=confined(self.root,name)
        if path==self.state_dir or self.state_dir in path.parents or path in self.state_dir.parents:
            raise MaintenanceError("Components cannot own maintenance state")
        backups=self.root/'home/.boiler/maintenance-blobs'
        if path==backups or backups in path.parents or path in backups.parents:
            raise MaintenanceError('Components cannot own maintenance backups')
        return path

    def _state(self):
        path=self.state_dir/"state.json"
        if path.is_symlink(): raise MaintenanceError("Unsafe state file")
        if not path.exists(): return {"schema_version":1,"root":self.identity,"owners":{},"components":{}}
        state=_read(path)
        if state.get("schema_version")!=1 or state.get("root")!=self.identity or not isinstance(state.get("owners"),dict) or not isinstance(state.get("components"),dict):
            raise MaintenanceError("State belongs to another mounted root or unsupported version")
        return state

    def _journal(self,identity):
        if not isinstance(identity,str) or not __import__('re').fullmatch(r"[0-9a-f]{32}",identity): raise MaintenanceError("Invalid transaction id")
        path=self.journals/(identity+".json")
        if path.is_symlink(): raise MaintenanceError("Unsafe journal path")
        return path

    def _put_snapshot(self,path,snapshot):
        if snapshot.get('storage') == 'blob-v1':
            from .adoption_blobs import restore_blob, remove_blob_target
            if snapshot.get('exists'): restore_blob(self,path,snapshot)
            else: remove_blob_target(self,path)
            return
        if not snapshot["exists"]:
            if path.exists(): path.unlink(); _sync_dir(path.parent)
            return
        data=base64.b64decode(snapshot["data"],validate=True)
        if hashlib.sha256(data).hexdigest()!=snapshot["sha256"]: raise MaintenanceError("Backup digest mismatch")
        _atomic(path,data,snapshot["mode"])
        if os.name!="nt": os.chown(path,snapshot["uid"],snapshot["gid"])

    def _observed(self,path,*expected):
        if any(item.get('storage') == 'blob-v1' for item in expected):
            from .adoption_blobs import observe
            return observe(self,path)
        return _snapshot(path)

    def _recover(self,*,allow_adoption=False,legacy_locked=False):
        if not allow_adoption and os.path.lexists(self.state_dir/'adoption-pending.json'):
            raise MaintenanceError('Pending adoption migration requires explicit receipt resume')
        if not legacy_locked:
            for candidate in self.journals.glob('*.json'):
                if candidate.is_symlink(): raise MaintenanceError('Unsafe journal')
                journal=_read(candidate)
                if (journal.get('status') not in ('committed','restored','rolled_back') and
                        _legacy_lock_required(journal)):
                    from .adoption_blobs import _legacy_lock
                    with _legacy_lock(self):
                        return self._recover(allow_adoption=allow_adoption,legacy_locked=True)
        recovered=[]
        for path in sorted(self.journals.glob("*.json")):
            if path.is_symlink(): raise MaintenanceError("Unsafe journal")
            journal=_read(path)
            if journal.get("status") in ("committed","restored","rolled_back"): continue
            if journal.get("root")!=self.identity: raise MaintenanceError("Journal belongs to another mounted root")
            if journal.get("status")=="adopting":
                # Adoption changes ownership metadata only; never uninstall legacy files during recovery.
                _json_write(self.state_dir/"state.json",journal["before_state"])
                journal["status"]="rolled_back"; _json_write(path,journal); recovered.append(journal["id"])
                continue
            if journal.get("status") in ("hook_running","hook_completed","manual_recovery_required"):
                raise MaintenanceError("Interrupted script requires manual recovery: "+journal["id"])
            # An interrupted write can only have produced the recorded before or after.
            for item in journal["operations"]:
                current=self._observed(self._target(item["target"]),item['before'],item['after'])
                if not (_same(current,item["before"]) or _same(current,item["after"])):
                    raise MaintenanceError("Recovery refused modified target: "+item["target"])
            restore_in_progress=journal.get("status")=="restoring"
            from .adoption_blobs import validate_backups
            validate_backups(self,journal['operations'])
            for item in reversed(journal["operations"]):
                if not item.get("retained"): self._put_snapshot(self._target(item["target"]),item["before"])
            _json_write(self.state_dir/"state.json",journal["before_state"])
            journal["status"]="restored" if restore_in_progress else "rolled_back"
            _json_write(path,journal); recovered.append(journal["id"])
        return recovered

    def recover(self):
        with self._lock(live_recover=True): return self._recover()

    def retained_owned(self,component,state=None):
        """Return bounded current identities for an explicit static retention plan."""
        retain=component.get('retain_owned_paths',False)
        if type(retain) is not bool: raise MaintenanceError('retain_owned_paths must be a boolean')
        if not retain: return []
        if component.get('hooks'): raise MaintenanceError('Retained ownership is restricted to static file components')
        if not component.get('files'): raise MaintenanceError('Retained static update requires declared files')
        from .adoption_blobs import observe, MAX_OPERATIONS
        state=self._state() if state is None else state
        cid=component['id']; installed=state['components'].get(cid)
        if not installed: raise MaintenanceError('Retained ownership requires an existing component')
        transaction=installed['transaction']; prior=_read(self._journal(transaction))
        if (prior.get('root') != self.identity or prior.get('status') != 'committed' or
                prior.get('id') != transaction or prior.get('component',{}).get('id') != cid):
            raise MaintenanceError('Retained ownership requires the current committed component journal')
        if prior['component'].get('hooks'):
            raise MaintenanceError('Retained ownership cannot replace a scripted component lifecycle')
        operations=prior.get('operations')
        if not isinstance(operations,list) or len(operations) > MAX_OPERATIONS:
            raise MaintenanceError('Invalid retained component journal')
        old={}
        for operation in operations:
            name=relative_path(operation['target'])
            if name in old: raise MaintenanceError('Duplicate retained ownership journal target')
            old[name]=operation['after']
        owned={name for name,owner in state['owners'].items() if owner['component'] == cid}
        if owned != set(old): raise MaintenanceError('Retained owner and journal path coverage differ')
        declared={relative_path(item['target']) for item in component.get('files',[])}
        if _overlapping_targets(declared | owned):
            raise MaintenanceError('Retained targets overlap or alias declared paths')
        result=[]
        for name in sorted(owned):
            owner=state['owners'][name]
            if owner.get('transaction') != transaction or owner.get('sha256') != old[name].get('sha256'):
                raise MaintenanceError('Retained path belongs to another component generation')
            folded=name.casefold()
            if any(other != name and (other.casefold() == folded or other.casefold().startswith(folded+'/') or
                    folded.startswith(other.casefold()+'/')) for other in state['owners']):
                raise MaintenanceError('Retained target overlaps installed ownership')
            current=observe(self,self._target(name))
            if not _same(current,old[name]): raise MaintenanceError('Retained managed file has changed')
            if name not in declared:
                result.append(dict(target=name,snapshot=current,transaction=transaction))
        return result

    def apply(self,component,payload_root,*,allow_hooks=False,environment=None,expected_state_sha256=None,hook_environment=None):
        validate_manifest(component,"ComponentManifest")
        if type(component.get('retain_owned_paths',False)) is not bool:
            raise MaintenanceError('retain_owned_paths must be a boolean')
        if not compatible(component,environment or {}): raise MaintenanceError("Component target compatibility has not been established")
        payload_root=Path(payload_root).resolve()
        hooks=component.get("hooks",{})
        if hooks:
            if not allow_hooks or os.name=="nt": raise MaintenanceError("Guest hooks require explicit privileged authorization on Linux")
            build_hook_environment(self.root, 'pending', hook_environment)
            package=validate_package(payload_root,allow_unsigned=True)
            if component not in package["components"]: raise MaintenanceError("Hook component not in verified package")
        with self._lock():
            self._recover(); state=self._state(); identity=uuid.uuid4().hex
            if expected_state_sha256 is not None:
                actual_state_sha256=hashlib.sha256(json.dumps(state,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()
                if actual_state_sha256 != expected_state_sha256: raise MaintenanceError("Installed state changed after update review")
            if any(dep not in state["components"] for dep in component.get("requires",[])):
                raise MaintenanceError("Required component is not installed")
            if any(dep in state["components"] for dep in component.get("conflicts",[])):
                raise MaintenanceError("Conflicting component is installed")
            if any(component["id"] in item.get("conflicts",[]) for item in state["components"].values()):
                raise MaintenanceError("Installed component declares a conflict")
            legacy_required=False
            installed=state['components'].get(component['id'])
            if installed:
                prior=_read(self._journal(installed['transaction']))
                if prior.get('root') != self.identity or prior.get('status') != 'committed':
                    raise MaintenanceError('Installed component journal is invalid')
                legacy_required=_legacy_lock_required(prior)
            from .adoption_blobs import _legacy_lock
            with _legacy_lock(self) if legacy_required else contextlib.nullcontext():
                return self._apply_locked(component,payload_root,hooks,state,identity,hook_environment,legacy_required)

    def _apply_locked(self,component,payload_root,hooks,state,identity,hook_environment,legacy_required):
        from .adoption_blobs import capture, payload_snapshot, observe, _hash_file, MAX_FILE, MAX_TOTAL, MAX_OPERATIONS
        operations=[]; total=0; targets=set()
        if _overlapping_targets(item['target'] for item in component.get('files',[])):
            raise MaintenanceError('Component targets overlap or alias')
        retained=self.retained_owned(component,state)
        if len(component.get('files',[]))+len(retained) > MAX_OPERATIONS:
            raise MaintenanceError('Component operation limit exceeded')
        for entry in component.get("files",[]):
            target=self._target(entry["target"]); key=entry["target"]
            if key.casefold() in targets: raise MaintenanceError("Duplicate target")
            targets.add(key.casefold())
            for owned,owner in state["owners"].items():
                if (key.casefold()==owned.casefold() or key.casefold().startswith(owned.casefold()+"/") or owned.casefold().startswith(key.casefold()+"/")) and owner["component"]!=component["id"]:
                    raise MaintenanceError("Target owned by another component")
            if entry['size'] > MAX_FILE:
                raise MaintenanceError('Component file exceeds the explicit binary transaction limit')
            source=confined(payload_root,entry['source'])
            try: _hash_file(source,entry)
            except MaintenanceError as error: raise ProtocolError(str(error)) from error
            before=observe(self,target)
            total+=entry['size']+before.get('size',0)
            if total > MAX_TOTAL: raise MaintenanceError('Transaction exceeds binary backup limit')
            owner=state["owners"].get(key)
            if owner:
                prior=_read(self._journal(owner["transaction"]))
                recorded=next((op["after"] for op in prior["operations"] if op["target"]==key),None)
                if recorded is None or not _same(before,recorded): raise MaintenanceError("Upgrade refused modified managed file")
            before=capture(self,target,before)
            after=payload_snapshot(self,source,entry,before)
            operations.append({"target":key,"before":before,"after":after})
        for item in retained:
            snapshot=item['snapshot']
            total+=2*snapshot.get('size',0)
            if total > MAX_TOTAL: raise MaintenanceError('Transaction exceeds binary backup limit')
            current=observe(self,self._target(item['target']))
            if not _same(current,snapshot): raise MaintenanceError('Retained managed file changed during preparation')
            snapshot=capture(self,self._target(item['target']),snapshot)
            operations.append(dict(target=item['target'],before=snapshot,after=dict(snapshot),retained=True))
        # Fail closed for upgrades that silently drop previously owned paths.
        old_paths={p for p,o in state["owners"].items() if o["component"]==component["id"]}
        if old_paths - {op["target"] for op in operations}: raise MaintenanceError("Upgrade drops owned files; explicit restore is required")
        journal={"schema_version":1,"id":identity,"root":self.identity,"status":"prepared","component":component,
                 "operations":operations,"before_state":state,"legacy_lock_required":legacy_required}
        path=self._journal(identity); _json_write(path,journal)
        try:
            journal["status"]="applying"; _json_write(path,journal)
            for item in operations:
                if not _same(observe(self,self._target(item['target'])),item['before']):
                    raise MaintenanceError('Target changed before apply')
            for item in operations:
                target=self._target(item['target'])
                if not _same(observe(self,target),item['before']):
                    raise MaintenanceError('Target changed before apply')
                if not item.get('retained'): self._put_snapshot(target,item['after'])
            if hooks:
                journal["status"]="hook_running"; _json_write(path,journal)
                for phase in ("apply","verify"):
                    if phase in hooks:
                        env=build_hook_environment(self.root,identity,hook_environment)
                        subprocess.run(["/bin/bash",str(confined(payload_root,hooks[phase])),str(self.root)],cwd=payload_root,env=env,check=True,timeout=900)
                journal["status"]="hook_completed"; _json_write(path,journal)
            # Hooks may not rewrite managed files behind ownership records.
            for item in operations:
                if not _same(observe(self,self._target(item["target"])),item["after"]): raise MaintenanceError("Applied file verification failed")
            next_state=json.loads(json.dumps(state))
            for item in operations: next_state["owners"][item["target"]]={"component":component["id"],"transaction":identity,"sha256":item["after"].get("sha256")}
            next_state["components"][component["id"]]={"version":component["version"],"transaction":identity,
                "requires":component.get("requires",[]),"conflicts":component.get("conflicts",[])}
            _json_write(self.state_dir/"state.json",next_state)
            journal["status"]="committed"; _json_write(path,journal)
        except BaseException:
            if journal["status"] in ("hook_running","hook_completed"):
                journal["status"]="manual_recovery_required"; _json_write(path,journal)
            else: self._recover(legacy_locked=legacy_required)
            raise
        return {"transaction":identity,"component":component["id"],"status":"committed","files":len(operations),
                "state_sha256":hashlib.sha256(json.dumps(next_state,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()}

    def verify(self,component_id):
        with self._lock():
            self._recover(); state=self._state()
            if component_id not in state["components"]: raise MaintenanceError("Component is not installed")
            journal=_read(self._journal(state["components"][component_id]["transaction"]))
            failures=[op["target"] for op in journal["operations"] if not _same(self._observed(self._target(op["target"]),op['after']),op["after"])]
            return {"component":component_id,"ok":not failures,"modified":failures}

    def verify_readonly(self,component_id):
        """For an exclusively mounted read-only image: never lock, recover or mkdir."""
        if not self.read_only: raise MaintenanceError("Read-only verification requires a read-only engine")
        if os.path.lexists(self.state_dir/'adoption-pending.json'): raise MaintenanceError('Pending adoption migration requires explicit receipt resume')
        for path in self.journals.glob("*.json"):
            if path.is_symlink(): raise MaintenanceError("Unsafe journal")
            if _read(path).get("status") not in ("committed","restored","rolled_back"):
                raise MaintenanceError("Pending transaction requires writable recovery before verification")
        state=self._state()
        if component_id not in state["components"]: raise MaintenanceError("Component is not installed")
        journal=_read(self._journal(state["components"][component_id]["transaction"]))
        if journal.get("root") != self.identity or journal.get("status") != "committed": raise MaintenanceError("Invalid active journal")
        failures=[op["target"] for op in journal["operations"] if not _same(self._observed(self._target(op["target"]),op['after']),op["after"])]
        return {"component":component_id,"ok":not failures,"modified":failures}

    def restore(self,transaction_id):
        with self._lock():
            self._recover(); state=self._state(); path=self._journal(transaction_id); journal=_read(path)
            if _legacy_lock_required(journal):
                from .adoption_blobs import _legacy_lock
                with _legacy_lock(self):
                    return self._restore_locked(transaction_id,state,path,journal)
            return self._restore_locked(transaction_id,state,path,journal)

    def _restore_locked(self,transaction_id,state,path,journal):
        if journal.get("root")!=self.identity or journal.get("status")!="committed": raise MaintenanceError("Transaction is not restorable")
        cid=journal["component"]["id"]
        if state["components"].get(cid,{}).get("transaction")!=transaction_id: raise MaintenanceError("Only active component generation can be restored")
        if journal["component"].get("hooks"): raise MaintenanceError("Script component requires explicit package restore integration")
        for item in journal["operations"]:
            if not _same(self._observed(self._target(item["target"]),item['after']),item["after"]): raise MaintenanceError("Restore refused modified target")
        # Preserve transactions belonging to other components installed later.
        previous=journal["before_state"]
        if cid not in previous["components"] and any(cid in item.get("requires",[]) for key,item in state["components"].items() if key!=cid):
            raise MaintenanceError("Installed component still requires this component")
        from .adoption_blobs import validate_backups
        validate_backups(self,journal['operations'])
        for item in journal["operations"]:
            name=item["target"]
            if name in previous["owners"]: state["owners"][name]=previous["owners"][name]
            else: state["owners"].pop(name,None)
        if cid in previous["components"]: state["components"][cid]=previous["components"][cid]
        else: state["components"].pop(cid,None)
        journal["before_state"]=state; journal["status"]="restoring"; _json_write(path,journal)
        for item in reversed(journal["operations"]):
            if not item.get("retained"): self._put_snapshot(self._target(item["target"]),item["before"])
        _json_write(self.state_dir/"state.json",state)
        journal["status"]="restored"; _json_write(path,journal)
        return {"transaction":transaction_id,"status":"restored"}

    def inspect_legacy(self,legacy_state,legacy_public_key):
        """Record evidence only. Original state/key stay untouched and authoritative.

        Legacy OXP3 inline generations are not interchangeable with Boiler state.
        An actual translation needs verified original/applied snapshots and is
        deliberately not claimed by this bridge.
        """
        with self._lock():
            evidence={"schema_version":1,"kind":"LegacyCompatibilityReport","migration_complete":False,
                      "status":"legacy-preserved","files":[]}
            for role,value in (("state",legacy_state),("trusted_public_key",legacy_public_key)):
                path=Path(value)
                if path.is_symlink() or not path.is_file(): raise MaintenanceError("Legacy evidence missing or unsafe")
                data=path.read_bytes()
                evidence["files"].append({"role":role,"path":str(path.resolve()),"sha256":hashlib.sha256(data).hexdigest(),"size":len(data)})
            _json_write(self.state_dir/"legacy-compatibility.json",evidence)
            return evidence

    def adopt(self,receipt_path):
        """Accept a protected model-adapter receipt without changing legacy files or keys."""
        receipt,receipt_digest=_adoption_receipt(receipt_path)
        if receipt.get('schema_version') == 2:
            from .adoption_blobs import adopt
            return adopt(self,receipt_path,receipt,receipt_digest)
        if (receipt.get("schema_version")!=1 or receipt.get("kind")!="LegacyAdoptionReceipt"
            or receipt.get("device_id")!="oxp3" or receipt.get("preserve_legacy") is not True
            or receipt.get("translation_complete") is not True): raise MaintenanceError("Unsupported adoption receipt")
        trusted=receipt["trusted_public_key"]
        public_key=Path(__file__).resolve().parents[1]/"protocol/trusted-public-key.pem"
        if trusted["sha256"]!=hashlib.sha256(public_key.read_bytes()).hexdigest():
            raise MaintenanceError("Legacy signing anchor differs; implicit key replacement forbidden")
        with self._lock():
            self._recover(); state=self._state(); prepared=[]; targets=set(); existing=[]
            for evidence in receipt["evidence"]:
                path=confined(self.root,evidence["path"]); verify_file(path,evidence)
                if os.name!="nt" and evidence.get("role")!="trusted-public-key":
                    attributes=path.stat()
                    if attributes.st_uid!=0 or stat.S_IMODE(attributes.st_mode)&0o022:
                        raise MaintenanceError("Legacy evidence can be modified by another user")
            for entry in receipt["components"]:
                component={"schema_version":1,"kind":"ComponentManifest","id":entry["id"],"version":entry["version"],"files":[]}
                validate_manifest(component,"ComponentManifest")
                if component["id"] in state["components"]:
                    active=_read(self._journal(state["components"][component["id"]]["transaction"]))
                    if (active.get("adoption_receipt")!=receipt_digest or active.get("status")!="committed"
                        or active.get("component")!=component or active.get("operations")!=entry["operations"]):
                        raise MaintenanceError("Component already installed from a different receipt")
                    for item in entry["operations"]:
                        if not _same(_snapshot(self._target(item["target"])),item["after"]): raise MaintenanceError("Previously adopted file changed")
                    existing.append(active["id"])
                    continue
                for item in entry["operations"]:
                    target=item["target"]; path=self._target(target)
                    if target in targets or target in state["owners"]: raise MaintenanceError("Adoption ownership conflict")
                    targets.add(target)
                    for snapshot in (item["before"],item["after"]):
                        if type(snapshot.get("exists")) is not bool: raise MaintenanceError("Invalid snapshot")
                        if snapshot["exists"]:
                            data=base64.b64decode(snapshot["data"],validate=True)
                            if len(data)>128*1024**2 or hashlib.sha256(data).hexdigest()!=snapshot["sha256"]: raise MaintenanceError("Invalid snapshot hash")
                            if any(type(snapshot.get(k)) is not int or snapshot[k]<0 for k in ("mode","uid","gid")): raise MaintenanceError("Invalid snapshot metadata")
                    if not _same(_snapshot(path),item["after"]): raise MaintenanceError("Legacy file changed after translation")
                prepared.append((component,entry["operations"]))
            transactions=list(existing)
            for component,operations in prepared:
                identity=uuid.uuid4().hex; previous=json.loads(json.dumps(state))
                journal={"schema_version":1,"id":identity,"root":self.identity,"status":"adopting","component":component,
                    "operations":operations,"before_state":previous,"adoption_evidence":receipt["evidence"],"adoption_receipt":receipt_digest}
                path=self._journal(identity); _json_write(path,journal)
                for item in operations:
                    state["owners"][item["target"]]={"component":component["id"],"transaction":identity,"sha256":item["after"].get("sha256")}
                state["components"][component["id"]]={"version":component["version"],"transaction":identity,"requires":[],"conflicts":[]}
                _json_write(self.state_dir/"state.json",state)
                journal["status"]="committed"; _json_write(path,journal); transactions.append(identity)
            return {"migration_complete":True,"scope":"verified-file-ownership","preserve_legacy":True,"transactions":transactions}
