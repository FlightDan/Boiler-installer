"""Create installer-owned directories without traversing symlink ancestors."""
from pathlib import Path
import os
import stat


def ensure_directory(path, mode=0o755):
    path = Path(path).absolute()
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink(): raise ValueError('Directory ancestor is a link')
        if not current.exists(): current.mkdir(mode=mode)
        if not stat.S_ISDIR(current.lstat().st_mode): raise ValueError('Expected directory')
    return path
