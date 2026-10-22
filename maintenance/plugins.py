"""Bounded, cache-free maintenance of Boiler's known Decky transactions.

Only the original plugin installer's private journals are accepted. Recorded
absolute mount paths are matched against fixed target suffixes, then rebound to
the explicitly mounted root. List and verify never create files or take locks.
Restoration retains removed code in its private epoch for crash-safe retries.
"""
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import uuid

from .engine import MaintenanceError
from protocol.manifest import confined


STORE = "var/lib/boiler/transactions/plugins"
CODE = "var/lib/boiler/decky/runtime"
UNIT = "etc/systemd/system/plugin_loader.service"
ENABLE = "etc/systemd/system/multi-user.target.d/50-boiler-decky.conf"
MARKER = "home/deck/.local/share/Steam/.cef-enable-remote-debugging"
SLOTS = "home/.boiler/decky/slots"
SUPPORTED = {"decky": ("core:decky", "services"),
             "steamgriddb": ("plugin:steamgriddb", "plugins/decky-steamgriddb"),
             "protondb": ("plugin:protondb", "plugins/protondb-decky")}
PHASES = {"prepared", "failed", "files_installed", "restoring", "rolled_back"}
FILE_PHASES = {"prepared", "installing", "installed", "failed", "partial_restore", "rolled_back"}
TREE_PHASES = {"prepared", "installing", "installed", "restoring", "rolled_back"}
MAX_JSON = 8 * 1024**2
MAX_TREE = 512 * 1024**2


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value: raise MaintenanceError("Duplicate plugin journal key")
        value[key] = item
    return value


