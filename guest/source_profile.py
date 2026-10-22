"""Read actual SteamOS metadata without imposing a model's kernel/version pin."""
from pathlib import Path, PurePosixPath


def image_path(root, relative):
    """Read image metadata through native links without following host paths."""
    root = Path(root)
    parts = PurePosixPath(relative)
    if parts.is_absolute() or any(part in ('', '.', '..') for part in relative.split('/')):
        raise ValueError('Unsafe image metadata path')
    pending, resolved, links = list(parts.parts), [], 0
    while pending:
        part = pending.pop(0)
        if part in ('', '.'): continue
        if part == '..':
            if not resolved: raise ValueError('Image metadata link escapes image root')
            resolved.pop(); continue
        if '\\' in part or ':' in part: raise ValueError('Unsafe image metadata link')
        candidate = root.joinpath(*resolved, part)
        if candidate.is_symlink():
            links += 1
            if links > 40: raise ValueError('Image metadata link cycle')
            target = PurePosixPath(candidate.readlink().as_posix())
            if target.is_absolute(): resolved = []; target_parts = target.parts[1:]
            else: target_parts = target.parts
            pending = list(target_parts) + pending
        else:
            if hasattr(candidate, 'is_junction') and candidate.is_junction(): raise ValueError('Image metadata junction is unsupported')
            resolved.append(part)
    return root.joinpath(*resolved)


def release_values(root):
    release = image_path(root, 'etc/os-release')
    if not release.is_file(): raise ValueError('Source release metadata is not a regular file')
    with release.open('rb') as stream: data = stream.read(65537)
    if len(data) > 65536: raise ValueError('Source release metadata exceeds inspection limit')
    values = {}
    for line in data.decode('utf-8').splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            if key in values: raise ValueError('Duplicate source release metadata')
            values[key] = value.strip('"\'')
    if values.get('ID') not in ('steamos', 'holo'):
        raise ValueError('Source is not SteamOS')
    if any(not values.get(key) or any(c in values[key] for c in '\r\n:') for key in ('VERSION_ID', 'BUILD_ID')):
        raise ValueError('Source version/build metadata missing or invalid')
    return values


def inspect_source(root):
    root = Path(root)
    values = release_values(root)
    modules = root / 'usr/lib/modules'
    if modules.is_symlink() or not modules.is_dir(): raise ValueError('Source kernel modules missing')
    kernels = sorted(path.name for path in modules.iterdir() if path.is_dir() and not path.is_symlink())
    if not kernels: raise ValueError('Source has no installed kernel')
    return dict(id=values['ID'], version=values['VERSION_ID'], build=values['BUILD_ID'], kernels=kernels)
