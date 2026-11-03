"""Add an independently verified common-device stage to one model candidate.

This prepares an unsigned local candidate. It does not authenticate a release,
execute model hooks, inspect a physical device, or claim hardware acceptance.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.manifest import confined, validate_package
from protocol.local import import_local
from protocol.resource_pool import decoder
from scripts.resources import bmi260, canonical_stage

MODEL_REFS = {
    'device:oxp3': 'brands/onexplayer/oxp3/common-device-sources.toon',
    'device:x2-mini': 'brands/onexplayer/x2-mini/common-device-sources.toon',
}
RESOURCE_IDS = {'resource:intel-xevmafix-724', 'resource:intel-be201-firmware',
                'resource:oxp3-speaker-route', 'resource:bmi260-gyro'}
POLICY = 'common/ssd/acer/gm7/components.toon'
VERSION = '0.1.0-localtest.20261103.canonical-common.3'
COMPONENT_VERSIONS = {'oxp3:xevmafix':'2026.10.30', 'oxp3:speaker-route':'2026.10.30'}
BASE_COMPONENTS = {'oxp3:legacy-adaptation','oxp3:microphone','oxp3:speaker-route','oxp3:xevmafix'}


def sha(data): return hashlib.sha256(data).hexdigest()
def json_bytes(value): return (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode()


def stage_snapshot(stage):
    """Return the actual authenticated bytes, not paths to reread later."""
    stage=Path(stage)
    proof_raw=confined(stage,'preparation.json').read_bytes()
    proof=json.loads(proof_raw)
    if proof.get('kind')=='CanonicalCommonSnapshotPreparation':
        return canonical_stage.verify(stage,ROOT)
    semantic=dict(proof)
    lock_sha=semantic.pop('project_input_lock_sha256',None)
    if (not isinstance(lock_sha,str) or not re.fullmatch(r'[0-9a-f]{64}',lock_sha)
            or proof.get('project_input_lock')!='adaptations/resource-build-inputs.json'
            or sha(confined(ROOT,proof['project_input_lock']).read_bytes())!=lock_sha
            or sha(json.dumps(semantic,sort_keys=True,separators=(',',':')).encode()) != bmi260.SEMANTIC_SHA
            or proof.get('source_commit')!=bmi260.COMMIT or proof.get('deployable') is not False
            or proof.get('official_release') is not False):
        raise ValueError('Canonical preparation semantic proof differs')
    rows=proof['files']
    if len(rows)!=77 or len({r['path'] for r in rows})!=77: raise ValueError('Canonical inventory differs')
    content={}
    for row in rows:
        path=confined(stage,row['path']); raw=path.read_bytes()
        if len(raw)!=row['size'] or sha(raw)!=row['sha256']:
            raise ValueError('Canonical stage member differs: '+row['path'])
        content[row['path']]=raw
    actual={p.relative_to(stage).as_posix() for p in stage.rglob('*') if p.is_file()}
    if actual != set(content)|{'preparation.json'}: raise ValueError('Canonical stage inventory differs')
    return content,dict(source_proof_sha256=sha(proof_raw),project_input_lock=proof['project_input_lock'],
                        project_input_lock_sha256=lock_sha,source_semantic_sha256=bmi260.SEMANTIC_SHA)


def stage_reference(content, identity):
    toon = decoder(ROOT/'adaptations')
    reference = toon.loads(content['source/'+MODEL_REFS[identity]].decode('utf-8'), strict=True)
    guide_name='source/brands/onexplayer/'+identity.split(':',1)[1]+'/'+identity.split(':',1)[1]+'.toon'
    if guide_name in content:
        guide=toon.loads(content[guide_name].decode('utf-8'),strict=True)
        if guide.get('resource_model',{}).get('common_references')!=reference:
            raise ValueError('Generated common reference differs from canonical model TOON')
    if (reference.get('kind') != 'BoilerModelCommonReferences' or reference.get('model_id') != identity
            or reference.get('source_only') is not True or {r['id'] for r in reference.get('resources',[])} != RESOURCE_IDS
            or len(reference['resources']) != 4):
        raise ValueError('Canonical model reference differs')
    policies=reference.get('boot_policies')
    if policies != [dict(id='ssd:gm7-boot-repair',version=1,source=POLICY,component_ref='ssd-gm7-boot-repair')]:
        raise ValueError('Canonical GM7 model reference differs')
    definition=toon.loads(content['source/'+POLICY].decode('utf-8'), strict=True)
    component=copy.deepcopy(definition['definitions']['ssd-gm7-boot-repair'])
    if component.get('ssd_boot_policy') != {'id':'ssd:gm7-boot-repair','version':1}:
        raise ValueError('Canonical GM7 policy differs')
    component['id']=identity.split(':',1)[1]+':ssd-gm7-boot-repair'
    component['hooks']={}; component['compatibility']={}
    return reference, component


def replace_component_bytes(directory, manifest, component_id, replacements):
    matches=[c for c in manifest['components'] if c['id']==component_id]
    if len(matches)!=1: raise ValueError('Expected one existing component: '+component_id)
    component=matches[0]
    for basename, raw in replacements.items():
        rows=[r for r in component['files'] if Path(r['source']).name==basename]
        if len(rows)!=1: raise ValueError('Expected one component source: '+basename)
        target=confined(directory,rows[0]['source'])
        target.write_bytes(raw)
        rows[0]['size']=len(raw); rows[0]['sha256']=sha(raw)
    return component


def version_replaced_components(*components):
    from maintenance.updates import _version_key
    if {component['id'] for component in components} != set(COMPONENT_VERSIONS) or len(components)!=2:
        raise ValueError('Expected the two fixed common-device components')
    for component in components:
        version=COMPONENT_VERSIONS[component['id']]
        if _version_key(version) <= _version_key(component['version']):
            raise ValueError('Common-device component version must advance')
        component['version']=version


def version_legacy_bridge(manifest, version):
    matches=[item for item in manifest['components'] if item['id']=='oxp3:legacy-adaptation']
    if len(matches)!=1: raise ValueError('Exactly one legacy bridge is required')
    matches[0]['version']=version


def update_gyro(directory, identity, files, profiles):
    """Keep the existing legacy owner, journal, and model-specific profile."""
    integration=directory/'payload/legacy/integration'
    path=integration/'manifest.json'; value=json.loads(path.read_bytes())
    rows=value['gyro']['files']; by_target={r['target']:r for r in rows}
    if len(by_target)!=len(rows): raise ValueError('Duplicate existing gyro target')
    expected=set(files)|set(profiles)
    # The profile was previously transformed for the connector keyboard.
    # Preserve its exact model-owned bytes and existing calibration semantics.
    if not set(profiles) <= set(by_target): raise ValueError('Model gyro profile is missing')
    if set(by_target)-expected: raise ValueError('Unexpected existing gyro target')
    index=0
    for target,(raw,mode) in sorted(files.items()):
        if target in by_target:
            entry=by_target[target]
            if entry.get('mode')!=mode or entry.get('user',False) is not False:
                raise ValueError('Existing gyro metadata differs')
        else:
            while (integration/'gyro'/('canonical-%03d.bin'%index)).exists(): index+=1
            entry=dict(target=target,source='gyro/canonical-%03d.bin'%index,mode=mode,user=False)
            rows.append(entry); by_target[target]=entry
        confined(integration,entry['source']).write_bytes(raw)
    helper=directory/'payload/legacy/maintenance/app/oxp3_maintenance/gyro_binding.py'
    if not helper.is_file() or helper.read_bytes()!=files['var/lib/oxp3-gyro-v1/binding.py'][0]:
        raise ValueError('Retained maintenance gyro binding differs from canonical source')
    path.write_bytes(json_bytes(value))
    return sorted(by_target)


def configure_wifi_selection(manifest, previous, reviewed, *, canonical):
    """Do not offer legacy BE201 writes before trusted target matching exists."""
    wifi=[cap for cap in manifest.get('legacy_capabilities',[]) if cap.get('id')=='oxp3:wifi']
    if len(wifi)!=1 or wifi[0].get('available') is not True:
        raise ValueError('Expected the available legacy BE201 firmware handler')
    if 'oxp3:wifi' in previous['repairs']:
        raise ValueError('Base selection unexpectedly includes BE201 firmware')
    expected=copy.deepcopy(previous);expected['repairs'].append('oxp3:wifi')
    if reviewed!=expected:raise ValueError('Reviewed selection differs from the base plus BE201 firmware')
    if canonical:
        wifi[0]['default']=False;wifi[0]['available']=False
        wifi[0]['reason']={'en':'Unavailable until trusted target BE201 identity is established.',
                           'zh':'在可信确认目标设备为 BE201 之前不可用。'}
        choice=copy.deepcopy(previous)
    else:
        wifi[0]['default']=True;choice=copy.deepcopy(reviewed)
    selected=set(choice['repairs']+choice['plugins'])
    defaults={cap['id'] for cap in manifest['legacy_capabilities'] if cap.get('default')}
    available={cap['id'] for cap in manifest['legacy_capabilities'] if cap.get('available')}
    if selected!=defaults or not selected<=available:
        raise ValueError('Reviewed selection differs from available manifest defaults')
    return choice


def update_capabilities(manifest, identity, gyro_targets):
    for cap in manifest.get('legacy_capabilities',[]):
        if cap['id']=='oxp3:gm7-resume' and cap.get('available') is not False:
            raise ValueError('Legacy GM7 ownership must remain unavailable')
        if cap['id']=='oxp3:gyro':
            cap['targets']=gyro_targets
            if identity=='device:x2-mini':
                notes=cap.get('notes')
                if not isinstance(notes,dict): notes={}
                cap['notes']={
                    'en': (notes.get('en','')+' X2 gyro orientation is inherited and has not been independently measured.').strip(),
                    'zh': (notes.get('zh','')+' X2 陀螺仪方向沿用原配置，尚未独立测定。').strip(),
                }


def build(base, base_sha256, stage, public_archive, output, *, version=VERSION):
    base,stage,output=map(lambda p: Path(p).absolute(),(base,stage,output))
    if output.exists() or output.is_symlink(): raise ValueError('Use a fresh output directory')
    if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in (output,*output.parents)):
        raise ValueError('Output cannot traverse links')
    if output.resolve().is_relative_to(base.resolve()) or output.resolve().is_relative_to(stage.resolve()):
        raise ValueError('Output cannot be inside an input')
    manifest=validate_package(base,expected_manifest_sha256=base_sha256)
    identity=manifest.get('id')
    if identity not in MODEL_REFS: raise ValueError('Only the two reviewed model identities are supported')
    if len(manifest['components'])!=4 or {item['id'] for item in manifest['components']} != BASE_COMPONENTS:
        raise ValueError('A complete four-component model base is required')
    content,proof=stage_snapshot(stage)
    reference, provider=stage_reference(content,identity)
    files,profiles,bmi_proof=bmi260.prepare(stage,Path(public_archive))
    if (files['var/lib/oxp3-gyro-v1/binding.py'][0] != content[bmi260.COMMON+'sources/gyro_binding.py']
            or files['var/lib/oxp3-gyro-v1/oxp3_bmi260.ko'][0] != content['payload-inputs/bmi260/gyro/000.bin']
            or files['var/lib/oxp3-gyro-v1/inputplumber'][0] != content['payload-inputs/bmi260/gyro/001.bin']):
        raise ValueError('BMI preparation differs from the reviewed stage snapshot')
    if any(c.get('ssd_boot_policy') for c in manifest['components']):
        raise ValueError('The selected base already owns a GM7 provider')
    if any(c.get('id')==provider['id'] for c in manifest['components']): raise ValueError('Provider id conflict')
    # Both historical contracts must still be verified by their own providers.
    if identity=='device:oxp3':
        from components.oxp3_full_efi_provider import verify
        contract='oxp3_full_efi_contract'; lock=ROOT/'components/oxp3_full_efi_provider.lock.json'
    else:
        from components.x2_efi_provider import verify
        contract='x2_efi_contract'; lock=ROOT/'components/x2_efi_provider.lock.json'
    if not manifest.get(contract): raise ValueError('Fixed model EFI contract is required')
    verify(base,lock)
    directory=output/'package'; directory.mkdir(parents=True)
    for row in manifest['files']:
        source=confined(base,row['path']); destination=confined(directory,row['path'])
        destination.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(source,destination)
        raw=destination.read_bytes()
        if len(raw)!=row['size'] or sha(raw)!=row['sha256']: raise ValueError('Base changed during copy')
    for name,raw in content.items():
        if not (name.startswith('source/') or name.startswith('payload-inputs/be201/')): continue
        target=confined(directory,'canonical-common/'+name); target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(raw)
    xe=replace_component_bytes(directory,manifest,'oxp3:xevmafix',{
        'load.py':content['payload-inputs/xevma/load.py'],
        'xevmafix.ko':content['payload-inputs/xevma/xevmafix.ko']})
    speaker=replace_component_bytes(directory,manifest,'oxp3:speaker-route',{
        'speaker_route.py':content['payload-inputs/alc245/speaker_route.py']})
    version_replaced_components(xe,speaker)
    gyro_targets=update_gyro(directory,identity,files,profiles[identity.split(':',1)[1]])
    manifest['components'].append(provider)
    manifest['version']=version
    manifest['minimum_engine_version']='0.1.4'
    version_legacy_bridge(manifest,version)
    manifest['common_device_sources']=dict(kind='canonical-common-device-references-v1',
        source_commit=proof.get('source_commit',bmi260.COMMIT), model_reference='canonical-common/source/'+MODEL_REFS[identity],
        stage_source_proof_sha256=proof['source_proof_sha256'], resources=reference['resources'],
        boot_policies=reference['boot_policies'], gyro_targets=gyro_targets,
        bmi260=bmi_proof, x2_orientation='inherited profile; independent X2 measurement pending',
        be201='source and firmware preparation only; fixed matching handler integration pending',
        physical_hardware_accepted=False)
    update_capabilities(manifest,identity,gyro_targets)
    previous = json.loads(confined(directory, 'selection.json').read_bytes())
    reviewed = json.loads(confined(ROOT, 'adaptations/brands/onexplayer/oxp3/bridge/selection.example.json').read_bytes())
    choice = configure_wifi_selection(manifest, previous, reviewed, canonical=('source_commit' in proof))
    confined(directory, 'selection.json').write_bytes(json_bytes(choice))
    # Neither the verified EFI code closure nor unrelated legacy payloads are
    # modified by this finalization. Verify again after all generated writes.
    manifest['files']=[dict(path=p.relative_to(directory).as_posix(),size=p.stat().st_size,sha256=sha(p.read_bytes()))
                       for p in sorted(directory.rglob('*')) if p.is_file() and p!=directory/'manifest.json']
    (directory/'manifest.json').write_bytes(json_bytes(manifest))
    digest=sha((directory/'manifest.json').read_bytes())
    validate_package(directory,expected_manifest_sha256=digest); verify(directory,lock)
    archive=output/('boiler-'+identity.replace(':','-')+'-'+version+'.zip')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as bundle:
        for name in ['manifest.json']+[r['path'] for r in manifest['files']]:
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.create_system=3
            info.external_attr=0o100644<<16; info.compress_type=zipfile.ZIP_DEFLATED
            with bundle.open(info,'w',force_zip64=True) as out, confined(directory,name).open('rb') as src:
                shutil.copyfileobj(src,out,length=1024*1024)
    imported=import_local(archive,output/'verified-import',developer=True)
    if imported['manifest_sha256']!=digest: raise ValueError('Final package import differs')
    report=dict(kind='CanonicalCommonModelCandidate',id=identity,version=version,manifest_sha256=digest,
        archive=archive.name,size=archive.stat().st_size,sha256=sha(archive.read_bytes()),
        source_commit=proof.get('source_commit',bmi260.COMMIT),hardware_acceptance=False,official_release=False,
        pending=['BE201 fixed handler binding','physical audio acceptance',
                 'BMI260 physical calibration and X2 orientation','full Linux install, maintenance and recovery'])
    (output/'verification.json').write_bytes(json_bytes(report))
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base',required=True,type=Path); p.add_argument('--base-sha256',required=True)
    p.add_argument('--stage',required=True,type=Path); p.add_argument('--public-installer',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path); p.add_argument('--version',default=VERSION)
    a=p.parse_args(); print(json.dumps(build(a.base,a.base_sha256,a.stage,a.public_installer,a.output,version=a.version),indent=2))
