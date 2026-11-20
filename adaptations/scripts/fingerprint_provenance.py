"""Validate the fixed public bridge against the installable local snapshot."""
import hashlib
import json

PUBLIC_LOCK = 'common/fingerprint/public-bridge-lock.json'
BRIDGE = ('index.js', 'main.py', 'package.json', 'plugin.json')
KEYBOARD_ROOT = 'common/fingerprint/kde/gamescope-auth/'
KEYBOARD_AGENT = 'polkit-kde-authentication-agent-1'
KEYBOARD_LICENSE = 'GPL-2.0-or-later.txt'
KEYBOARD_PROFILE = 'common/fingerprint/runtime-profile/profile.json'
KEYBOARD_TARGETS = {
    KEYBOARD_AGENT: ('var/lib/oxp3-installer/fingerprint/libexec/' + KEYBOARD_AGENT, 0o755),
    KEYBOARD_LICENSE: ('usr/share/licenses/boiler-fingerprint-keyboard/' + KEYBOARD_LICENSE, 0o644),
}
KEYBOARD_SOURCE_FILES = ('BUILD.md', 'gamescope-auth.patch', 'polkit-kde-agent-1-v6.7.3-source.tar.gz')


def keyboard_sources(read):
    """Return the reviewed agent, corresponding source and license after byte validation."""
    lock = json.loads(read(KEYBOARD_ROOT + 'source-lock.json'))
    expected = {*KEYBOARD_TARGETS, *KEYBOARD_SOURCE_FILES}
    rows = lock.get('files', [])
    if (lock.get('kind') != 'BoilerFingerprintKeyboardSource' or lock.get('schema_version') != 1
            or len(rows) != len(expected) or {row.get('path') for row in rows} != expected):
        raise ValueError('Unexpected fingerprint keyboard source inventory')
    profile = json.loads(read(KEYBOARD_PROFILE))
    if profile.get('game_mode_auth_agent') != '/' + KEYBOARD_TARGETS[KEYBOARD_AGENT][0]:
        raise ValueError('Fingerprint profile does not point to the packaged keyboard agent')
    verified = {}
    for row in rows:
        data = read(KEYBOARD_ROOT + row['path'])
        if (len(data), hashlib.sha256(data).hexdigest()) != (row['size'], row['sha256']):
            raise ValueError('Fingerprint keyboard source differs from lock: ' + row['path'])
        verified[row['path']] = data
    return verified



def validate(root, source_lock, member):
    if source_lock.get('public_bridge_lock') != PUBLIC_LOCK:
        raise ValueError('Fingerprint public bridge lock missing')
    raw = member(root, PUBLIC_LOCK).read_bytes()
    if hashlib.sha256(raw).hexdigest() != source_lock.get('public_bridge_lock_sha256'):
        raise ValueError('Fingerprint public bridge lock changed')
    lock = json.loads(raw)
    if (lock.get('schema_version') != 1 or lock.get('kind') != 'BoilerFingerprintPublicBridgeSource'
            or lock.get('repository') != 'FlightDan/FingerprintUnlock-SteamOS'
            or lock.get('commit') != '8be889082ebf4e8df6ad706857d2f59bd701d070'):
        raise ValueError('Unexpected fingerprint public bridge identity')
    expected = {('bridge/'+n, 'common/fingerprint/bridge/'+n) for n in BRIDGE}
    license_expected = {('COPYING', 'common/fingerprint/public-COPYING'),
                        ('upstream/LICENSE', 'common/fingerprint/upstream/LICENSE')}
    licenses = [{'spdx':'AGPL-3.0','reference':'COPYING','local_path':'common/fingerprint/public-COPYING'},
                {'spdx':'MIT','reference':'upstream/LICENSE','local_path':'common/fingerprint/upstream/LICENSE'}]
    if lock.get('licenses') != licenses:
        raise ValueError('Fingerprint public license references changed')
    for key, required in (('files', expected), ('license_files', license_expected)):
        rows = lock.get(key)
        if not isinstance(rows, list) or len(rows) != len(required) or {(r.get('path'), r.get('local_path')) for r in rows} != required:
            raise ValueError('Unexpected fingerprint public inventory')
        for row in rows:
            data = member(root, row['local_path']).read_bytes()
            blob = hashlib.sha1(b'blob '+str(len(data)).encode('ascii')+b'\0'+data).hexdigest()
            if len(data) != row['size'] or hashlib.sha256(data).hexdigest() != row['sha256'] or blob != row['git_blob_sha1']:
                raise ValueError('Fingerprint public source changed: '+row['path'])
    return lock
