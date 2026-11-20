#!/usr/bin/env python3
"""Refresh the pinned S2/S4 payload from an audited official GitHub release."""
import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request
import zipfile

REPO = 'FlightDan/SteamOS-S2-S4-switcher'
API = 'https://api.github.com/repos/' + REPO
PREFIX = 'Boiler-S2S4-Switcher/'
MEMBERS = ('main.py', 'dist/index.js', 'package.json', 'plugin.json',
           'sleep_control.py', 'install.py', 'README.md', 'LICENSE')
RUNTIME = ('main.py', 'dist/index.js', 'package.json', 'plugin.json',
           'sleep_control.py', '90-boiler-sleep.conf', 'LICENSE')
TARGETS = ('var/lib/oxp3-installer/decky/runtime/plugins/Boiler-S2S4-Switcher/main.py',
           'var/lib/oxp3-installer/decky/runtime/plugins/Boiler-S2S4-Switcher/dist/index.js',
           'var/lib/oxp3-installer/decky/runtime/plugins/Boiler-S2S4-Switcher/package.json',
           'var/lib/oxp3-installer/decky/runtime/plugins/Boiler-S2S4-Switcher/plugin.json',
           'var/lib/boiler-sleep/sleep_control.py',
           'etc/systemd/system/systemd-suspend.service.d/90-boiler-sleep.conf',
           'usr/share/licenses/boiler-s2s4/LICENSE')

def digest(data):
    return hashlib.sha256(data).hexdigest()

def fetch(url):
    headers = {'User-Agent': 'Boiler-adaptations-release-refresh', 'Accept': 'application/vnd.github+json'}
    request = urllib.request.Request(url, headers=headers)
    if url.startswith(API + '/') and os.environ.get('GITHUB_TOKEN'):
        request.add_unredirected_header('Authorization', 'Bearer ' + os.environ['GITHUB_TOKEN'])
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()

def api(path):
    return json.loads(fetch(API + path))

def git(binary, checkout, *args):
    return subprocess.check_output([str(binary), '-C', str(checkout), *args])

def version(tag):
    if not re.fullmatch(r'v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', tag):
        raise ValueError('Release tag must be a stable vX.Y.Z version')
    return tuple(map(int, tag[1:].split('.')))

def resolve_release():
    release = api('/releases/latest')
    tag = release['tag_name']
    version(tag)
    if release['draft'] or release['prerelease']:
        raise ValueError('Release is not stable')
    name = 'Boiler-S2S4-Switcher-' + tag[1:] + '.zip'
    assets = [a for a in release['assets'] if a['name'] == name]
    if len(assets) != 1:
        raise ValueError('Expected exactly one versioned release ZIP')
    asset = assets[0]
    if asset['browser_download_url'] != f'https://github.com/{REPO}/releases/download/{tag}/{name}':
        raise ValueError('Unexpected release asset URL')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', asset.get('digest') or '') or not isinstance(asset['size'], int) or asset['size'] <= 0:
        raise ValueError('Release asset lacks authoritative size or SHA-256')
    ref = api('/git/ref/tags/' + tag)['object']
    for _ in range(8):
        if ref['type'] == 'commit':
            break
        if ref['type'] != 'tag' or not re.fullmatch(r'[0-9a-f]{40}', ref['sha']):
            raise ValueError('Invalid GitHub tag object')
        ref = api('/git/tags/' + ref['sha'])['object']
    else:
        raise ValueError('Too many nested GitHub tag objects')
    if not re.fullmatch(r'[0-9a-f]{40}', ref['sha']):
        raise ValueError('Invalid GitHub commit')
    return tag, ref['sha'], asset

