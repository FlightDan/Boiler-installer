"""Validate the fixed public bridge against the installable local snapshot."""
import hashlib
import json

PUBLIC_LOCK = 'common/fingerprint/public-bridge-lock.json'
BRIDGE = ('index.js', 'main.py', 'package.json', 'plugin.json')


def validate(root, source_lock, member):
    if source_lock.get('public_bridge_lock') != PUBLIC_LOCK:
        raise ValueError('Fingerprint public bridge lock missing')
    raw = member(root, PUBLIC_LOCK).read_bytes()
    if hashlib.sha256(raw).hexdigest() != source_lock.get('public_bridge_lock_sha256'):
        raise ValueError('Fingerprint public bridge lock changed')
    lock = json.loads(raw)
    if (lock.get('schema_version') != 1 or lock.get('kind') != 'BoilerFingerprintPublicBridgeSource'
            or lock.get('repository') != 'FlightDan/FingerprintUnlock-SteamOS'
            or lock.get('commit') != 'fde8fa62bbad0318a9a8be88cc77bb4c3b4f919e'):
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
