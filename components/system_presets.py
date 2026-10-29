"""Offline SteamOS locale and account presets, owned by one file transaction.

No command runs in the target image. Password material remains in private guest
memory, private temporary files and the maintenance engine's private blob store.
"""
import os
import contextlib
from pathlib import Path
import re
import stat
import struct
import time

IDENTITY = 'core:system-presets'
LANGUAGES = {'system', 'zh_CN', 'zh_TW', 'en_US', 'ja_JP', 'ko_KR', 'de_DE', 'fr_FR', 'es_ES', 'ru_RU'}
PASSWORD_HASH = re.compile(r'\$6\$(?:rounds=\d+\$)?[A-Za-z0-9./]{1,16}\$[A-Za-z0-9./]{86}\Z')
SSH_TARGET = 'etc/systemd/system/multi-user.target.d/50-boiler-ssh.conf'
SSH_DATA = b'# Boiler offline SSH preset\n[Unit]\nWants=sshd.service\n'
LOCALE_CATEGORIES = ('LC_CTYPE', 'LC_NUMERIC', 'LC_TIME', 'LC_COLLATE', 'LC_MONETARY', 'LC_MESSAGES/SYS_LC_MESSAGES', 'LC_PAPER', 'LC_NAME', 'LC_ADDRESS', 'LC_TELEPHONE', 'LC_MEASUREMENT', 'LC_IDENTIFICATION')


def locale_magic(category):
    # glibc locale/localeinfo.h LIMAGIC, for the x86_64 target.
    return struct.pack('<I', (0x20090720 if category == 0 else 0x20051014 if category == 3 else 0x20031115) ^ category)


def selected(options):
    return options.get('kde_language', 'system') != 'system' or options.get('set_deck_password', False) or options.get('enable_ssh', False)


def validate(options):
    for key in ('set_deck_password', 'enable_ssh'):
        if type(options.get(key, False)) is not bool:
            raise ValueError('Invalid system preset flag: ' + key)
    language = options.get('kde_language', 'system')
    if not isinstance(language, str) or language not in LANGUAGES:
        raise ValueError('Unknown system preset language')
    if options.get('install_mode', 'custom') in ('imported', 'local') and selected(options):
        raise ValueError('Imported images cannot receive system presets')


def _path(root, relative):
    from protocol.manifest import confined
    return confined(root, relative)


def _read(root, relative, *, required=False, private=False):
    path = _path(root, relative)
    from maintenance.adoption_blobs import _file
    user = {}
    if relative.startswith('home/deck/'):
        rows = [line.split(':') for line in _read(root, 'etc/passwd', required=True).decode().splitlines() if line.startswith('deck:')]
        if len(rows) != 1 or len(rows[0]) != 7 or not rows[0][2].isdigit():
            raise ValueError('Expected one deck account')
        user = dict(user_from=Path(root) / 'home/deck', user_uid=int(rows[0][2]))
    try:
        with _file(path, private=private, **user) as (stream, info):
            if info.st_size > 1024 * 1024: raise ValueError('System preset file exceeds limit')
            data = stream.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024: raise ValueError('System preset file exceeds limit')
            return data
    except FileNotFoundError:
        if required: raise ValueError('Required system preset file missing: ' + relative) from None
        return None


def _config(text, group, values):
    _check_config(text)
    lines = text.splitlines()
    indices = [i for i, line in enumerate(lines) if line.strip() == '[' + group + ']']
    if len(indices) > 1: raise ValueError('Ambiguous desktop language group')
    if not indices: lines += ['', '[' + group + ']']; start = len(lines) - 1
    else: start = indices[0]
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith('[')), len(lines))
    body = lines[start + 1:end]
    for key, value in values.items():
        matches = [i for i, line in enumerate(body) if line.split('=', 1)[0].strip() == key]
        if len(matches) > 1: raise ValueError('Ambiguous desktop language key')
        if matches: body[matches[0]] = key + '=' + value
        else: body.append(key + '=' + value)
    return '\n'.join(lines[:start + 1] + body + lines[end:]) + '\n'


