"""Rebase the HDR delta onto the authenticated X2 2026.11.5.2 package."""
from __future__ import annotations
import argparse
import importlib.util
import json
import re
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('x2_complete_builder', Path(__file__).with_name('build-x2-complete.py'))
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
OLD = '381dd40541f7fe7eb7c306ff055663b23fa2e111d15b938274d9012f6cbe8f9d'
VERSION = '2026.11.8.4'


def read_overlay(folder, pin):
    path = b.regular(folder, 'overlay-lock.json')
    b.require(b.sha(path) == pin, 'HDR overlay lock pin mismatch')
    lock = b.document(path.read_bytes())
    b.require(lock.get('kind') == 'BoilerX2CompleteOverlay' and lock.get('role') == 'hdr'
              and lock.get('base_manifest_sha256') == b.BASE_ANCHOR, 'Wrong HDR overlay')
    result, sources = {}, {'overlay-lock.json'}
    for row in lock['files']:
        name, source = b.relative(row['path']), b.relative(row['source'])
        b.require(name not in result and source not in sources, 'Duplicate HDR overlay path')
        b.require(name in b.HDR_SHARED_PATHS or name.startswith(b.HDR_PREFIXES), 'Non-HDR overlay path')
        item = b.regular(folder, source)
        b.require(item.stat().st_size == row['size'] and b.sha(item) == row['sha256'], 'Changed HDR overlay member')
        result[name] = row
        sources.add(source)
    return lock, result


