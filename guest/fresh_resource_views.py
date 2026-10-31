"""Read-only official mount policy for an uninitialized prepared image.

This describes future physical ownership; it never creates an upper/work or
offload directory. The trusted mount owner must activate and verify the views.
"""
import hashlib
import os
from pathlib import Path
from protocol.manifest import confined
from maintenance.resource_runtime import ResourceError
from maintenance.engine import MaintenanceError
from offload_mounts import etc_overlay_options


def _directory(path):
    from maintenance.adoption_blobs import _directory as held_directory
    with held_directory(path): pass


def _exists_safe(path):
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise ResourceError('Redirected fresh mount policy path')
    return path.exists()


def _optional_directory(path, boundary):
    """Validate existing ancestry even when initialization has not made a leaf."""
    present = _exists_safe(path)
    existing = path
    while not existing.exists(): existing = existing.parent
    try:
        _directory(existing)
        if os.name == 'posix':
            for current in (existing, *existing.parents):
                if not current.is_relative_to(boundary): break
                info = current.stat(follow_symlinks=False)
                # Sticky /tmp outside the target remains allowed by the shared
                # walker. Image-owned mount backing storage must be private.
                if info.st_uid != 0 or info.st_mode & 0o022:
                    raise ResourceError('Unsafe existing mount backing ancestor')
    except (OSError, MaintenanceError) as error:
        raise ResourceError('Unsafe existing mount backing ancestor') from error
    return present


def inspect(root, home):
    root, home = Path(root).absolute(), Path(home).absolute()
    if root == Path(root.anchor) or home == Path(home.anchor):
        raise ResourceError('A mounted image root and shared home are required')
    _directory(root); _directory(home)
    result = dict(etc_overlay=False, fresh_etc=False, opt_offload=False, fresh_opt=False, units={})
    for kind in ('etc', 'opt'):
        unit = confined(root, 'usr/lib/systemd/system/'+kind+'.mount')
        if not unit.is_file(): continue
        from maintenance.adoption_blobs import _file
        with _file(unit) as (stream,info):
            if info.st_size > 16384: raise ResourceError('Unexpected mount unit size')
            raw = stream.read(16385)
        text = raw.decode()
        result['units'][kind] = hashlib.sha256(raw).hexdigest()
        override = root/'etc/systemd/system'/(kind+'.mount')
        if kind == 'opt' and override.is_symlink() and str(override.readlink()) == '/dev/null':
            continue
        for candidate in (override, override.with_name(kind+'.mount.d'), unit.with_name(kind+'.mount.d')):
            if candidate.exists() or candidate.is_symlink():
                raise ResourceError('Custom mount policy requires review')
        if kind == 'etc':
            etc_overlay_options(text)
            _directory(root/'etc')
            upper = root/'var/lib/overlays/etc/upper'
            work = root/'var/lib/overlays/etc/work'
            present = _optional_directory(upper, root/'var')
            work_present = _optional_directory(work, root/'var')
            if not present and work_present:
                with os.scandir(work) as children:
                    if next(children, None) is not None:
                        raise ResourceError('Uninitialized etc has nonempty work storage; preserve it for review')
            for candidate in (upper/'systemd/system/etc.mount', upper/'systemd/system/etc.mount.d'):
                if candidate.exists() or candidate.is_symlink():
                    raise ResourceError('Custom persistent overlay requires review')
            result.update(etc_overlay=True, fresh_etc=not present)
        else:
            if not all(line in text.splitlines() for line in
                       ('What=/home/.steamos/offload/opt', 'Where=/opt', 'Options=bind')):
                raise ResourceError('Unrecognized SteamOS opt mount policy')
            source = home/'.steamos/offload/opt'
            present = _optional_directory(source, home)
            _directory(root/'opt')
            result.update(opt_offload=True, fresh_opt=not present)
    return result


def verify_active(root, home, expected):
    """Called after the existing mount owner activates every slot's views."""
    current = inspect(root, home)
    if current['units'] != expected['units'] or current['opt_offload'] != expected['opt_offload']:
        raise ResourceError('Official mount policy changed after unified review')
    if expected['etc_overlay']:
        if current['fresh_etc'] or not Path(root, 'etc').is_mount():
            raise ResourceError('The persistent etc view was not activated')
    if expected['opt_offload']:
        source, target = Path(home)/'.steamos/offload/opt', Path(root)/'opt'
        if current['fresh_opt'] or not target.is_mount() or not os.path.samestat(source.stat(), target.stat()):
            raise ResourceError('Shared opt physical binding differs from reviewed policy')
