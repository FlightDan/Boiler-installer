"""Turn an exported installer session into a small, sanitized regression fixture.

Usage:
    python sanitize-install-session.py EXPORT_DIR OUTPUT_DIR

Only text records are copied. Disk images, binaries and UI logs are excluded.
Serial numbers, GUIDs, user/profile paths and request nonces are replaced with
deterministic fixtures; sizes, offsets, partition numbers and timestamps stay.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import uuid

SALT = b'boiler-install-session-fixture-v1'
GUID = re.compile(r'\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?')
NONCE = re.compile(r'(?<![0-9a-fA-F])[0-9a-f]{32}(?![0-9a-fA-F])')
MARKERS = ('PARTITION_PROGRESS', 'PARTITION_PHASE', 'HOME_COPY_PROFILE',
           'PHYSICAL_VOLUME_PREFLIGHT', 'PHYSICAL_VOLUME_DISCOVERY',
           'PHYSICAL_VOLUME_LOCK', 'PHYSICAL_RESULT', 'PHYSICAL_RECOVERY_PREFIX_VERIFIED')


def digest(text: str) -> bytes:
    return hashlib.sha256(SALT + b'\0' + text.encode('utf-8')).digest()


def fake_guid(text: str) -> str:
    # Braces, case and EFI's little-endian binary encoding identify the same GUID.
    raw = bytearray(digest(str(uuid.UUID(text)))[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    value = '%08x-%04x-%04x-%04x-%012x' % (
        int.from_bytes(raw[0:4], 'big'), int.from_bytes(raw[4:6], 'big'),
        int.from_bytes(raw[6:8], 'big'), int.from_bytes(raw[8:10], 'big'),
        int.from_bytes(raw[10:16], 'big'))
    return '{' + value + '}' if text.startswith('{') else value


def fake_serial(text: str) -> str:
    if not text:
        return text
    body = ('SANITIZED_' + digest(text).hex()).upper()
    return body[:len(text)].ljust(len(text), '_')


def sanitize_text(text: str, serials: set[str]) -> str:
    for serial in sorted(serials, key=len, reverse=True):
        if serial:
            text = text.replace(serial, fake_serial(serial))
    text = re.sub(r'C:\\Users\\[^\\/\s"\']+', r'C:\\Users\\fixture', text, flags=re.I)
    text = re.sub(r'C:\\boiler-installer[^\\/\s"\']*', r'C:\\fixture\\installer', text, flags=re.I)
    text = re.sub(r'D:\\boiler-installer[^\\/\s"\']*', r'D:\\fixture\\installer', text, flags=re.I)
    text = re.sub(r'E:\\Boiler-workspace[^\\/\s"\']*', r'E:\\fixture\\work', text, flags=re.I)
    text = re.sub(r'E:\\Boiler-installer[^\\/\s"\']*', r'E:\\fixture\\installer', text, flags=re.I)
    text = GUID.sub(lambda m: fake_guid(m.group(0)), text)
    text = NONCE.sub(lambda m: digest(m.group(0)).hex()[:32], text)
    return text


def sanitize_efi_entry(value: str, serials: set[str]) -> str:
    """Sanitize the retained GPT/file-path EFI_LOAD_OPTION, not opaque Base64.

    Other device paths or optional binary data need an explicit privacy review;
    never silently pass them through as if string GUID replacement covered them.
    """
    data = base64.b64decode(value, validate=True)
    if len(data) < 8:
        raise ValueError('Truncated firmware fixture entry')
    path_size = struct.unpack_from('<H', data, 4)[0]
    end = 6
    while end + 1 < len(data) and data[end:end + 2] != b'\0\0':
        end += 2
    if end + 2 + path_size != len(data) or not path_size:
        raise ValueError('Invalid firmware fixture size or unreviewed optional data')
    description = sanitize_text(data[6:end].decode('utf-16-le'), serials)
    pos = end + 2
    nodes = bytearray()
    drives = 0
    terminated = False
    while pos + 4 <= len(data):
        kind, subtype, size = struct.unpack_from('<BBH', data, pos)
        if size < 4 or pos + size > len(data):
            raise ValueError('Malformed firmware fixture device path')
        node = data[pos:pos + size]
        if (kind, subtype) == (4, 1) and size == 42 and node[40:42] == b'\x02\x02':
            identity = uuid.UUID(bytes_le=node[24:40])
            replacement = uuid.UUID(fake_guid(str(identity))).bytes_le
            node = node[:24] + replacement + node[40:]
            drives += 1
        elif (kind, subtype) == (4, 4) and size >= 6 and size % 2 == 0 and node[-2:] == b'\0\0':
            path = sanitize_text(node[4:-2].decode('utf-16-le'), serials).encode('utf-16-le') + b'\0\0'
            node = struct.pack('<BBH', kind, subtype, 4 + len(path)) + path
        elif (kind, subtype) == (0x7f, 0xff) and size == 4 and pos + size == len(data):
            terminated = True
        else:
            raise ValueError('Unreviewed firmware fixture device path')
        nodes.extend(node)
        pos += size
    if not terminated or drives != 1 or pos != len(data):
        raise ValueError('Incomplete firmware fixture device path')
    result = data[:4] + struct.pack('<H', len(nodes))
    result += description.encode('utf-16-le') + b'\0\0' + nodes
    return base64.b64encode(result).decode('ascii')


TYPE_KEYS = {'Type', 'type', 'gptType', 'GptType', 'gpt_type'}


def sanitize_obj(value, serials: set[str], key: str | None = None):
    if isinstance(value, str):
        if key in TYPE_KEYS:
            # GPT type GUIDs are public constants used for role recognition.
            return value
        if key == 'entry':
            return sanitize_efi_entry(value, serials)
        if key == 'Snapshot':
            try:
                return json.dumps(sanitize_obj(json.loads(value), serials), ensure_ascii=False,
                                  separators=(',', ':'))
            except json.JSONDecodeError:
                return sanitize_text(value, serials)
        return sanitize_text(value, serials)
    if isinstance(value, list):
        return [sanitize_obj(item, serials, key) for item in value]
    if isinstance(value, dict):
        return {item_key: sanitize_obj(item, serials, item_key) for item_key, item in value.items()}
    return value


def collect_serials(value, found: set[str]):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() == 'serial' and isinstance(item, str) and item:
                found.add(item)
            else:
                collect_serials(item, found)
    elif isinstance(value, list):
        for item in value:
            collect_serials(item, found)


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def decode_helper(path: Path) -> str:
    chunks = []
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            chunks.append(json.loads(line))
        except json.JSONDecodeError:
            chunks.append(line)
    return ''.join(str(chunk) for chunk in chunks)


def transcript_lines(text: str) -> list[str]:
    text = text.replace('\r', '\n')
    for marker in MARKERS:
        text = re.sub(r'(?=' + re.escape(marker) + r'=)', '\n', text)
    return [line.strip() for line in text.splitlines()
            if line.startswith(tuple(marker + '=' for marker in MARKERS))]


def filter_progress(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    kept = []
    first = {}
    last = {}
    for row in rows:
        step = row.get('Step')
        first.setdefault(step, row)
        last[step] = row
    previous_step = None
    for index, row in enumerate(rows):
        step = row.get('Step')
        keep = index == 0 or step != previous_step or row is first.get(step) or row is last.get(step)
        keep = keep or index % 50 == 0
        if keep:
            kept.append(row)
        previous_step = step
    # Preserve order and remove accidental duplicates without reordering.
    seen = set()
    result = []
    for row in kept:
        key = json.dumps(row, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    source = Path(argv[1]).resolve()
    output = Path(argv[2]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    session = read_json(source / 'session/session.json')
    physical = read_json(source / 'deployment/physical-state.json')
    plan = read_json(source / 'session/partition-plan.json')
    serials: set[str] = set()
    collect_serials(session, serials)
    collect_serials(physical, serials)
    collect_serials(plan, serials)
    serials.discard('')
    # The selected target snapshot contains the authoritative disk identity.
    selection = physical['plan']['Selection']
    snapshot = json.loads(selection['Snapshot'])
    collect_serials(snapshot, serials)

    def write_json(name: str, value):
        (output / name).write_text(json.dumps(sanitize_obj(value, serials), ensure_ascii=False, indent=1) + '\n',
                                   encoding='utf-8')

    write_json('session.json', session)
    write_json('physical-state.json', physical)
    write_json('partition-plan.json', plan)

    helper = source / 'deployment/helper.jsonl'
    lines = transcript_lines(decode_helper(helper)) if helper.exists() else []
    (output / 'full-transcript.log').write_text('\n'.join(sanitize_text(line, serials) for line in lines) + '\n',
                                                encoding='utf-8')

    progress_path = source / 'session/progress.jsonl'
    rows = [json.loads(line) for line in progress_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    filtered = filter_progress(rows)
    (output / 'progress.jsonl').write_text(
        ''.join(json.dumps(sanitize_obj(row, serials), ensure_ascii=False) + '\n' for row in filtered),
        encoding='utf-8')

    for name in ('components.json', 'install-plan-A.json', 'install-plan-B.json',
                 'firmware-plan.json'):
        path = source / 'session' / name
        if path.exists():
            write_json(name, read_json(path))
    for name in ('firmware-before.json', 'firmware-result.json'):
        path = source / 'deployment' / name
        if path.exists():
            write_json(name, read_json(path))

    home_used = None
    for line in lines:
        match = re.match(r'HOME_COPY_PROFILE=([^:]+):(\d+):(\d+)', line)
        if match:
            home_used = int(match.group(2))
            break
    added = physical['plan']['Added']
    roles = [dict(number=item['Number'], name=item['Name'], offset=item['Offset'],
                  size=item['Size'], type=item['Type'], id=item['Id'], attributes=item.get('Attributes', 0))
             for item in sorted(added, key=lambda item: item['Offset'])]
    preserved = [dict(number=item['Number'], offset=item['Offset'], size=item['Size'],
                      id=item['Id'], type=item.get('Type')) for item in sorted(physical['plan']['Preserved'],
                                                                              key=lambda item: item['Offset'])]
    disk = dict(number=physical['device']['Number'], size=physical['device']['Size'],
                sector_size=physical['device']['SectorSize'], bus=physical['device']['Bus'],
                serial=physical['device'].get('Serial', ''),
                model=physical['device'].get('Product', ''), vendor=physical['device'].get('Vendor', ''))
    domestic = [item for item in snapshot if item.get('number') == disk['number']]
    if domestic:
        disk['model'] = domestic[0].get('model', disk['model'])
        disk['guid'] = domestic[0].get('guid', '')
    layout = dict(schema=1, kind='internal' if physical['plan']['Kind'] == 0 else 'external',
                  disk=disk, selection=dict(start=selection['Start'], size=selection['Capacity'],
                                            align_right=bool(selection.get('AlignRight'))),
                  preserved=preserved, roles=roles,
                  home_used_bytes=home_used,
                  home_partition_size=next(item['size'] for item in roles if item['name'] == 'home'))
    write_json('partition-layout.json', layout)

    ui = source / 'session/ui.log'
    verify_lines = []
    if ui.exists():
        seen = set()
        for line in ui.read_text(encoding='utf-8', errors='replace').splitlines():
            match = re.match(r'OXP_INTEGRATION_VERIFIED=([AB])(?::.*)?$', line.strip())
            if match and match.group(1) not in seen:
                seen.add(match.group(1))
                verify_lines.append('OXP_INTEGRATION_VERIFY=' +
                                    json.dumps(dict(slot=match.group(1), readonly=True), separators=(',', ':')))
    (output / 'verify-markers.log').write_text('\n'.join(verify_lines) + ('\n' if verify_lines else ''),
                                               encoding='utf-8')

    (output / 'disk-snapshots.json').write_text(
        json.dumps(sanitize_obj(snapshot, serials), ensure_ascii=False, indent=1) + '\n', encoding='utf-8')

    max_gap = 0
    stage_rows = [row for row in rows if row.get('Step', '').startswith('02.01')]
    for left, right in zip(stage_rows, stage_rows[1:]):
        max_gap = max(max_gap, right.get('elapsedMs', 0) - left.get('elapsedMs', 0))
    files = {}
    for path in sorted(output.iterdir()):
        if path.is_file() and path.name != 'manifest.json':
            files[path.name] = dict(size=path.stat().st_size,
                                    sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    manifest = dict(schema=1, kind='BoilerInstallSessionFixture',
                    source_export=source.name,
                    exported_utc=json.loads((source / 'export-info.json').read_text(encoding='utf-8'))['exportedUtc'],
                    installer_version=json.loads((source / 'export-info.json').read_text(encoding='utf-8'))['version'],
                    sanitization=dict(version=2, salt=SALT.decode(),
                                      rules=['canonical case/braces guid mapping', 'shape-preserving serial',
                                             'user/profile and installer paths', '32-hex nonce',
                                             'binary GPT firmware identity', 'single-pass identity projections']),
                    sampling=dict(progress='step transitions + first/last per step + every 50th record',
                                  transcript='all partition/physical marker lines'),
                    expectations=dict(kind=layout['kind'], preserved_count=len(preserved),
                                      role_numbers=[item['number'] for item in roles],
                                      role_names=[item['name'] for item in roles],
                                      home_used_bytes=home_used,
                                      historical_max_component_gap_ms=max_gap),
                    files=files)
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n',
                                          encoding='utf-8')
    print(json.dumps(dict(output=str(output), kind=layout['kind'], roles=len(roles),
                          preserved=len(preserved), home_used=home_used,
                          progress_records=len(filtered), transcript_lines=len(lines)),
                     ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
