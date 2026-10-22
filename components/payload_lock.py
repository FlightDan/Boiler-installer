"""Content locked, local-only payload inputs shared by preparation and guest code."""
import hashlib
from pathlib import Path, PurePosixPath


def relative(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise ValueError('Expected a portable relative payload path')
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ('', '.', '..') for part in value.split('/')):
        raise ValueError('Unsafe payload path: ' + value)
    return path


def regular(base, value):
    path = Path(base)
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()): raise ValueError('Payload base is a link')
    for part in relative(value).parts:
        path = path / part
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()): raise ValueError('Payload links are forbidden')
    if not path.is_file(): raise FileNotFoundError('Missing locked payload: ' + value)
    return path


def checked(base, record):
    data = regular(base, record['source']).read_bytes()
    if type(record.get('size')) is not int or len(data) != record['size']:
        raise ValueError('Locked payload size differs: ' + record['source'])
    digest = record.get('sha256')
    if not isinstance(digest, str) or len(digest) != 64 or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('Locked payload hash differs: ' + record['source'])
    return data


def record(source, data, **extra):
    relative(source)
    return dict(source=source, size=len(data), sha256=hashlib.sha256(data).hexdigest(), **extra)