def checked_archive(data, asset, binary, checkout, commit):
    if len(data) != asset['size'] or 'sha256:' + digest(data) != asset['digest']:
        raise ValueError('Release archive size or SHA-256 mismatch')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        expected = {PREFIX + x for x in MEMBERS}
        if len(infos) != len(expected) or {x.filename for x in infos} != expected or any(x.is_dir() for x in infos):
            raise ValueError('Unexpected, missing or duplicate ZIP member')
        result = {}
        for member in MEMBERS:
            content = archive.read(PREFIX + member)
            if content != git(binary, checkout, 'show', commit + ':' + member):
                raise ValueError('ZIP differs from tagged Git source: ' + member)
            result[member] = content
    return result

def router(source):
    tree = ast.parse(source)
    values = [node.value for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == 'ROUTER_BYTES' for t in node.targets)]
    if len(values) != 1:
        raise ValueError('Expected one static ROUTER_BYTES')
    value = ast.literal_eval(values[0])
    if not isinstance(value, bytes):
        raise ValueError('ROUTER_BYTES must be literal bytes')
    return value

def refresh(root, binary, checkout):
    tag, commit, asset = resolve_release()
    remote = git(binary, checkout, 'remote', 'get-url', 'origin').decode().strip()
    if remote not in (f'https://github.com/{REPO}.git', f'https://github.com/{REPO}', f'git@github.com:{REPO}.git'):
        raise ValueError('Upstream checkout origin is not the official repository')
    for revision in ('HEAD', 'refs/tags/' + tag):
        if git(binary, checkout, 'rev-parse', '--verify', revision + '^{commit}').decode().strip() != commit:
            raise ValueError('Checkout and tag must match official release commit')
    base = root / 'common' / 'sleep'
    manifest = json.loads((base / 'install.json').read_text(encoding='utf-8'))
    lock = json.loads((base / 'source-lock.json').read_text(encoding='utf-8'))
    if version(tag) < version('v' + manifest['version']):
        raise ValueError('Refusing release downgrade')
    if lock['source']['repository'] != REPO:
        raise ValueError('Unexpected source lock repository')
    if version(tag) == version('v' + manifest['version']):
        asset_identity = dict(path=asset['name'], size=asset['size'], sha256=asset['digest'].removeprefix('sha256:'))
        if lock['source']['commit'] != commit or lock['archive'] != asset_identity:
            raise ValueError('Existing release version points to a different commit or archive')
    if [(f['source'], f['target'], f['mode']) for f in manifest['files']] != [
        ('common/sleep/runtime/' + name, target, 420) for name, target in zip(RUNTIME, TARGETS)]:
        raise ValueError('Existing install mapping differs from the audited seven targets')
    archive = fetch(asset['browser_download_url'])
    members = checked_archive(archive, asset, binary, checkout, commit)
    package = json.loads(members['package.json'])
    if package.get('name') != 'boiler-sleep' or package.get('version') != tag[1:]:
        raise ValueError('Decky package identity differs from the release tag')
    payload = {name: members[name] for name in RUNTIME if name != '90-boiler-sleep.conf'}
    payload['90-boiler-sleep.conf'] = router(members['install.py'])
    manifest['version'] = tag[1:]
    for row, name in zip(manifest['files'], RUNTIME):
        row.update(size=len(payload[name]), sha256=digest(payload[name]))
    lock['source']['commit'] = commit
    lock['archive'] = {'path': asset['name'], 'size': len(archive), 'sha256': digest(archive)}
    lock['files'] = [{'path': 'common/sleep/runtime/' + n, 'size': len(payload[n]), 'sha256': digest(payload[n])} for n in RUNTIME]
    for name in RUNTIME:
        (base / 'runtime' / name).write_bytes(payload[name])
    for name, obj in (('install.json', manifest), ('source-lock.json', lock)):
        (base / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream-checkout', required=True, type=Path)
    parser.add_argument('--git', required=True, type=Path, help='Explicit trusted git executable')
    args = parser.parse_args()
    if not args.git.is_absolute() or not args.git.is_file() or not args.upstream_checkout.is_absolute():
        parser.error('Provide absolute paths to the git executable and verified upstream checkout')
    refresh(Path(__file__).resolve().parent.parent, args.git, args.upstream_checkout)

if __name__ == '__main__':
    main()