def _metadata(info):
    return {"mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}


def _equal(left, right):
    return left["exists"] == right["exists"] and (not left["exists"] or
        all(left.get(key) == right.get(key) for key in
            (("data",) if os.name == "nt" else ("data", "mode", "uid", "gid"))))


def _decode_snapshot(value):
    if not isinstance(value, dict) or type(value.get("exists")) is not bool:
        raise MaintenanceError("Invalid plugin original snapshot")
    if not value["exists"]: return {"exists": False}
    if any(type(value.get(k)) is not int or not 0 <= value[k] <= 2**32-1 for k in ("mode", "uid", "gid")) or value["mode"] > 0o7777:
        raise MaintenanceError("Invalid plugin snapshot metadata")
    try: data = base64.b64decode(value["data"], validate=True)
    except (KeyError, ValueError, TypeError) as error:
        raise MaintenanceError("Invalid plugin snapshot bytes") from error
    if len(data) > 1024**2: raise MaintenanceError("Plugin snapshot exceeds limit")
    return {"exists": True, "data": data, **{k: value[k] for k in ("mode", "uid", "gid")}}


class PluginMaintenance:
    def __init__(self, root):
        self.root = Path(root).resolve()
        if not self.root.is_dir(): raise MaintenanceError("Plugin target root is not mounted")
        self.directory = self._path(STORE)

    def _path(self, name):
        return confined(self.root, name)

    def _protected(self, path, *, private=False):
        """Private records and code ancestors must not be replaceable by users."""
        cursor = Path(path)
        while cursor.is_relative_to(self.root):
            info = cursor.lstat()
            if stat.S_ISLNK(info.st_mode) or (hasattr(cursor, "is_junction") and cursor.is_junction()):
                raise MaintenanceError("Plugin path contains a link")
            if os.name != "nt" and (info.st_uid != 0 or info.st_mode & (0o077 if private and cursor == path else 0o022)):
                raise MaintenanceError("Plugin state/code is not root-owned and protected")
            if cursor == self.root: break
            cursor = cursor.parent

    @staticmethod
    def _directory_fd(path):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        path = Path(path).absolute()
        current = os.open(path.anchor, flags)
        try:
            for part in path.parts[1:]:
                child = os.open(part, flags, dir_fd=current)
                os.close(current); current = child
            return current
        except BaseException:
            os.close(current); raise

    def _bytes(self, path, maximum):
        path = self._path(path.relative_to(self.root).as_posix())
        if os.name == "nt":
            with path.open("rb") as stream:
                info = os.fstat(stream.fileno()); data = stream.read(maximum + 1)
        else:
            parent = self._directory_fd(path.parent)
            try:
                descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            finally: os.close(parent)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise MaintenanceError("Plugin file is not a single regular file")
                data = stream.read(maximum + 1)
                after = os.fstat(stream.fileno())
                if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise MaintenanceError("Plugin file changed during inspection")
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or len(data) > maximum:
            raise MaintenanceError("Plugin file is invalid or exceeds limit")
        return data, info

    def _json(self, path):
        self._protected(path, private=True)
        raw, opened = self._bytes(path, MAX_JSON)
        if os.name != "nt" and (opened.st_uid != 0 or opened.st_mode & 0o077):
            raise MaintenanceError("Plugin journal is not private")
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                               parse_constant=lambda _: (_ for _ in ()).throw(MaintenanceError("Invalid JSON constant")))
        except (ValueError, UnicodeError, RecursionError) as error:
            raise MaintenanceError("Invalid plugin journal") from error
        if not isinstance(value, dict): raise MaintenanceError("Plugin journal must be an object")
        return value

    def _snapshot(self, relative):
        path = self._path(relative)
        if not path.exists(): return {"exists": False}
        raw, info = self._bytes(path, 1024**2)
        return {"exists": True, "data": raw, **_metadata(info)}

    def _write(self, path, data, metadata=None):
        path = self._path(path.relative_to(self.root).as_posix())
        temporary = ".boiler-plugin-" + uuid.uuid4().hex
        if os.name == "nt":
            staging = path.parent / temporary
            try:
                with staging.open("xb") as stream:
                    stream.write(data); stream.flush(); os.fsync(stream.fileno())
                os.replace(staging, path)
            finally:
                if staging.exists(): staging.unlink()
            return
        parent = self._directory_fd(path.parent)
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data); stream.flush()
                os.fchmod(stream.fileno(), metadata["mode"] if metadata else 0o600)
                os.fchown(stream.fileno(), metadata["uid"] if metadata else 0, metadata["gid"] if metadata else 0)
                os.fsync(stream.fileno())
            os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent); os.fsync(parent)
        finally:
            try: os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError: pass
            os.close(parent)

    def _save(self, path, value):
        self._write(path, (json.dumps(value, sort_keys=True) + "\n").encode())

    def _pointer(self, plugin):
        if plugin not in SUPPORTED: raise MaintenanceError("Unsupported plugin; only known Boiler transactions can be managed")
        path = self._path(STORE + "/" + plugin + ".json")
        if not path.exists(): return None
        self._protected(self.directory, private=True)
        value = self._json(path)
        if value.get("id") != plugin or not isinstance(value.get("epoch"), str) or not re.fullmatch(r"[0-9a-f]{32}", value["epoch"]) or value.get("phase") not in PHASES:
            raise MaintenanceError("Invalid plugin operation pointer")
        epoch = self._path(STORE + "/" + value["epoch"])
        self._protected(epoch, private=True)
        return path, value, epoch

    def list(self):
        if not self.directory.exists(): return {"components": [], "unsupported": []}
        self._protected(self.directory, private=True)
        names = []
        for count, path in enumerate(self.directory.iterdir(), 1):
            if count > 10000: raise MaintenanceError("Plugin operation count exceeds limit")
            if path.suffix == ".json": names.append(path.name)
        result = []
        for plugin in SUPPORTED:
            record = self._pointer(plugin)
            if record:
                _, value, _ = record
                result.append({"id": SUPPORTED[plugin][0], "plugin": plugin, "phase": value["phase"],
                               "epoch": value["epoch"], "runtime": value.get("runtime", "not_validated")})
        return {"components": result, "unsupported": sorted(set(names) - {key + ".json" for key in SUPPORTED})}

    def _tree(self, path):
        path = self._path(path.relative_to(self.root).as_posix())
        if not path.exists(): return None
        self._protected(path)
        result = {}; total = 0; pending = [path]
        while pending:
            item = pending.pop(); info = item.lstat()
            if len(result) >= 10000: raise MaintenanceError("Plugin code file count exceeds limit")
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)) or (os.name != "nt" and (info.st_uid != 0 or info.st_mode & 0o022)):
                raise MaintenanceError("Plugin code contains an unsafe file")
            relative = item.relative_to(path).as_posix()
            entry = {"directory": stat.S_ISDIR(info.st_mode), **_metadata(info)}
            if entry["directory"]:
                for child in item.iterdir():
                    if len(pending) + len(result) >= 10000: raise MaintenanceError("Plugin code file count exceeds limit")
                    pending.append(child)
            else:
                if info.st_nlink != 1: raise MaintenanceError("Plugin code contains a hard link")
                total += info.st_size
                if total > MAX_TREE: raise MaintenanceError("Plugin code exceeds size limit")
                raw, opened = self._bytes(item, MAX_TREE - total + info.st_size)
                if _metadata(opened) != _metadata(info): raise MaintenanceError("Plugin code metadata changed")
                entry.update(sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))
            result[relative] = entry
        return result

    @staticmethod
    def _tree_metadata(tree):
        return {name: {key: item[key] for key in ("directory", "mode", "uid", "gid")} for name, item in tree.items()}

    @staticmethod
    def _tree_equal(first, second):
        if os.name != "nt": return first == second
        def contents(tree):
            if tree is None: return None
            return {name: {k: v for k, v in item.items() if k not in ("mode", "uid", "gid")} for name, item in tree.items()}
        return contents(first) == contents(second)

    @staticmethod
    def _recorded_root(recorded, relative):
        suffix = "/" + relative
        if not isinstance(recorded, str) or not recorded.startswith("/") or "\\" in recorded or any(ord(c) < 32 for c in recorded) or not recorded.endswith(suffix):
            raise MaintenanceError("Plugin transaction destination is outside the known allowlist")
        prefix = recorded[:-len(suffix)]
        if prefix and any(part in ("", ".", "..") for part in prefix[1:].split("/")):
            raise MaintenanceError("Invalid recorded plugin mount root")
        return prefix

    def _code_plan(self, plugin, epoch):
        state_path = epoch / "code/state.json"
        if not state_path.exists(): return None
        state = self._json(state_path)
        relative = CODE + "/" + SUPPORTED[plugin][1]
        prefix = self._recorded_root(state.get("destination"), relative)
        if state.get("phase") not in TREE_PHASES or type(state.get("original_exists")) is not bool:
            raise MaintenanceError("Invalid plugin directory journal")
        installed = self._tree(epoch / "code/installed")
        original = self._tree(epoch / "code/original-reference")
        if installed is None or self._tree_metadata(installed) != state.get("installed_metadata"):
            raise MaintenanceError("Plugin installed reference is missing or modified")
        if state["original_exists"] and (original is None or self._tree_metadata(original) != state.get("original_metadata")):
            raise MaintenanceError("Plugin original reference is missing or modified")
        if not state["original_exists"] and original is not None: raise MaintenanceError("Unexpected plugin original reference")
        current = self._tree(self._path(relative))
        restored = self._tree_equal(current, original)
        quarantined = self._tree(epoch / "code/boiler-maintenance-removed")
        if quarantined is not None and not self._tree_equal(quarantined, installed): raise MaintenanceError("Plugin restore quarantine was modified")
        if not restored:
            if not self._tree_equal(current, installed) and not (current is None and state["phase"] in ("prepared", "installing", "restoring")):
                raise MaintenanceError("Installed plugin code changed; preserving user changes")
            if state["original_exists"] and not self._tree_equal(self._tree(epoch / "code/original"), original):
                raise MaintenanceError("Plugin original directory backup is missing or modified")
        return {"path": state_path, "state": state, "relative": relative, "prefix": prefix,
                "installed": installed, "original": original, "restored": restored}

    def _file_plan(self, epoch, group, prefix):
        state_path = epoch / group / "state.json"
        if not state_path.exists(): return None
        state = self._json(state_path)
        allowed = state.get("allowed")
        expected = {prefix + "/" + UNIT} if group == "unit" else {prefix + "/" + ENABLE, prefix + "/" + MARKER}
        if (state.get("phase") not in FILE_PHASES or not isinstance(allowed, list) or not allowed or
                len(set(allowed)) != len(allowed) or not set(allowed).issubset(expected) or
                (group == "unit" and set(allowed) != expected) or
                (group == "activation" and prefix + "/" + ENABLE not in allowed) or
                not isinstance(state.get("original"), dict) or set(state["original"]) != set(allowed) or
                not isinstance(state.get("applied"), dict) or not set(state["applied"]).issubset(allowed)):
            raise MaintenanceError("Plugin file journal allowlist is invalid")
        owner = None; operations = []
        for recorded in allowed:
            relative = recorded[len(prefix) + 1:]; path = self._path(relative)
            before = _decode_snapshot(state["original"][recorded])
            if recorded not in state["applied"]:
                if not _equal(self._snapshot(relative), before): raise MaintenanceError("Unwritten plugin target changed")
                continue
            encoded = state["applied"][recorded]
            metadata = {key: before[key] for key in ("mode", "uid", "gid")} if before["exists"] else {"mode": 0o644, "uid": 0, "gid": 0}
            if not before["exists"]:
                parent = path.parent.stat()
                if parent.st_mode & stat.S_ISGID: metadata["gid"] = parent.st_gid
            if relative == MARKER:
                if owner is None:
                    passwd, _ = self._bytes(self._path("etc/passwd"), 1024**2)
                    rows = [row.split(":") for row in passwd.decode().splitlines() if row.startswith("deck:")]
                    if len(rows) != 1 or len(rows[0]) != 7 or rows[0][5] != "/home/deck": raise MaintenanceError("Expected one deck account")
                    owner = int(rows[0][2]), int(rows[0][3])
                    if min(owner) <= 0: raise MaintenanceError("Deck account cannot be privileged")
                metadata.update(uid=owner[0], gid=owner[1])
            try:
                after = {"exists": False} if encoded is None else {"exists": True, "data": base64.b64decode(encoded, validate=True), **metadata}
                accepted = [{"exists": False} if data is None else {"exists": True, "data": base64.b64decode(data, validate=True), **metadata}
                            for data in state.get("accepted", {}).get(recorded, [])]
            except (TypeError, ValueError) as error: raise MaintenanceError("Invalid plugin applied snapshot") from error
            current = self._snapshot(relative)
            if not any(_equal(current, known) for known in [before, after, *accepted]):
                raise MaintenanceError("Plugin configuration changed; preserving user changes")
            operations.append({"relative": relative, "before": before, "after": after, "accepted": accepted,
                               "restored": _equal(current, before), "verified": _equal(current, after)})
        return {"path": state_path, "state": state, "operations": operations}

    def _plan(self, plugin):
        record = self._pointer(plugin)
        if record is None: raise MaintenanceError("No known Boiler plugin transaction")
        pointer, value, epoch = record
        code = self._code_plan(plugin, epoch)
        prefix = code["prefix"] if code else None
        files = []
        if plugin == "decky":
            # A failure before directory preparation cannot have installed unit/activation files.
            if prefix is None and any((epoch / group / "state.json").exists() for group in ("unit", "activation")):
                raise MaintenanceError("Plugin file transaction lacks its directory anchor")
            if prefix is not None:
                files = [plan for group in ("activation", "unit") if (plan := self._file_plan(epoch, group, prefix)) is not None]
        if value["phase"] == "files_installed" and (code is None or plugin == "decky" and len(files) != 2):
            raise MaintenanceError("Installed plugin transaction journals are incomplete")
        return {"pointer": pointer, "value": value, "epoch": epoch, "code": code, "files": files}

    def verify(self, plugin):
        plan = self._plan(plugin)
        files = plan["files"]; code = plan["code"]
        complete = (plan["value"]["phase"] == "files_installed" and code is not None and
                    code["state"]["phase"] == "installed" and self._tree_equal(self._tree(self._path(code["relative"])), code["installed"]) and
                    (plugin != "decky" or len(files) == 2) and
                    all(file["state"]["phase"] == "installed" and all(op["verified"] for op in file["operations"]) for file in files))
        return {"id": SUPPORTED[plugin][0], "plugin": plugin, "ok": complete,
                "phase": plan["value"]["phase"], "runtime": "not_validated"}

    @contextlib.contextmanager
    def _lock(self):
        self._protected(self.directory, private=True)
        path = self._path(STORE + "/.lock")
        if os.name == "nt":
            import msvcrt
            with path.open("a+b") as stream:
                if stream.seek(0, 2) == 0: stream.write(b"0"); stream.flush()
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                try: yield
                finally: stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            parent = self._directory_fd(self.directory)
            try: lock = os.open(".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent)
            finally: os.close(parent)
            try:
                info = os.fstat(lock)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0 or info.st_mode & 0o077:
                    raise MaintenanceError("Untrusted plugin operation lock")
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield
            finally: os.close(lock)

    def _slot_guard(self, plan):
        directory = self._path(SLOTS)
        marker_operations = [operation for group in plan["files"] for operation in group["operations"] if operation["relative"] == MARKER]
        if not directory.exists():
            if "slot" in plan["value"]: raise MaintenanceError("Decky slot ownership registry is missing")
            # Old transactions have no proof that the other slot has stopped
            # using this shared home file. Restore the rest, report the residue.
            return {"retained": {MARKER} if any(not op["restored"] for op in marker_operations) else set(), "reference": None}
        self._protected(directory, private=True)
        references = []
        for count, path in enumerate(directory.iterdir(), 1):
            if count > 2 or path.name not in ("A.json", "B.json"):
                raise MaintenanceError("Unknown Decky slot ownership record")
            value = self._json(path)
            slot = path.stem
            if (value.get("schema") != 1 or value.get("slot") != slot or value.get("marker") != MARKER or
                    type(value.get("owns_marker")) is not bool or not isinstance(value.get("epoch"), str) or
                    not re.fullmatch(r"[0-9a-f]{32}", value["epoch"])):
                raise MaintenanceError("Invalid Decky slot ownership record")
            references.append((path, value))
        own = [(path, value) for path, value in references if value["epoch"] == plan["value"]["epoch"]]
        if len(own) != 1: raise MaintenanceError("Exact Decky slot ownership record is missing or ambiguous")
        path, value = own[0]
        if "slot" in plan["value"] and plan["value"]["slot"] != value["slot"]:
            raise MaintenanceError("Decky slot and operation journal disagree")
        owns_marker = any(not operation["before"]["exists"] for operation in marker_operations)
        if value["owns_marker"] != owns_marker: raise MaintenanceError("Decky marker ownership journal disagrees")
        if value["owns_marker"] and any(other["slot"] != value["slot"] for _, other in references):
            raise MaintenanceError("Other SteamOS slot still requires the shared Decky marker; restore that slot first")
        return {"retained": set(), "reference": (path, value)}

    def _retire_slot(self, reference):
        if reference is None: return
        path, expected = reference
        if self._json(path) != expected: raise MaintenanceError("Decky slot ownership changed during restoration")
        if os.name == "nt": path.unlink()
        else:
            parent = self._directory_fd(path.parent)
            try: os.unlink(path.name, dir_fd=parent); os.fsync(parent)
            finally: os.close(parent)

    def _restore_files(self, plan, retained):
        errors = []
        for operation in plan["operations"]:
            current = self._snapshot(operation["relative"])
            if _equal(current, operation["before"]): continue
            if operation["relative"] in retained:
                errors.append({"path": operation["relative"], "error": "Shared marker retained: sibling slot ownership is unknown"})
                continue
            if not any(_equal(current, known) for known in [operation["after"], *operation["accepted"]]):
                raise MaintenanceError("Plugin target changed during restore")
            path = self._path(operation["relative"]); before = operation["before"]
            if before["exists"]: self._write(path, before["data"], before)
            elif os.name == "nt": path.unlink()
            else:
                parent = self._directory_fd(path.parent)
                try: os.unlink(path.name, dir_fd=parent); os.fsync(parent)
                finally: os.close(parent)
        plan["state"]["phase"] = "partial_restore" if errors else "rolled_back"; plan["state"]["restore_errors"] = errors
        self._save(plan["path"], plan["state"])

    def _restore_code(self, plugin, plan):
        # Recheck after configuration restoration and before directory renames.
        plan = self._code_plan(plugin, plan["epoch"])
        if plan is None: return
        if plan["restored"]:
            if plan["state"]["phase"] != "rolled_back":
                plan["state"]["phase"] = "rolled_back"; self._save(plan["path"], plan["state"])
            return
        state = plan["state"]; state["phase"] = "restoring"; self._save(plan["path"], state)
        destination = self._path(plan["relative"]); directory = plan["path"].parent
        removed = directory / "boiler-maintenance-removed"
        if destination.exists():
            if removed.exists(): raise MaintenanceError("Plugin restore quarantine already exists")
            os.rename(destination, removed)
            self._sync(destination.parent); self._sync(directory)
        if state["original_exists"]:
            os.rename(directory / "original", destination)
            self._sync(directory); self._sync(destination.parent)
        state["phase"] = "rolled_back"; self._save(plan["path"], state)

    @staticmethod
    def _sync(path):
        if os.name != "nt":
            descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)

    def restore(self, plugin):
        if os.name != "nt" and os.geteuid() != 0: raise MaintenanceError("Plugin restoration requires root")
        if not self.directory.exists(): raise MaintenanceError("No known Boiler plugin transaction")
        with self._lock():
            plan = self._plan(plugin)
            if plugin == "decky":
                inventory = self.list()
                if inventory["unsupported"] or any(row["plugin"] != "decky" and row["phase"] != "rolled_back" for row in inventory["components"]):
                    raise MaintenanceError("Restore dependent plugins before Decky; unknown plugin journals require inspection")
            if plan["value"]["phase"] == "rolled_back":
                if plan["code"] and not plan["code"]["restored"] or any(not op["restored"] for group in plan["files"] for op in group["operations"]):
                    raise MaintenanceError("Previously restored plugin targets changed")
                # Complete a crash between the durable pointer and slot unlink.
                if plugin == "decky" and self._path(SLOTS).exists():
                    self._protected(self._path(SLOTS), private=True)
                    matches = []
                    for slot in ("A", "B"):
                        own_path = self._path(SLOTS + "/" + slot + ".json")
                        if own_path.exists():
                            reference = self._json(own_path)
                            if reference.get("epoch") == plan["value"]["epoch"]:
                                if reference.get("slot") != slot or plan["value"].get("slot", slot) != slot:
                                    raise MaintenanceError("Restored Decky slot reference disagrees")
                                matches.append((own_path, reference))
                    if len(matches) > 1: raise MaintenanceError("Ambiguous restored Decky slot reference")
                    if matches: self._retire_slot(matches[0])
                return {"id": SUPPORTED[plugin][0], "status": "restored", "service_stopped": False}
            shared = self._slot_guard(plan) if plugin == "decky" else {"retained": set(), "reference": None}
            live = os.name != "nt" and self.root == Path("/")
            if live:
                subprocess.run(["/usr/bin/systemctl", "stop", "plugin_loader.service"], check=True, timeout=60,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            plan["value"]["phase"] = "restoring"; self._save(plan["pointer"], plan["value"])
            for group in plan["files"]: self._restore_files(group, shared["retained"])
            self._restore_code(plugin, plan)
            if shared["retained"]:
                plan["value"]["retained_shared_state"] = sorted(shared["retained"])
                self._save(plan["pointer"], plan["value"])
                return {"id": SUPPORTED[plugin][0], "status": "partial_restore", "service_stopped": live,
                        "retained_shared_state": sorted(shared["retained"]), "scope": "managed code and unit restored; shared marker retained"}
            plan["value"]["phase"] = "rolled_back"; plan["value"].pop("error", None)
            plan["value"].pop("retained_shared_state", None)
            self._save(plan["pointer"], plan["value"])
            self._retire_slot(shared["reference"])
            return {"id": SUPPORTED[plugin][0], "status": "restored", "service_stopped": live,
                    "scope": "managed-files; user-created state directories preserved"}
