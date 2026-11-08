# SPDX-License-Identifier: AGPL-3.0-or-later
"""Derive the internal legacy runtime from an authenticated maintenance app.

The caller must obtain expected_manifest_sha256 from the already validated model
package inventory. The input and previous installed generations stay untouched.
"""
import hashlib
import json

CLI = 'oxp3_maintenance/cli.py'
OPERATIONS = 'oxp3_maintenance/operations.py'
REVIEWED = {
    CLI: 'dc3bbce4936a26ce415431d6e5bae747245edbf7a9815b830211781c36a14b26',
    OPERATIONS: '3aabfa824011ba723bf479d83b56ae53c17662c5a1b07dbc6733af748e9ce6ca',
}
REMOVED = frozenset(('OXP3-Maintenance.desktop', 'oxp3_maintenance/gui.py',
                     'oxp3_maintenance/touch_ui.py', 'oxp3_maintenance/desktop_service.py'))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Reviewed legacy runtime transformation no longer matches')
    return text.replace(old, new)


def derive(manifest_raw, app, expected_manifest_sha256):
    """Return derived member bytes and generated manifest bytes; never mutate input."""
    if sha(manifest_raw) != expected_manifest_sha256:
        raise ValueError('Maintenance manifest differs from the validated package')
    manifest = json.loads(manifest_raw)
    if manifest.get('schema') != 1 or not isinstance(manifest.get('files'), dict):
        raise ValueError('Invalid maintenance application manifest')
    if {name: sha(data) for name, data in app.items()} != manifest['files']:
        raise ValueError('Maintenance application differs from its authenticated inventory')
    if not REMOVED <= app.keys() or any(sha(app[name]) != digest for name, digest in REVIEWED.items()):
        raise ValueError('Maintenance runtime differs from reviewed transform input')
    result = {name: data for name, data in app.items() if name not in REMOVED}
    cli = result[CLI].decode('utf-8')
    cli = replace_once(cli, "    if args.action == 'gui':\n        from .touch_ui import run\n        return run(args)\n",
                       "    if args.action == 'gui':\n        raise RuntimeError('The legacy maintenance desktop tool has been retired')\n")
    result[CLI] = cli.encode('utf-8')
    operations = result[OPERATIONS].decode('utf-8')
    operations = replace_once(operations,
        "    if 'entry.py' not in content or 'OXP3-Maintenance.desktop' not in content:\n        raise RuntimeError('Maintenance runtime is missing its entry or desktop launcher')\n",
        "    if 'entry.py' not in content:\n        raise RuntimeError('Maintenance runtime is missing its entry')\n")
    start = "    desktop = content['OXP3-Maintenance.desktop']\n"
    end = "    files['etc/systemd/system/oxp3-maintenance-activate.service'] = {\n"
    if operations.count(start) != 1 or operations.count(end) != 1:
        raise ValueError('Reviewed launcher block no longer matches')
    before, tail = operations.split(start, 1)
    _, after = tail.split(end, 1)
    operations = before + end + after
    result[OPERATIONS] = operations.encode('utf-8')
    derived_manifest = dict(manifest, files={name: sha(data) for name, data in sorted(result.items())})
    return result, (json.dumps(derived_manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def derive_package(validated_manifest, read_member):
    """Return changed bytes and removed paths for a validated device package.

    The caller validates the original package and confines read_member. The
    function rechecks every affected member against that validated inventory.
    It performs no writes and does not update package or provider manifests.
    """
    if validated_manifest.get('id') not in ('device:oxp3', 'device:x2-mini'):
        raise ValueError('Unsupported model package')
    rows = validated_manifest['files']
    inventory = {row['path']: row for row in rows}
    if len(inventory) != len(rows):
        raise ValueError('Duplicate model package inventory member')
    prefix = 'payload/legacy/maintenance/app/'
    manifest_path = 'payload/legacy/maintenance/manifest.json'
    wrapper_path = 'payload/legacy/components/maintenance_tool.py'
    integration_path = 'payload/legacy/components/integration.py'
    def read_checked(name):
        row = inventory[name]
        raw = read_member(name)
        if len(raw) != row['size'] or sha(raw) != row['sha256']:
            raise ValueError('Package member differs from validated inventory: ' + name)
        return raw
    maintenance_manifest = read_checked(manifest_path)
    app = {name[len(prefix):]: read_checked(name) for name in inventory if name.startswith(prefix)}
    derived, generated = derive(maintenance_manifest, app, inventory[manifest_path]['sha256'])
    wrapper = read_checked(wrapper_path)
    if sha(wrapper) != 'e624ec3bb39dcb60d87b601e63da1779e543114c76efffce7bc113153b47f232':
        raise ValueError('Legacy maintenance wrapper differs from reviewed input')
    from pathlib import Path
    replacement = (Path(__file__).resolve().parents[1]/'brands/onexplayer/oxp3/bridge/embedded-maintenance-tool.py').read_bytes()
    integration = read_checked(integration_path).decode('utf-8')
    integration = replace_once(integration,
        "reports['maintenance-tool']={'status':'files_installed','activation':'desktop-launch',",
        "reports['maintenance-tool']={'status':'files_installed','activation':'boot-service',")
    changed = {manifest_path: generated, wrapper_path: replacement,
               integration_path: integration.encode('utf-8')}
    changed.update({prefix + name: raw for name, raw in derived.items() if app[name] != raw})
    return changed, {prefix + name for name in app.keys() - derived.keys()}
