"""Inline the reviewed locale helper into the self-contained Decky entry files."""
import argparse
from pathlib import Path


HELPER = 'common/plugin-i18n/decky-locale.mjs'
PLUGINS = (
    'brands/onexplayer/common/plugin-i18n/battery/index.js',
    'brands/onexplayer/oxp3/current/plugin-i18n/cpu/index.js',
    'brands/onexplayer/oxp3/current/plugin-i18n/fan/index.js',
    'brands/onexplayer/oxp3/current/plugin-i18n/light/index.js',
    'common/plugin-i18n/fingerprint/index.js',
)
START = b'// BEGIN BOILER PLUGIN LOCALE'
END = b'// END BOILER PLUGIN LOCALE'


def refresh(root, check):
    helper = (root / HELPER).read_bytes().strip().replace(b'export function ', b'function ')
    block = START + b'\n' + helper + b'\n' + END
    for member in PLUGINS:
        path = root / member
        source = path.read_bytes()
        if source.count(START) != 1 or source.count(END) != 1:
            raise ValueError('Expected one locale block: ' + member)
        start = source.index(START)
        end = source.index(END) + len(END)
        if end <= start:
            raise ValueError('Locale block markers out of order: ' + member)
        updated = source[:start] + block + source[end:]
        if source == updated:
            continue
        if check:
            raise ValueError('Stale locale block: ' + member)
        path.write_bytes(updated)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--write', action='store_true')
    mode.add_argument('--check', action='store_true')
    args = parser.parse_args()
    refresh(args.root, args.check)