def build(old, new, old_overlay, new_overlay, output, *, root, adaptations, new_manifest_sha256):
    b.require(re.fullmatch(r'[0-9a-f]{64}', new_manifest_sha256) is not None, 'Invalid candidate manifest pin')
    root, adaptations = Path(root).resolve(), Path(adaptations).resolve()
    sys.path.insert(0, str(root))
    from protocol.manifest import validate_package
    from protocol.local import import_local
    from components.x2_efi_provider import verify as verify_efi
    old, new, old_overlay, new_overlay = map(lambda p: Path(p).absolute(), (old,new,old_overlay,new_overlay))
    before = validate_package(old, expected_manifest_sha256=OLD)
    candidate = validate_package(new, expected_manifest_sha256=new_manifest_sha256)
    b.require(before['id'] == candidate['id'] == 'device:x2-mini' and before['version'] == '2026.11.5.2'
              and candidate['version'] == '2026.11.8.3', 'Unexpected X2 package identities')
    model, inputs = b.load_recipe(root, adaptations)
    b.require(model['resource_model']['hdr'] == {'profile':'x2-mini','full_frame':472,'peak':1100}, 'Unexpected TOON HDR mapping')
    old_pin, new_pin = before['source']['hdr_overlay_sha256'], inputs['hdr_overlay']['sha256']
    b.require(candidate['source']['hdr_overlay_sha256'] == new_pin, 'New package does not match TOON HDR pin')
    _, old_rows = read_overlay(old_overlay, old_pin)
    new_lock, new_rows = read_overlay(new_overlay, new_pin)
    b.require(new_lock['mapping'] == {'game':{'peak':1100,'full_frame':472},'desktop':{'peak':1100,'full_frame':472}}, 'Unexpected overlay mapping')
    old_files = {row['path']:row for row in before['files']}
    new_files = {row['path']:row for row in candidate['files']}
    targets = (set(old_rows) | set(new_rows)) & set(old_files)
    b.require(len(targets) == 10 and targets <= set(new_files), 'Unexpected HDR delta scope')
    for name in targets:
        if name in old_rows:
            b.require(old_files[name]['sha256'] == old_rows[name]['sha256'], 'Old HDR preimage mismatch: '+name)
        if name not in old_rows:
            b.require(old_files[name]['sha256'] == new_rows[name]['base_sha256'], 'New-only HDR preimage mismatch: '+name)
        if name in new_rows:
            b.require(new_files[name]['sha256'] == new_rows[name]['sha256'], 'New HDR member mismatch: '+name)
    old_efi = root/'components/x2_efi_provider.lock.json'
    new_efi = root/'components/x2_efi_provider.hdr1100.lock.json'
    verify_efi(old, old_efi)
    verify_efi(new, new_efi)
    output = b.output_path(output, (old,new,old_overlay,new_overlay))
    directory = output/'package'; directory.mkdir(parents=True)
    for name, row in old_files.items():
        b.copy_approved(b.regular(old,name),directory/name,row)
    for name in sorted(targets):
        b.copy_approved(b.regular(new,name),directory/name,new_files[name])
    generated = {b.GENERATED} if b.refresh_maintenance(directory) else set()
    metadata = b.document(b.encoded(before))
    expected_name = {'zh':'ONEXPLAYER X2 Mini / Mini PRO','en':'ONEXPLAYER X2 Mini / Mini PRO'}
    b.require(metadata['name'] == expected_name, 'Unexpected prior package name')
    metadata['name'] = {'zh':'ONEXPLAYER X2 Mini','en':'ONEXPLAYER X2 Mini'}
    matches = [item for item in metadata['legacy_capabilities'] if item['id'] == 'oxp3:desktop-hdr']
    b.require(len(matches) == 1, 'Unexpected desktop HDR capability')
    cap = matches[0]
    old_label = {'zh':'KDE \u684c\u9762 HDR \u6620\u5c04\u4e0e\u6062\u590d\uff08X2mini \u5b89\u5168\u8bd5\u7528\uff09', 'en':'KDE desktop HDR mapping and recovery (X2mini safe trial)'}
    b.require(cap['name'] == old_label and all(cap[k] == v for k,v in old_label.items()), 'Unexpected prior desktop HDR labels')
    new_label = {'zh':'KDE \u684c\u9762 HDR \u6620\u5c04\u4e0e\u6062\u590d', 'en':'KDE desktop HDR mapping and recovery'}
    cap.update(new_label); cap['name'] = dict(new_label)
    old_note = '\u4ec5\u7cbe\u786e\u8bc6\u522b X2mini \u65f6\u5b89\u88c5 KDE \u8bd5\u7528\u5165\u53e3\uff1b475/700 \u6620\u5c04\u3001\u771f\u5b9e HDR \u56db\u5757\u6d4b\u8bd5\u300110 \u79d2\u72ec\u7acb\u56de\u9000\u53ca\u786e\u8ba4\u4fdd\u5b58\u5df2\u5b9e\u73b0\u3002'
    new_note = '\u684c\u9762 HDR \u4f7f\u7528 1100/472 \u6620\u5c04\uff1b\u4e0d\u518d\u521b\u5efa\u8bd5\u7528\u5165\u53e3\uff0c\u4fdd\u7559\u65e7\u8bd5\u7528\u65e5\u5fd7\u7684\u542f\u52a8\u6062\u590d\u3002'
    b.require(cap['legacy_notes'].count(old_note) == 1, 'Unexpected prior HDR acceptance note')
    cap['legacy_notes'] = cap['legacy_notes'].replace(old_note, new_note)
    metadata['version'] = VERSION
    metadata['components'][0]['version'] = VERSION
    metadata['source']['hdr_overlay_sha256'] = new_pin
    metadata['complete_reuse']['hdr_mapping'] = {'paper_white_nits':472,'peak_nits':1100}
    if b.REUSE_RECORD in old_files:
        (directory/b.REUSE_RECORD).write_bytes(b.encoded(metadata['complete_reuse']))
        generated.add(b.REUSE_RECORD)
    for name, row in old_files.items():
        if name not in targets | generated:
            b.require(b.sha(b.regular(directory,name)) == row['sha256'], 'Unrelated file changed: '+name)
    rows = b.inventory(directory)
    metadata['files'] = rows
    b.refresh_component_inventory(metadata, rows)
    (directory/'manifest.json').write_bytes(b.encoded(metadata))
    manifest_sha = b.sha(directory/'manifest.json')
    validate_package(directory, expected_manifest_sha256=manifest_sha)
    verify_efi(directory, new_efi)
    archive = output/('boiler-device-x2-mini-'+VERSION+'.zip')
    b.deterministic_zip(directory,archive,rows)
    import_local(archive,output/'verified-import',developer=True)
    validate_package(output/'verified-import', expected_manifest_sha256=manifest_sha)
    report = {'version':VERSION,'manifest_sha256':manifest_sha,'archive':archive.name,'size':archive.stat().st_size,
              'sha256':b.sha(archive),'old_manifest_sha256':OLD,'candidate_manifest_sha256':new_manifest_sha256,
              'old_hdr_overlay_sha256':old_pin,'hdr_overlay_sha256':new_pin,'replaced':sorted(targets),
              'generated':sorted(generated),'hardware_acceptance':False}
    (output/'verification.json').write_bytes(b.encoded(report))
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for arg in ('old','new','old-overlay','new-overlay','output','project-root','adaptations-root'):
        p.add_argument('--'+arg,type=Path,required=True)
    p.add_argument('--new-manifest-sha256',required=True)
    a=p.parse_args()
    print(json.dumps(build(a.old,a.new,a.old_overlay,a.new_overlay,a.output,root=a.project_root,adaptations=a.adaptations_root,new_manifest_sha256=a.new_manifest_sha256),indent=2))

if __name__ == '__main__': main()
