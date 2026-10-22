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


def runtime_files():
    root=Path(__file__).resolve().parents[1]
    return [p for name in ("maintenance","protocol") for p in sorted((root/name).iterdir()) if p.suffix in (".py",".pem")]


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
        all(left.get(k)==right.get(k) for k in (("sha256",) if os.name=="nt" else ("sha256","mode","uid","gid"))))


class Engine:
    def __init__(self,root,state_dir=None,*,read_only=False):
        self.root=Path(root).resolve()
        if not self.root.is_dir(): raise MaintenanceError("Target root is not mounted")
        self.state_dir=Path(state_dir).absolute() if state_dir else self.root/"var/lib/boiler/maintenance"
        self.read_only=read_only
        cursor=self.state_dir
        while cursor != cursor.parent:
            if cursor.is_symlink() or (hasattr(cursor,"is_junction") and cursor.is_junction()): raise MaintenanceError("State path contains a link")
            cursor=cursor.parent
        if not read_only: self.state_dir.mkdir(parents=True,exist_ok=True)
        self.state_dir=self.state_dir.resolve()
        self.identity="mounted-target" if self.state_dir.is_relative_to(self.root) else str(self.root)
        self.journals=self.state_dir/"transactions"
        if not read_only: self.journals.mkdir(exist_ok=True)

    @contextlib.contextmanager
    def _lock(self):
        if self.read_only: raise MaintenanceError("Read-only verification cannot modify maintenance state")
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
        if not snapshot["exists"]:
            if path.exists(): path.unlink(); _sync_dir(path.parent)
            return
        data=base64.b64decode(snapshot["data"],validate=True)
        if hashlib.sha256(data).hexdigest()!=snapshot["sha256"]: raise MaintenanceError("Backup digest mismatch")
        _atomic(path,data,snapshot["mode"])
        if os.name!="nt": os.chown(path,snapshot["uid"],snapshot["gid"])

    def _recover(self):
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
                current=_snapshot(self._target(item["target"]))
                if not (_same(current,item["before"]) or _same(current,item["after"])):
                    raise MaintenanceError("Recovery refused modified target: "+item["target"])
            restore_in_progress=journal.get("status")=="restoring"
            for item in reversed(journal["operations"]): self._put_snapshot(self._target(item["target"]),item["before"])
            _json_write(self.state_dir/"state.json",journal["before_state"])
            journal["status"]="restored" if restore_in_progress else "rolled_back"
            _json_write(path,journal); recovered.append(journal["id"])
        return recovered

    def recover(self):
        with self._lock(): return self._recover()

    def apply(self,component,payload_root,*,allow_hooks=False,environment=None,expected_state_sha256=None):
        validate_manifest(component,"ComponentManifest")
        if not compatible(component,environment or {}): raise MaintenanceError("Component target compatibility has not been established")
        payload_root=Path(payload_root).resolve()
        hooks=component.get("hooks",{})
        if hooks:
            if not allow_hooks or os.name=="nt": raise MaintenanceError("Guest hooks require explicit privileged authorization on Linux")
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
            operations=[]; total=0; targets=set()
            for entry in component.get("files",[]):
                target=self._target(entry["target"]); key=entry["target"]
                if key.casefold() in targets: raise MaintenanceError("Duplicate target")
                targets.add(key.casefold())
                for owned,owner in state["owners"].items():
                    if (key.casefold()==owned.casefold() or key.casefold().startswith(owned.casefold()+"/") or owned.casefold().startswith(key.casefold()+"/")) and owner["component"]!=component["id"]:
                        raise MaintenanceError("Target owned by another component")
                source=confined(payload_root,entry["source"]); verify_file(source,entry)
                before=_snapshot(target); data=source.read_bytes(); total+=len(data)+(len(base64.b64decode(before["data"])) if before["exists"] else 0)
                if hashlib.sha256(data).hexdigest()!=entry["sha256"] or len(data)!=entry["size"]:
                    raise MaintenanceError("Payload changed after verification")
                owner=state["owners"].get(key)
                if owner:
                    prior=_read(self._journal(owner["transaction"]))
                    recorded=next((op["after"] for op in prior["operations"] if op["target"]==key),None)
                    if recorded is None or not _same(before,recorded): raise MaintenanceError("Upgrade refused modified managed file")
                if total>256*1024**2: raise MaintenanceError("Transaction exceeds backup limit")
                uid=before.get("uid",os.getuid() if hasattr(os,"getuid") else 0)
                gid=before.get("gid",os.getgid() if hasattr(os,"getgid") else 0)
                after={"exists":True,"data":base64.b64encode(data).decode(),"sha256":entry["sha256"],"mode":entry.get("mode",420),"uid":uid,"gid":gid}
                operations.append({"target":key,"before":before,"after":after})
            # Fail closed for upgrades that silently drop previously owned paths.
            old_paths={p for p,o in state["owners"].items() if o["component"]==component["id"]}
            if old_paths - {op["target"] for op in operations}: raise MaintenanceError("Upgrade drops owned files; explicit restore is required")
            journal={"schema_version":1,"id":identity,"root":self.identity,"status":"prepared","component":component,
                     "operations":operations,"before_state":state}
            path=self._journal(identity); _json_write(path,journal)
            try:
                journal["status"]="applying"; _json_write(path,journal)
                for item in operations: self._put_snapshot(self._target(item["target"]),item["after"])
                if hooks:
                    journal["status"]="hook_running"; _json_write(path,journal)
                    for phase in ("apply","verify"):
                        if phase in hooks:
                            env=dict(os.environ,BOILER_TARGET_ROOT=str(self.root),BOILER_TRANSACTION_ID=identity)
                            subprocess.run(["/bin/bash",str(confined(payload_root,hooks[phase])),str(self.root)],cwd=payload_root,env=env,check=True,timeout=900)
                    journal["status"]="hook_completed"; _json_write(path,journal)
                # Hooks may not rewrite managed files behind ownership records.
                for item in operations:
                    if not _same(_snapshot(self._target(item["target"])),item["after"]): raise MaintenanceError("Applied file verification failed")
                next_state=json.loads(json.dumps(state))
                for item in operations: next_state["owners"][item["target"]]={"component":component["id"],"transaction":identity,"sha256":item["after"]["sha256"]}
                next_state["components"][component["id"]]={"version":component["version"],"transaction":identity,
                    "requires":component.get("requires",[]),"conflicts":component.get("conflicts",[])}
                _json_write(self.state_dir/"state.json",next_state)
                journal["status"]="committed"; _json_write(path,journal)
            except BaseException:
                if journal["status"] in ("hook_running","hook_completed"):
                    journal["status"]="manual_recovery_required"; _json_write(path,journal)
                else: self._recover()
                raise
            return {"transaction":identity,"component":component["id"],"status":"committed","files":len(operations),
                    "state_sha256":hashlib.sha256(json.dumps(next_state,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()}

    def verify(self,component_id):
        with self._lock():
            self._recover(); state=self._state()
            if component_id not in state["components"]: raise MaintenanceError("Component is not installed")
            journal=_read(self._journal(state["components"][component_id]["transaction"]))
            failures=[op["target"] for op in journal["operations"] if not _same(_snapshot(self._target(op["target"])),op["after"])]
            return {"component":component_id,"ok":not failures,"modified":failures}

    def verify_readonly(self,component_id):
        """For an exclusively mounted read-only image: never lock, recover or mkdir."""
        if not self.read_only: raise MaintenanceError("Read-only verification requires a read-only engine")
        for path in self.journals.glob("*.json"):
            if path.is_symlink(): raise MaintenanceError("Unsafe journal")
            if _read(path).get("status") not in ("committed","restored","rolled_back"):
                raise MaintenanceError("Pending transaction requires writable recovery before verification")
        state=self._state()
        if component_id not in state["components"]: raise MaintenanceError("Component is not installed")
        journal=_read(self._journal(state["components"][component_id]["transaction"]))
        if journal.get("root") != self.identity or journal.get("status") != "committed": raise MaintenanceError("Invalid active journal")
        failures=[op["target"] for op in journal["operations"] if not _same(_snapshot(self._target(op["target"])),op["after"])]
        return {"component":component_id,"ok":not failures,"modified":failures}

    def restore(self,transaction_id):
        with self._lock():
            self._recover(); state=self._state(); path=self._journal(transaction_id); journal=_read(path)
            if journal.get("root")!=self.identity or journal.get("status")!="committed": raise MaintenanceError("Transaction is not restorable")
            cid=journal["component"]["id"]
            if state["components"].get(cid,{}).get("transaction")!=transaction_id: raise MaintenanceError("Only active component generation can be restored")
            if journal["component"].get("hooks"): raise MaintenanceError("Script component requires explicit package restore integration")
            for item in journal["operations"]:
                if not _same(_snapshot(self._target(item["target"])),item["after"]): raise MaintenanceError("Restore refused modified target")
            # Preserve transactions belonging to other components installed later.
            previous=journal["before_state"]
            if cid not in previous["components"] and any(cid in item.get("requires",[]) for key,item in state["components"].items() if key!=cid):
                raise MaintenanceError("Installed component still requires this component")
            for item in journal["operations"]:
                name=item["target"]
                if name in previous["owners"]: state["owners"][name]=previous["owners"][name]
                else: state["owners"].pop(name,None)
            if cid in previous["components"]: state["components"][cid]=previous["components"][cid]
            else: state["components"].pop(cid,None)
            journal["before_state"]=state; journal["status"]="restoring"; _json_write(path,journal)
            for item in reversed(journal["operations"]): self._put_snapshot(self._target(item["target"]),item["before"])
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
        receipt_path=Path(receipt_path)
        if receipt_path.is_symlink() or not receipt_path.is_file(): raise MaintenanceError("Unsafe adoption receipt")
        info=receipt_path.stat()
        if os.name!="nt" and (os.geteuid()!=0 or info.st_uid!=0 or stat.S_IMODE(info.st_mode)&0o077):
            raise MaintenanceError("Adoption requires a root-owned private receipt")
        receipt=_read(receipt_path)
        receipt_digest=hashlib.sha256(receipt_path.read_bytes()).hexdigest()
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