def _check_config(text, *, expected=None):
    """Fail closed for immutable/localized/duplicate locale overrides."""
    group = None; groups = set(); keys = set()
    for line in text.splitlines():
        clean = line.strip()
        if not clean or clean.startswith(('#', ';')): continue
        if clean.startswith('['):
            match = re.fullmatch(r'\[([^\]]+)\](.*)', clean)
            if not match: raise ValueError('Ambiguous KDE language configuration')
            group, flags = match.groups()
            if group in ('Translations', 'Formats'):
                if flags or group in groups: raise ValueError('Immutable or ambiguous KDE language group')
                groups.add(group)
            continue
        if '=' not in clean or group not in ('Translations', 'Formats'): continue
        key, value = clean.split('=', 1); key = key.strip()
        base = key.split('[', 1)[0]
        if base not in ('LANGUAGE', 'LANG', 'LC_MESSAGES', 'LC_ALL'): continue
        if key != base or (group, base) in keys or base == 'LC_ALL':
            raise ValueError('Immutable or ambiguous KDE language key')
        keys.add((group, base))
        wanted = expected if base == 'LANGUAGE' else expected + '.UTF-8' if expected else None
        if expected and value.strip() != wanted:
            raise ValueError('Existing deck desktop language conflicts with the selected preset; change it in KDE first')


def _compiled_locale(stream, offset, length, category):
    if length < 12: raise ValueError('Truncated compiled locale header')
    stream.seek(offset); header = stream.read(8)
    if len(header) != 8 or header[:4] != locale_magic(category): raise ValueError('Invalid compiled locale category')
    count = struct.unpack_from('<I', header, 4)[0]
    if not 0 < count <= 100000 or 8 + count * 4 > length: raise ValueError('Invalid compiled locale index')
    indices = stream.read(count * 4)
    if len(indices) != count * 4 or any(not 8 + count * 4 <= value < length for value in struct.unpack('<' + 'I' * count, indices)):
        raise ValueError('Compiled locale index escapes category')


def _translation(stream, length):
    if length < 28: raise ValueError('Truncated KDE translation header')
    stream.seek(0); header = stream.read(28)
    if header[:4] not in (b'\xde\x12\x04\x95', b'\x95\x04\x12\xde'): raise ValueError('Invalid KDE translation magic')
    endian = '<' if header[:4] == b'\xde\x12\x04\x95' else '>'
    _, revision, count, originals, translations, hashes, hash_offset = struct.unpack(endian + '7I', header)
    if revision >> 16 > 1 or not 0 < count <= 100000 or hashes > 1000000:
        raise ValueError('Invalid KDE translation catalogue')
    if hashes and (hash_offset < 28 or hash_offset + hashes * 4 > length): raise ValueError('KDE translation hash table escapes file')
    for table in (originals, translations):
        if table < 28 or table + count * 8 > length: raise ValueError('KDE translation table escapes file')
        stream.seek(table); entries = stream.read(count * 8)
        if len(entries) != count * 8: raise ValueError('Truncated KDE translation table')
        for size, position in struct.iter_unpack(endian + 'II', entries):
            if position < 28 or position + size >= length: raise ValueError('KDE translation string escapes file')
            stream.seek(position + size)
            if stream.read(1) != b'\0': raise ValueError('KDE translation string is unterminated')


