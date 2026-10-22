"""Prepare Decky from an explicit local lock; no latest-release lookup or cache fallback."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'components'))
from payload_lock import checked, record


def prepare(lock_path, input_root, output):
    lock = json.loads(Path(lock_path).read_text())
    if lock.get('schema') != 1 or lock.get('id') != 'core:decky' or lock.get('architecture') != 'x86_64':
        raise ValueError('Pinned x86_64 Decky lock required')
    if not lock.get('version') or not lock.get('url', '').startswith('https://') or not lock.get('license'):
        raise ValueError('Decky version, HTTPS source and license required')
    data = checked(input_root, lock['file'])
    notices = [(item, checked(input_root, item)) for item in lock.get('notices', [])]
    if not notices: raise ValueError('Decky corresponding source archive required')
    if (len(data) < 1024 * 1024 or data[:6] != b'\x7fELF\x02\x01' or
            int.from_bytes(data[18:20], 'little') != 62 or int.from_bytes(data[16:18], 'little') not in (2, 3)):
        raise ValueError('Decky is not a Linux x86_64 ELF executable')
    output = Path(output)
    if output.exists(): raise FileExistsError('Output exists; use a fresh preparation directory')
    (output / 'decky').mkdir(parents=True)
    (output / 'decky/PluginLoader').write_bytes(data)
    result = {key: lock[key] for key in ('schema', 'id', 'architecture', 'version', 'url', 'license')}
    result.update(available=True, files=[record('decky/PluginLoader', data)])
    result['notices'] = []
    for item, content in notices:
        target = output / 'sources' / item['source']; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(content)
        result['notices'].append(record('sources/' + item['source'], content, url=item.get('url', '')))
    license_file = Path(__file__).resolve().parents[1] / 'components/licenses/decky/LICENSE'
    license_data = license_file.read_bytes()
    (output / 'sources/LICENSE').write_bytes(license_data)
    result['notices'].append(record('sources/LICENSE', license_data))
    (output / 'manifest.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.lock, args.input, args.output), indent=2))