@contextlib.contextmanager
def _artifact_file(path, *, maximum):
    """Read immutable image artifacts, including glibc's legitimate hardlinks.

    Unlike transaction inputs such as shadow, read-only system artifacts do not
    require link count one. Every ancestor and the opened inode stay pinned and
    protected; metadata must remain stable until the caller finishes parsing.
    """
    from maintenance.adoption_blobs import _directory, _signature
    path = Path(path).absolute()
    with _directory(path.parent) as directory:
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
        descriptor = os.open(path if os.name == 'nt' else path.name, flags,
                             **({} if os.name == 'nt' else {'dir_fd': directory}))
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink < 1 or not 0 < info.st_size <= maximum:
                raise ValueError('Invalid or oversized read-only system artifact')
            if os.name != 'nt' and (info.st_uid != 0 or info.st_mode & 0o022):
                raise ValueError('Read-only system artifact ownership is untrusted')
            yield stream, info
            if _signature(os.fstat(stream.fileno())) != _signature(info):
                raise ValueError('Read-only system artifact changed during inspection')


def _artifact(root, relative, *, executable=False, locale_category=None, translation=False):
    from source_profile import image_path
    path = image_path(root, relative)
    try:
        with _artifact_file(path, maximum=128 * 1024 * 1024) as (stream, info):
            if not 0 < info.st_size <= 128 * 1024 * 1024 or executable and os.name != 'nt' and not info.st_mode & 0o111:
                raise ValueError('Invalid target locale or executable artifact: ' + relative)
            if locale_category is not None: _compiled_locale(stream, 0, info.st_size, locale_category)
            if translation: _translation(stream, info.st_size)
            stream.seek(0)
            return stream.read(4)
    except FileNotFoundError:
        return None


def _archive_locale(root, language):
    # Fixed glibc locarhead/namehashent/locrecent layouts; no image programs run.
    # https://raw.githubusercontent.com/bminor/glibc/master/locale/locarchive.h
    from source_profile import image_path
    path = image_path(root, 'usr/lib/locale/locale-archive')
    try:
        with _artifact_file(path, maximum=2 * 1024 * 1024 * 1024) as (stream, info):
            header = stream.read(56)
            if len(header) != 56: raise ValueError('Truncated locale archive header')
            values = struct.unpack('<14I', header)
            magic, _, no, nu, ns, so, su, ss, ro, ru, rs, _, _, _ = values
            if magic != 0xde020109 or not 2 < ns <= 100000 or not 0 <= nu <= ns or not 0 < su <= ss <= 8 * 1024 * 1024 or not 0 < ru <= rs <= 100000:
                raise ValueError('Invalid locale archive tables')
            for offset, length in ((no, ns * 12), (so, ss), (ro, rs * 108)):
                if offset < 56 or offset + length > info.st_size: raise ValueError('Locale archive table escapes file')
            spans = sorted(((no, no + ns * 12), (so, so + ss), (ro, ro + rs * 108)))
            if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
                raise ValueError('Locale archive tables overlap')
            stream.seek(no); names = stream.read(ns * 12)
            stream.seek(so); strings = stream.read(su)
            wanted = {language + '.utf8', language + '.UTF-8'}
            for index in range(ns):
                _, name, record = struct.unpack_from('<III', names, index * 12)
                if not name or not record: continue
                if not so <= name < so + su: raise ValueError('Locale archive name escapes string table')
                end = strings.find(b'\0', name - so, min(su, name - so + 257))
                if end < 0: raise ValueError('Unterminated locale archive name')
                if strings[name - so:end].decode('ascii') not in wanted: continue
                if record < ro or (record - ro) % 108 or record + 108 > ro + ru * 108:
                    raise ValueError('Locale archive record escapes active records')
                stream.seek(record); data = stream.read(108)
                if len(data) != 108 or struct.unpack_from('<I', data)[0] == 0: raise ValueError('Deleted locale archive record')
                for category in range(13):
                    if category == 6: continue  # LC_ALL is not a stored category.
                    offset, length = struct.unpack_from('<II', data, 4 + category * 8)
                    if not length or offset < 56 or offset + length > info.st_size:
                        raise ValueError('Generated locale category escapes archive')
                    _compiled_locale(stream, offset, length, category)
                return True
            return False
    except FileNotFoundError:
        return False


def require_language(root, language):
    generated = any(all(_artifact(root, 'usr/lib/locale/' + name + '/' + category, locale_category=index if index < 6 else index + 1) is not None
                        for index, category in enumerate(LOCALE_CATEGORIES))
                    for name in (language + '.utf8', language + '.UTF-8'))
    if not generated and not _archive_locale(root, language):
        raise ValueError('Selected language has no generated libc locale in this image')
    if _artifact(root, 'usr/bin/plasmashell', executable=True) is None:
        raise ValueError('Selected desktop language requires KDE Plasma in the image')
    if language != 'en_US':
        folder = language if language in ('zh_CN', 'zh_TW') else language.split('_')[0]
        magic = _artifact(root, 'usr/share/locale/' + folder + '/LC_MESSAGES/plasmashell.mo', translation=True)
        if magic not in (b'\xde\x12\x04\x95', b'\x95\x04\x12\xde'):
            raise ValueError('Selected KDE translation is not installed in this image')


def prepare(root, options, password_hash=None):
    """Complete target checks and generate bytes without changing the image."""
    validate(options)
    root = Path(root)
    if not selected(options): return None, {}
    from source_profile import release_values
    release_values(root)
    rows = [_line.split(':') for _line in _read(root, 'etc/passwd', required=True).decode().splitlines() if _line.startswith('deck:')]
    if len(rows) != 1 or len(rows[0]) != 7 or not rows[0][2].isdigit() or not rows[0][3].isdigit() or min(int(rows[0][2]), int(rows[0][3])) <= 0 or rows[0][5] != '/home/deck':
        raise ValueError('System presets require one unprivileged deck account with /home/deck')
    files = {}
    language = options.get('kde_language', 'system')
    if language != 'system':
        require_language(root, language)
        locale = language + '.UTF-8'
        # Home is shared between A/B slots. Keep it outside per-slot ownership.
        # Agreeing user preferences remain unchanged; conflicting overrides need
        # an explicit user migration instead of two independent restore chains.
        user = _read(root, 'home/deck/.config/plasma-localerc')
        if user is not None:
            _check_config(user.decode(), expected=language)
        system = (_read(root, 'etc/locale.conf') or b'').decode().splitlines()
        if any(line.split('=', 1)[0].strip() in ('LC_ALL', 'LANGUAGE') for line in system):
            raise ValueError('Conflicting global locale override requires migration')
        for key in ('LANG', 'LC_MESSAGES'):
            matches = [i for i, line in enumerate(system) if line.split('=', 1)[0].strip() == key]
            if len(matches) > 1: raise ValueError('Ambiguous system locale key')
            if matches: system[matches[0]] = key + '=' + locale
            else: system.append(key + '=' + locale)
        files['etc/locale.conf'] = ('\n'.join(system) + '\n').encode()
        desktop = (_read(root, 'etc/xdg/plasma-localerc') or b'').decode()
        desktop = _config(desktop, 'Translations', {'LANGUAGE': language})
        files['etc/xdg/plasma-localerc'] = _config(desktop, 'Formats', {'LANG': locale, 'LC_MESSAGES': locale}).encode()
    if options.get('set_deck_password', False):
        if not isinstance(password_hash, str) or not PASSWORD_HASH.fullmatch(password_hash):
            raise ValueError('A SHA-512 crypt hash is required for the selected deck password')
        original = _read(root, 'etc/shadow', required=True, private=True).decode()
        lines = original.splitlines()
        matches = [i for i, line in enumerate(lines) if line.startswith('deck:')]
        if len(matches) != 1 or len(lines[matches[0]].split(':')) != 9:
            raise ValueError('Expected one valid deck shadow entry')
        fields = lines[matches[0]].split(':'); fields[1] = password_hash
        today = int(time.time() // 86400)
        for index in (3, 4, 6, 7):
            if fields[index] and not fields[index].isdigit(): raise ValueError('Invalid deck password ageing policy')
        if fields[7] and int(fields[7]) <= today: raise ValueError('Deck account is expired; password preset cannot enable login')
        if fields[4] and int(fields[4]) == 0: raise ValueError('Deck password policy expires the initial password immediately')
        if fields[3] and fields[4] and int(fields[3]) > int(fields[4]): raise ValueError('Invalid deck password ageing range')
        fields[2] = str(today)
        lines[matches[0]] = ':'.join(fields)
        files['etc/shadow'] = ('\n'.join(lines) + ('\n' if original.endswith('\n') else '')).encode()
    elif password_hash is not None:
        raise ValueError('Unexpected password hash while password setup is disabled')
    if options.get('enable_ssh', False):
        shell = rows[0][6]
        if not shell.startswith('/') or Path(shell).name in ('nologin', 'false') or _artifact(root, shell.lstrip('/'), executable=True) is None:
            raise ValueError('Deck SSH requires an available executable login shell')
        # systemd loads a target drop-in as a dependency; no symlink transaction
        # or target-side systemctl invocation is needed for offline enablement.
        override = _read(root, 'etc/systemd/system/sshd.service')
        unit = override if override is not None else _read(root, 'usr/lib/systemd/system/sshd.service', required=True)
        if b'[Service]' not in unit: raise ValueError('Invalid target sshd.service unit')
        _read(root, SSH_TARGET)
        files[SSH_TARGET] = SSH_DATA
    from payload_lock import record
    records = [record('preset/' + target, data, target=target, mode=0o600 if target == 'etc/shadow' else 0o644) for target, data in sorted(files.items())]
    return dict(schema_version=1, kind='ComponentManifest', id=IDENTITY, version='0.1.0', files=records, requires=[], conflicts=[], hooks={}), files


def apply(engine, component, files):
    """Publish one transaction; the private staging tree is never an output."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix='boiler-presets-') as temporary:
        staged = Path(temporary)
        for target, data in files.items():
            path = staged / 'preset' / target
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with path.open('xb') as stream:
                if os.name != 'nt': os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
        return engine.apply(component, staged)


def verify(root, options, engine):
    """Independently check effective preset semantics and private ownership."""
    validate(options)
    if not selected(options): return
    if not engine.verify_readonly(IDENTITY)['ok']: raise ValueError('System preset managed files differ')
    language = options.get('kde_language', 'system')
    if language != 'system':
        require_language(root, language)
        locale = language + '.UTF-8'
        system = _read(root, 'etc/locale.conf', required=True).decode().splitlines()
        for key in ('LANG', 'LC_MESSAGES'):
            if [line for line in system if line.split('=', 1)[0].strip() == key] != [key + '=' + locale]:
                raise ValueError('System locale differs from selected language')
        desktop = _read(root, 'etc/xdg/plasma-localerc', required=True).decode()
        if _config(_config(desktop, 'Translations', {'LANGUAGE': language}), 'Formats', {'LANG': locale, 'LC_MESSAGES': locale}) != desktop:
            raise ValueError('Desktop locale differs from selected language')
        user = _read(root, 'home/deck/.config/plasma-localerc')
        if user is not None: _check_config(user.decode(), expected=language)
    if options.get('set_deck_password', False):
        lines = _read(root, 'etc/shadow', required=True, private=True).decode().splitlines()
        rows = [line.split(':') for line in lines if line.startswith('deck:')]
        if len(rows) != 1 or len(rows[0]) != 9 or not PASSWORD_HASH.fullmatch(rows[0][1]):
            raise ValueError('Deck password preset is not a valid private crypt entry')
    if options.get('enable_ssh', False):
        if _read(root, SSH_TARGET, required=True) != SSH_DATA: raise ValueError('SSH target dependency differs')
        override = _read(root, 'etc/systemd/system/sshd.service')
        unit = override if override is not None else _read(root, 'usr/lib/systemd/system/sshd.service', required=True)
        if b'[Service]' not in unit: raise ValueError('Target SSH service is missing')
