"""Fixed OXP3 split-lock EFI bytes with independent, reversible ownership.

Only the trusted installer bridge may invoke this module after authenticating
its package inventory, known-code closure, and reviewed canonical selection.
The hashes accepted here bind that already-established authority; they do not
make a package trustworthy. No arbitrary renderer, command, or target is used.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import os
from pathlib import Path
import re
import stat

from .engine import Engine, MaintenanceError
from .keyboard_runtime.vendor.transactions import Store
from .keyboard_runtime.vendor import split_lock as canonical
from . import ssd_boot as boot

STATE = 'home/.boiler/boot-ownership-v1'
HASH = re.compile(r'[0-9a-f]{64}\Z')
SPLIT_LOCK_SHA256 = '1eb270c5ac1151422b26d0233ee68b52155042b316c26a6e932bcbd4e8abc694'
LICENSE_SHA256 = '1959942e9e263938024cbd4d8c63bba342647877777dd48056c467672dcb915a'
DROPIN = 'etc/default/grub.d/oxp3-splitlock.cfg'
LICENSE = 'usr/share/licenses/oxp3-split-lock/UPSTREAM_LICENSE'
COMPANIONS = (DROPIN, LICENSE)


def _canonical():
    with boot._file(Path(canonical.__file__)) as (stream, info):
        if info.st_size > 65536 or hashlib.sha256(stream.read(65537)).hexdigest() != SPLIT_LOCK_SHA256:
            raise MaintenanceError('Canonical split-lock renderer differs from its fixed source lock')
    return canonical


def _posix_snapshot(root, relative):
    path = Path(root)/relative
    try:
        with boot._file(path) as (stream, info):
            data=stream.read(1024*1024+1)
            if len(data) > 1024*1024:
                raise MaintenanceError('Split-lock companion is oversized')
            return dict(exists=True,data=boot._encode(data),mode=stat.S_IMODE(info.st_mode),uid=info.st_uid,gid=info.st_gid)
    except FileNotFoundError:
        cursor=path.parent
        while not os.path.lexists(cursor):
            if cursor == Path(root) or not cursor.is_relative_to(root):
                raise MaintenanceError('Unsafe split-lock companion ancestor')
            cursor=cursor.parent
        with boot._directory(cursor):
            pass
        return {'exists':False}


def _companions(root):
    return {name:_posix_snapshot(root,name) for name in COMPANIONS}


def _expected_bytes(name, encoded):
    try:
        data=base64.b64decode(encoded,validate=True)
    except (ValueError,TypeError) as error:
        raise MaintenanceError('Invalid split-lock companion bytes') from error
    if len(data) > 1024*1024:
        raise MaintenanceError('Split-lock companion exceeds size limit')
    if name == DROPIN and data != _canonical().DROPIN_DATA:
        raise MaintenanceError('Split-lock drop-in differs from fixed source')
    if name == LICENSE and hashlib.sha256(data).hexdigest() != LICENSE_SHA256:
        raise MaintenanceError('Split-lock license differs from fixed source')
    return data


def _preflight_companions(root):
    originals=_companions(root)
    for name, value in originals.items():
        if value['exists']:
            _expected_bytes(name,value['data'])
    return originals


def _companion_journal(root, slot, originals, *, phase):
    path=Path(root)/'home/.oxp3-installer/integration'/slot/'split-lock/state.json'
    if not os.path.lexists(path):
        if phase == 'rolled_back' and _companions(root) == originals:
            return None
        raise MaintenanceError('Split-lock companion journal is missing')
    try:
        with boot._file(path,private=True) as (stream,info):
            if info.st_size > 8*1024*1024:
                raise MaintenanceError('Split-lock companion journal is oversized')
            raw=stream.read(8*1024*1024+1)
        journal=boot._decode_json(raw)
        if (not isinstance(journal,dict) or journal.get('phase') != phase
                or journal.get('allowed') != list(COMPANIONS)
                or journal.get('original') != originals
                or not isinstance(journal.get('applied'),dict)):
            raise MaintenanceError('Split-lock companion journal does not match the combined intent')
        applied=journal['applied']
        if phase == 'installed':
            if set(applied) != set(COMPANIONS):
                raise MaintenanceError('Incomplete split-lock companion journal')
            actual=_companions(root)
            for name in COMPANIONS:
                _expected_bytes(name,applied[name])
                observed=actual[name]
                if not observed.get('exists') or observed.get('data') != applied[name]:
                    raise MaintenanceError('Split-lock companion bytes changed')
                original=originals[name]
                if original['exists']:
                    expected_metadata={key:original[key] for key in ('mode','uid','gid')}
                else:
                    parent=(Path(root)/name).parent.stat()
                    expected_metadata=dict(mode=0o644,uid=0,gid=parent.st_gid if parent.st_mode & stat.S_ISGID else 0)
                if any(observed[key] != expected_metadata[key] for key in expected_metadata):
                    raise MaintenanceError('Split-lock companion ownership changed')
        elif _companions(root) != originals:
            raise MaintenanceError('Split-lock companions are not restored to their first originals')
        return hashlib.sha256(raw).hexdigest()
    except (KeyError,TypeError,ValueError,UnicodeError,RecursionError) as error:
        raise MaintenanceError('Invalid split-lock companion journal') from error



def _source_for(identity, selection, package_id):
    """Bind already-authenticated outer context to the caller's exact model."""
    if (not isinstance(identity, dict)
            or set(identity) != {'package_id','manifest_sha256','selection_sha256'}
            or identity['package_id'] != package_id
            or any(not isinstance(identity[key],str) or not HASH.fullmatch(identity[key])
                   for key in ('manifest_sha256','selection_sha256'))):
        raise MaintenanceError('Invalid authenticated model source binding')
    keys = {'schema_version','slot','repairs','plugins','installer_language','kde_language',
            'acknowledge_legacy_update_hold'}
    if (not isinstance(selection, dict) or set(selection) != keys
            or type(selection['schema_version']) is not int or selection['schema_version'] != 1
            or selection['slot'] != 'guest' or selection['acknowledge_legacy_update_hold'] is not True
            or not isinstance(selection['repairs'],list) or not isinstance(selection['plugins'],list)
            or any(not isinstance(item,str) or not item.startswith('oxp3:')
                   for item in selection['repairs']+selection['plugins'])
            or len(set(selection['repairs']+selection['plugins'])) != len(selection['repairs']+selection['plugins'])
            or 'oxp3:split-lock' not in selection['repairs']
            or 'oxp3:gm7-resume' in selection['repairs']
            or not isinstance(selection['installer_language'],str)
            or not isinstance(selection['kde_language'],str)
            or boot.digest(selection) != identity['selection_sha256']):
        raise MaintenanceError('Model selection is not the fixed reviewed split-lock choice')
    return dict(identity)


def _source(identity, selection):
    return _source_for(identity, selection, 'device:oxp3')


def _x2_source(identity, selection):
    return _source_for(identity, selection, 'device:x2-mini')


def _model_source(identity, selection):
    if isinstance(identity,dict) and identity.get('package_id') == 'device:x2-mini':
        return _x2_source(identity,selection)
    return _source(identity,selection)


def _model_target(bound):
    return {key: bound[key] for key in ('root_partuuid','slot','esp_partuuid','esp_type','home_uuid')}


def _render(original):
    if not isinstance(original, bytes) or not original or len(original) > boot._MAX or b'\0' in original:
        raise MaintenanceError('Invalid model EFI configuration')
    try:
        return _canonical().patch_grub(original.decode('utf-8','strict')).encode('utf-8')
    except (UnicodeError, ValueError) as error:
        raise MaintenanceError('Cannot safely render model split-lock EFI settings') from error


def _record_for(value, bound, package_id):
    keys={'version','target','source','original','applied','status','companions_original','journal_sha256'}
    if (not isinstance(value,dict) or set(value) != keys or value['version'] != 2
            or value['target'] != _model_target(bound) or value['status'] not in ('installed','restored')
            or not isinstance(value['journal_sha256'],str) or not HASH.fullmatch(value['journal_sha256'])
            or not isinstance(value['companions_original'],dict)
            or set(value['companions_original']) != set(COMPANIONS)):
        raise MaintenanceError('Model EFI state differs from the target or is corrupt')
    source = value['source']
    if (not isinstance(source,dict) or set(source) != {'package_id','manifest_sha256','selection_sha256'}
            or source['package_id'] != package_id
            or any(not isinstance(source[key],str) or not HASH.fullmatch(source[key])
                   for key in ('manifest_sha256','selection_sha256'))):
        raise MaintenanceError('Invalid saved model EFI source binding')
    for entry in value['companions_original'].values():
        if not isinstance(entry,dict) or type(entry.get('exists')) is not bool:
            raise MaintenanceError('Invalid original split-lock companion snapshot')
        if not entry['exists']:
            if entry != {'exists':False}:
                raise MaintenanceError('Invalid absent split-lock companion snapshot')
        else:
            if (set(entry) != {'exists','data','mode','uid','gid'}
                    or any(type(entry[key]) is not int or entry[key] < 0 for key in ('mode','uid','gid'))):
                raise MaintenanceError('Invalid split-lock companion metadata')
            try: base64.b64decode(entry['data'],validate=True)
            except (ValueError,TypeError) as error: raise MaintenanceError('Invalid split-lock companion snapshot') from error
    original, applied = boot._decode(value['original']), boot._decode(value['applied'])
    if _render(original) != applied:
        raise MaintenanceError('Invalid model EFI byte lineage')
    return original, applied


def _record(value, bound):
    return _record_for(value, bound, 'device:oxp3')


def _x2_record(value, bound):
    return _record_for(value, bound, 'device:x2-mini')


def _model_record(value, bound):
    if isinstance(value,dict) and isinstance(value.get('source'),dict) and value['source'].get('package_id') == 'device:x2-mini':
        return _x2_record(value,bound)
    return _record(value,bound)


def _check_pair(pair,source):
    if (not isinstance(pair,dict) or set(pair) != {'kind','version','source','slots','pair_sha256'}
            or pair['kind'] != 'FreshModelSsdPair' or pair['version'] != 1 or pair['source'] != source
            or not isinstance(pair['slots'],dict) or set(pair['slots']) != {'A','B'}
            or boot.digest({k:v for k,v in pair.items() if k != 'pair_sha256'}) != pair['pair_sha256']):
        raise MaintenanceError('Invalid fresh two-slot authorization')
    for label, slot in pair['slots'].items():
        if (not isinstance(slot,dict) or set(slot) != {'target','model_plan_sha256','ssd_preflight_sha256','model_efi_sha256'}
                or not isinstance(slot['target'],dict) or slot['target'].get('slot') != label
                or any(not isinstance(slot[key],str) or not HASH.fullmatch(slot[key])
                       for key in ('model_plan_sha256','model_efi_sha256'))
                or slot['ssd_preflight_sha256'] is not None and (not isinstance(slot['ssd_preflight_sha256'],str) or not HASH.fullmatch(slot['ssd_preflight_sha256']))):
            raise MaintenanceError('Invalid fresh two-slot target')
    if (pair['slots']['A']['ssd_preflight_sha256'] is None) != (pair['slots']['B']['ssd_preflight_sha256'] is None):
        raise MaintenanceError('Fresh pair must have SSD preflights for both slots or neither')
    a,b=(pair['slots'][slot]['target'] for slot in ('A','B'))
    if (a['root_partuuid'] == b['root_partuuid'] or a['esp_partuuid'] == b['esp_partuuid']
            or a['home_uuid'] != b['home_uuid']):
        raise MaintenanceError('Fresh two-slot targets are not distinct or do not share home')


def _check_journal_inventory(root, slot, seen):
    """Reject any unlisted installer journal in this slot or shared scope."""
    root=Path(root)
    for group in (slot,'shared'):
        base=root/'home/.oxp3-installer/integration'/group
        if not os.path.lexists(base):
            continue
        with boot._directory(base) as descriptor:
            with os.scandir(base if os.name == 'nt' else descriptor) as entries:
                count=0
                for entry in entries:
                    count+=1
                    if count > 20000 or entry.is_symlink():
                        raise MaintenanceError('Unsafe or oversized legacy journal inventory')
                    if not entry.is_dir(follow_symlinks=False):
                        if not entry.is_file(follow_symlinks=False):
                            raise MaintenanceError('Unknown legacy integration state; preserve it for review')
                        if entry.name == 'target-disk.json' and group == slot:
                            relative=f'home/.oxp3-installer/integration/{slot}/target-disk.json'
                            if relative not in seen:
                                raise MaintenanceError('Target disk record is absent from protected adapter evidence')
                            with boot._file(base/entry.name,private=True) as (stream,info):
                                if info.st_size != 5 or stream.read(6) != b'null\n':
                                    raise MaintenanceError('Unexpected legacy target disk record; preserve it for review')
                        elif entry.name not in ('result.json','request.json'):
                            raise MaintenanceError('Unknown legacy integration state; preserve it for review')
                        continue
                    boot.relative_path(entry.name)
                    relative=f'home/.oxp3-installer/integration/{group}/{entry.name}/state.json'
                    journal=base/entry.name/'state.json'
                    with boot._directory(base/entry.name):
                        pass
                    if not os.path.lexists(journal) or relative not in seen:
                        raise MaintenanceError('Unlisted legacy component journal; preserve it for review')


def _verify_adapter_proof(root,source,slot='A', *, model_record_path=None):
    """Verify the completed first-slot bridge record and protected evidence."""
    if slot not in ('A','B'):
        raise MaintenanceError('Invalid adapter proof slot')
    path=Path(root)/'home/.boiler/oxp3-adapter'/(slot+'.json')
    with boot._file(path,private=True) as (stream,info):
        if info.st_size > 8*1024*1024:
            raise MaintenanceError('Fresh sibling adapter record exceeds size limit')
        record=boot._decode_json(stream.read(8*1024*1024+1))
    if (not isinstance(record,dict) or record.get('schema_version') != 1 or record.get('phase') != 'applied'
            or boot.digest(record.get('selection')) != source['selection_sha256']):
        raise MaintenanceError('Fresh sibling adapter selection or status differs')
    proof=record.get('verification')
    if (not isinstance(proof,dict) or proof.get('slot') != slot
            or not isinstance(proof.get('evidence'),list)
            or boot.digest({key:value for key,value in proof.items() if key != 'sha256'}) != proof.get('sha256')):
        raise MaintenanceError('Fresh sibling adapter proof is invalid')
    seen=set()
    for item in proof['evidence']:
        if (not isinstance(item,dict) or set(item) != {'path','sha256','size'}
                or not isinstance(item['path'],str) or item['path'] in seen
                or not isinstance(item['sha256'],str) or not HASH.fullmatch(item['sha256'])
                or type(item['size']) is not int or not 0 <= item['size'] <= 128*1024*1024):
            raise MaintenanceError('Invalid fresh sibling evidence')
        relative=boot.relative_path(item['path'])
        if not (relative.startswith('home/.oxp3-installer/integration/') or relative.startswith('opt/')
                or relative == model_record_path):
            raise MaintenanceError('Unexpected fresh sibling evidence location')
        with boot._file(Path(root)/relative,private=True) as (stream,info):
            if info.st_size != item['size']:
                raise MaintenanceError('Fresh sibling evidence size changed')
            digest=hashlib.sha256()
            while True:
                chunk=stream.read(1024*1024)
                if not chunk: break
                digest.update(chunk)
        if digest.hexdigest() != item['sha256']:
            raise MaintenanceError('Fresh sibling evidence changed')
        seen.add(relative)
    required={f'home/.oxp3-installer/integration/{slot}/result.json',
              f'home/.oxp3-installer/integration/{slot}/split-lock/state.json'}
    if model_record_path is not None:
        required.add(model_record_path)
    if not required <= seen:
        raise MaintenanceError('Adapter proof lacks model result, model record or split-lock journal')
    _check_journal_inventory(root,slot,seen)


def reject_conflicting_component(engine,component):
    """Read-only generic-write barrier; caller holds the existing Engine lock."""
    files=component.get('files',[])
    hooks=component.get('hooks',{})
    if (not isinstance(files,list) or any(not isinstance(item,dict) or 'target' not in item for item in files)
            or not isinstance(hooks,dict)):
        raise MaintenanceError('Invalid component targets or hooks')
    targets=tuple(path.casefold() for path in (boot.TARGET,DROPIN,LICENSE))
    relevant=False
    for item in files:
        target=boot.relative_path(item['target']).casefold()
        relevant |= any(target == fixed or target.startswith(fixed+'/') or fixed.startswith(target+'/') for fixed in targets)
    if not relevant:
        return
    directory=engine.home_root/'.boiler/boot-ownership-v1'
    if not os.path.lexists(directory):
        return
    with boot._directory(directory) as fd:
        if os.name != 'nt' and os.fstat(fd).st_mode & 0o077:
            raise MaintenanceError('Model EFI ownership directory must be private')
        names=os.listdir(directory if os.name == 'nt' else fd)
        if len(names) > 64:
            raise MaintenanceError('Unknown model EFI ownership state')
        pair=None; records={}
        for name in names:
            if name == 'model-pending.json':
                raise MaintenanceError('Interrupted model EFI operation requires recovery first')
            if name == 'lock':
                with boot._file(directory/name,private=True): pass
                continue
            if name != 'pair.json' and not re.fullmatch(r'model-esp-[a-z0-9-]{8,64}\.json',name):
                raise MaintenanceError('Unknown model EFI ownership state; preserve it for review')
            try:
                with boot._file(directory/name,private=True) as (stream,info):
                    if info.st_size > 8*1024*1024:
                        raise MaintenanceError('Model EFI ownership state is oversized')
                    value=boot._decode_json(stream.read(8*1024*1024+1))
                if name == 'pair.json':
                    _check_pair(value,value['source'])
                    pair=value
                else:
                    target=value['target']
                    _model_record(value,target)
                    if (name != 'model-esp-'+target['esp_partuuid']+'.json'
                            or target['slot'] not in ('A','B') or target['slot'] in records
                            or target['esp_type'] != boot.ESP_TYPE
                            or not isinstance(target['root_partuuid'],str) or not boot._PART.fullmatch(target['root_partuuid'])
                            or not isinstance(target['esp_partuuid'],str) or not boot._PART.fullmatch(target['esp_partuuid'])
                            or not isinstance(target['home_uuid'],str) or not target['home_uuid']):
                        raise MaintenanceError('Invalid model EFI ownership target')
                    records[target['slot']]=value
                    if value['status']=='installed':
                        raise MaintenanceError('Model EFI owns boot paths; restore it first')
            except (KeyError,TypeError,ValueError,UnicodeError,RecursionError) as error:
                raise MaintenanceError('Invalid model EFI ownership state; preserve it for review') from error
        if pair is not None:
            if set(records) != {'A','B'}:
                raise MaintenanceError('Fresh model pair has unfinished ownership')
            for slot in ('A','B'):
                if records[slot]['target'] != pair['slots'][slot]['target'] or records[slot]['source'] != pair['source']:
                    raise MaintenanceError('Model EFI records differ from the fresh pair')


def _ssd_restore_barrier(root, bound, source):
    """Block current or uncertain SSD ownership; permit only the verified other slot."""
    directory=Path(root)/boot.STATE
    parent=Path(root)/'home/.boiler'
    if os.path.lexists(parent):
        with boot._directory(parent): pass
    if not os.path.lexists(directory):
        return
    actual=boot._durable(bound)
    model_store=Store(Path(root),Path(root)/STATE)
    pair=model_store._state_read('pair.json')
    _check_pair(pair,source)
    if pair['slots'][bound['slot']]['target'] != _model_target(bound):
        raise MaintenanceError('Current model target differs from the protected pair')
    seen=set()
    with boot._directory(directory) as fd:
        if os.name != 'nt' and os.fstat(fd).st_mode & 0o077:
            raise MaintenanceError('SSD ownership state directory must be private')
        names=os.listdir(directory if os.name == 'nt' else fd)
        if len(names) > 4: raise MaintenanceError('Unknown SSD ownership state')
        for name in names:
            if name == 'pending.json':
                raise MaintenanceError('Interrupted SSD repair requires recovery first')
            if name == 'lock':
                with boot._file(directory/name,private=True): pass
                continue
            if not re.fullmatch(r'esp-[a-z0-9-]{8,64}\.json',name):
                raise MaintenanceError('Unknown SSD ownership state')
            with boot._file(directory/name,private=True) as (stream,info):
                if info.st_size > 8*1024*1024: raise MaintenanceError('SSD ownership state is oversized')
                value=boot._decode_json(stream.read(8*1024*1024+1))
            try:
                target=value['target']; physical=target['physical']; slot=target['slot']
                if (set(target) != set(actual) or slot not in ('A','B') or slot in seen
                        or target['esp_type'] != boot.ESP_TYPE or name != 'esp-'+target['esp_partuuid']+'.json'
                        or not boot._PART.fullmatch(target['root_partuuid']) or not boot._PART.fullmatch(target['esp_partuuid'])
                        or not isinstance(target['home_uuid'],str) or not target['home_uuid']
                        or not isinstance(physical,dict) or set(physical) != {'bus','model','serial'}
                        or physical['bus'] != 'nvme' or not isinstance(physical['serial'],str)
                        or not physical['serial'] or boot.classify_reviewed_device('NVMe',physical['model']) != 'match'
                        or any(not isinstance(v,str) or v != ' '.join(v.split()).casefold() for v in physical.values())):
                    raise MaintenanceError('Invalid SSD ownership target')
                original,applied=boot._record(value,target)
                if (target['home_uuid'] != actual['home_uuid'] or physical != actual['physical']
                        or pair['slots'][slot]['target'] != {k:target[k] for k in ('root_partuuid','slot','esp_partuuid','esp_type','home_uuid')}):
                    raise MaintenanceError('SSD ownership does not match actual protected pair')
                seen.add(slot)
                handoff=value.get('handoff')
                if not isinstance(handoff,dict): raise MaintenanceError('SSD ownership has no authenticated model handoff')
                preflight=handoff['preflight']; proof=handoff['model_proof']
                sibling=model_store._state_read('model-esp-'+target['esp_partuuid']+'.json')
                _,model_applied=_model_record(sibling,target)
                if (sibling['source'] != source or not isinstance(preflight,dict)
                        or preflight.get('kind') != 'ReviewedModelSsdPreflight' or preflight.get('version') != 1
                        or preflight.get('target') != target or preflight.get('source') != source
                        or preflight.get('model_plan_sha256') != pair['slots'][slot]['model_plan_sha256']
                        or preflight.get('model_efi_sha256') != pair['slots'][slot]['model_efi_sha256']
                        or preflight.get('model_efi_sha256') != hashlib.sha256(model_applied).hexdigest()
                        or boot.digest({k:v for k,v in preflight.items() if k != 'review_sha256'}) != preflight.get('review_sha256')
                        or pair['slots'][slot]['ssd_preflight_sha256'] != preflight.get('review_sha256')
                        or not isinstance(proof,dict) or proof.get('source') != source
                        or proof.get('target') != _model_target(target)
                        or proof.get('applied_sha256') != hashlib.sha256(model_applied).hexdigest()
                        or boot.digest({k:v for k,v in proof.items() if k != 'proof_sha256'}) != proof.get('proof_sha256')
                        or original['efi'] != sibling['applied'] or boot._decode(applied['efi']) != boot.render_grub_config(model_applied)):
                    raise MaintenanceError('SSD lineage differs from protected model pair')
                same=(slot == bound['slot'] or target['root_partuuid'] == actual['root_partuuid'] or target['esp_partuuid'] == actual['esp_partuuid'])
                if same:
                    if target != actual: raise MaintenanceError('SSD ownership partially overlaps current target')
                    if value['status'] == 'installed': raise MaintenanceError('Restore the current slot SSD repair first')
                elif value['status'] == 'installed':
                    if sibling['status'] != 'installed' or proof != _make_proof(root,target,sibling):
                        raise MaintenanceError('Other-slot installed model proof changed')
            except (KeyError,TypeError,ValueError,UnicodeError,RecursionError) as error:
                raise MaintenanceError('Invalid SSD ownership state; preserve it for inspection') from error


def model_efi_authorize_pair(root_a,root_b,host_context,source_identity,selection,model_plans,ssd_preflights=None):
    """Record the exact two reviewed slots only after both read-only preflights."""
    if os.name != 'posix' or os.geteuid() != 0:
        raise MaintenanceError('Fresh pair authorization requires Linux root')
    if (not isinstance(model_plans,(list,tuple)) or len(model_plans) != 2
            or ssd_preflights is not None and (not isinstance(ssd_preflights,(list,tuple)) or len(ssd_preflights) != 2)):
        raise MaintenanceError('Both model and SSD slot preflights are required')
    owners=[ModelEFI(root_a,host_context,source_identity,selection),ModelEFI(root_b,host_context,source_identity,selection)]
    with owners[0].store.lock():
        names=os.listdir(owners[0].store.state)
        if set(names) - {'lock'}:
            raise MaintenanceError('Fresh pair requires an empty model ownership ledger')
        if os.stat(owners[0].root/'home').st_dev != os.stat(owners[1].root/'home').st_dev:
            raise MaintenanceError('Both model roots must use the same mounted shared home')
        slots={}
        previews=ssd_preflights if ssd_preflights is not None else (None,None)
        for label,owner,model_plan,ssd_preview in zip(('A','B'),owners,model_plans,previews):
            actual=owner.plan('apply')
            if actual != model_plan or actual['target']['slot'] != label:
                raise MaintenanceError('Model pair preflight changed; review again')
            if ssd_preview is not None:
                fresh=boot.installer_preflight_model_ssd(owner.root,host_context,model_plan,provider_owner=ssd_preview.get('provider_owner'))
                if fresh != ssd_preview or fresh['source'] != owner.source:
                    raise MaintenanceError('SSD pair preflight changed; review again')
                preview_sha=fresh['review_sha256']
            else:
                preview_sha=None
            slots[label]=dict(target=_model_target(actual['target']),model_plan_sha256=actual['review_sha256'],
                              ssd_preflight_sha256=preview_sha,model_efi_sha256=actual['expected_efi_sha256'])
        body=dict(kind='FreshModelSsdPair',version=1,source=owners[0].source,slots=slots)
        pair=dict(body,pair_sha256=boot.digest(body))
        _check_pair(pair,owners[0].source)
        if model_plans[0]['target']['disk'] != model_plans[1]['target']['disk']:
            raise MaintenanceError('Fresh model slots do not have the same parent disk')
        owners[0].store._state_write('pair.json',pair)
        return pair


def _make_proof(root,bound,record):
    original,applied=_model_record(record,bound)
    relative=STATE+'/model-esp-'+bound['esp_partuuid']+'.json'
    with boot._file(Path(root)/relative,private=True) as (stream,info):
        if info.st_size > 8*1024*1024:
            raise MaintenanceError('Model EFI record exceeds size limit')
        raw=stream.read(8*1024*1024+1)
    if boot._decode_json(raw) != record:
        raise MaintenanceError('Opened model EFI record differs from validated state')
    body=dict(kind='ModelEfiProof',version=2,target=_model_target(bound),source=record['source'],
              original_sha256=hashlib.sha256(original).hexdigest(),applied_sha256=hashlib.sha256(applied).hexdigest(),
              applied_size=len(applied),record_sha256=boot.digest(record),record_path=relative,
              record_file_sha256=hashlib.sha256(raw).hexdigest(),record_file_size=len(raw),
              companion_journal_sha256=record['journal_sha256'])
    return dict(body,proof_sha256=boot.digest(body))


def _ssd_projection(root,context,bound,model_record,current):
    """Accept S only if the separately owned SSD record proves exact M→S."""
    if bound['disposition'] != 'match':
        return False
    store=Store(Path(root),Path(root)/boot.STATE)
    if not os.path.lexists(store.state):
        return False
    with boot._directory(store.state) as descriptor:
        if os.name != 'nt' and os.fstat(descriptor).st_mode & 0o077:
            raise MaintenanceError('SSD projection state must be private')
    if store._state_read('pending.json') is not None:
        raise MaintenanceError('Interrupted SSD repair requires recovery first')
    record=store._state_read('esp-'+bound['esp_partuuid']+'.json')
    if not isinstance(record,dict) or record.get('version') not in (3,4) or record.get('status') != 'installed' or 'handoff' not in record:
        return False
    original,applied=boot._record(record,bound)
    if (original['efi'] != model_record['applied'] or applied['efi'] != boot._encode(current)
            or boot._snapshot(root,bound) != applied):
        raise MaintenanceError('SSD projection bytes differ from their protected model and SSD journals')
    handoff=record['handoff']
    observed,proof,model_bytes=validate_model_handoff(root,context,handoff['preflight'],expected_proof=handoff['model_proof'])
    if observed != bound or model_bytes != boot._decode(model_record['applied']) or proof['record_sha256'] != boot.digest(model_record):
        raise MaintenanceError('SSD projection does not prove the current model lineage')
    return True


def validate_model_handoff(root, host_context, preflight, *, expected_proof=None):
    """Read protected fixed model proof while the caller holds the boot lock.

    This function never creates or acquires a lock. The outer bridge previously
    authenticated the code and created the fixed, fresh pair authorization.
    """
    if (not isinstance(preflight,dict) or preflight.get('kind') != 'ReviewedModelSsdPreflight'
            or preflight.get('version') != 1 or boot.digest({k:v for k,v in preflight.items() if k != 'review_sha256'}) != preflight.get('review_sha256')):
        raise MaintenanceError('Invalid reviewed model-to-SSD preflight')
    root=Path(os.path.abspath(root))
    bound=boot.binding(root,installer_context=host_context)
    if bound['disposition'] != 'match' or preflight.get('target') != boot._durable(bound):
        raise MaintenanceError('Model-to-SSD preflight belongs to another target')
    source=preflight.get('source')
    store=Store(root,root/STATE)
    if not os.path.lexists(store.state):
        raise MaintenanceError('Model EFI ownership state is missing')
    with boot._directory(store.state) as descriptor:
        if os.name != 'nt' and os.fstat(descriptor).st_mode & 0o077:
            raise MaintenanceError('Model EFI ownership state is not private')
    pair=store._state_read('pair.json')
    _check_pair(pair,source)
    slot=pair['slots'][bound['slot']]
    if (slot['target'] != _model_target(bound) or slot['ssd_preflight_sha256'] != preflight['review_sha256']
            or slot['model_plan_sha256'] != preflight.get('model_plan_sha256')
            or slot['model_efi_sha256'] != preflight.get('model_efi_sha256')):
        raise MaintenanceError('Model-to-SSD preflight differs from the protected fresh pair')
    if store._state_read('model-pending.json') is not None:
        raise MaintenanceError('Interrupted model EFI operation requires recovery first')
    record=store._state_read('model-esp-'+bound['esp_partuuid']+'.json')
    original,applied=_model_record(record,bound)
    if record['status'] != 'installed' or record['source'] != source:
        raise MaintenanceError('The authenticated model EFI layer is not installed')
    if (hashlib.sha256(original).hexdigest() != preflight.get('original_efi_sha256')
            or hashlib.sha256(applied).hexdigest() != preflight.get('model_efi_sha256')
            or hashlib.sha256(boot.render_grub_config(applied)).hexdigest() != preflight.get('ssd_efi_sha256')):
        raise MaintenanceError('Model EFI byte lineage differs from the reviewed handoff')
    journal_sha=_companion_journal(root,bound['slot'],record['companions_original'],phase='installed')
    if journal_sha != record['journal_sha256']:
        raise MaintenanceError('Model companion journal changed after its proof')
    proof=_make_proof(root,bound,record)
    _verify_adapter_proof(root,source,bound['slot'],model_record_path=proof['record_path'])
    if expected_proof is not None and expected_proof != proof:
        raise MaintenanceError('Protected model proof changed since SSD ownership began')
    return bound,proof,applied


class ModelEFI:
    """Fixed model EFI and companion intent; bridge owns POSIX journal writes."""
    def __init__(self, root, host_context, identity, selection, *, sibling_root=None):
        self.selection = dict(selection)
        self.sibling_root = Path(os.path.abspath(sibling_root)) if sibling_root is not None else None
        self.root = Path(os.path.abspath(root))
        self.context = host_context
        self.source = _model_source(identity,selection)
        self.store = Store(self.root, self.root/STATE)

    @staticmethod
    def _name(bound):
        return 'model-esp-' + bound['esp_partuuid'] + '.json'

    def _inspect(self, action):
        if action not in ('apply','verify','restore','recover'):
            raise MaintenanceError('Unknown model EFI operation')
        bound = boot.binding(self.root, installer_context=self.context)
        if os.path.lexists(self.store.state):
            with boot._directory(self.store.state) as descriptor:
                if os.name != 'nt' and os.fstat(descriptor).st_mode & 0o077:
                    raise MaintenanceError('Model EFI state directory must be private')
        if action in ('restore','recover'):
            _ssd_restore_barrier(self.root,bound,self.source)
        current = boot._target(self.root,bound)
        record = self.store._state_read(self._name(bound))
        pending = self.store._state_read('model-pending.json')
        if pending is not None:
            if action != 'recover' or not isinstance(pending,dict) or pending.get('target') != _model_target(bound):
                raise MaintenanceError('Interrupted model EFI transaction requires recovery on its original slot')
        elif action == 'recover':
            raise MaintenanceError('No interrupted model EFI transaction')
        if record is not None:
            original, applied = _model_record(record,bound)
            if record['source'] != self.source:
                raise MaintenanceError('Model EFI source or selection differs from first ownership')
            if pending is None:
                if current != (applied if record['status']=='installed' else original):
                    if not (action == 'verify' and record['status']=='installed'
                            and _ssd_projection(self.root,self.context,bound,record,current)):
                        raise MaintenanceError('Owned model EFI bytes changed; preserve them for review')
                if record['status']=='installed' and _companion_journal(self.root,bound['slot'],record['companions_original'],phase='installed') != record['journal_sha256']:
                    raise MaintenanceError('Split-lock companion journal differs from the completed model proof')
        elif action == 'apply':
            engine = Engine(self.root,read_only=True)
            pair=self.store._state_read('pair.json')
            legacy_present = any(os.path.lexists(self.root/path) for path in
                ('home/.oxp3-installer/integration','home/.boiler/oxp3-adapter'))
            if legacy_present:
                self._verify_fresh_current(bound,pair)
                if bound['slot'] == 'B':
                    self._verify_fresh_sibling(bound,pair)
                allowed_parent,seen_parent=self._verified_parent(engine,bound)
                boot._legacy_noninstaller_conflicts(self.root,engine,allowed_parent=allowed_parent)
                if not seen_parent():
                    raise MaintenanceError('Authenticated model parent journal is missing')
            else:
                boot._legacy_conflicts(self.root,engine)
            boot.reject_conflicting_component(engine,{'files':[{'target':boot.TARGET}]})
            _render(current)
        if action in ('verify','restore') and (record is None or record['status'] != 'installed'):
            raise MaintenanceError('No installed model EFI transaction')
        companions = _preflight_companions(self.root) if action == 'apply' and record is None else _companions(self.root)
        body = dict(kind='ReviewedModelEfiPlan',version=2,action=action,target=bound,source=self.source,
                    current_sha256=hashlib.sha256(current).hexdigest(),record_sha256=boot.digest(record),
                    pending_sha256=boot.digest(pending),companions_sha256=boot.digest(companions))
        if action == 'apply':
            preview = _render(current if record is None else boot._decode(record['original']))
            body['expected_efi'] = boot._encode(preview)
            body['expected_efi_sha256'] = hashlib.sha256(preview).hexdigest()
        body['review_sha256'] = boot.digest(body)
        if action == 'apply':
            pair=self.store._state_read('pair.json')
            if pair is not None:
                _check_pair(pair,self.source)
                slot=pair['slots'].get(bound['slot'])
                if (slot is None or slot['target'] != _model_target(bound)
                        or slot['model_plan_sha256'] != body['review_sha256']):
                    raise MaintenanceError('Model EFI plan differs from the fresh two-slot authorization')
        return body,current,record,pending,companions

    def _verified_parent(self,engine,bound):
        """Recognize only the Engine hook that entered the fixed trusted bridge."""
        identity=os.environ.get('BOILER_TRANSACTION_ID','')
        target=os.environ.get('BOILER_TARGET_ROOT','')
        if (not re.fullmatch(r'[0-9a-f]{32}',identity)
                or not target or os.path.abspath(target) != str(self.root)):
            raise MaintenanceError('Missing fixed model parent transaction binding')
        with boot._file(Path('/run/boiler-install-slot')) as (stream,info):
            if info.st_size > 8 or stream.read(9) not in (bound['slot'].encode(),(bound['slot']+'\n').encode()):
                raise MaintenanceError('Model parent transaction slot differs')
        path=engine.journals/(identity+'.json')
        with boot._file(path,private=True) as (stream,info):
            if info.st_size > 8*1024*1024:
                raise MaintenanceError('Model parent transaction journal is oversized')
            saved=boot._decode_json(stream.read(8*1024*1024+1))
        component=saved.get('component') if isinstance(saved,dict) else None
        if (not isinstance(saved,dict) or saved.get('id') != identity or saved.get('root') != engine.identity
                or saved.get('status') != 'hook_running' or not isinstance(component,dict)
                or component.get('id') != 'oxp3:legacy-adaptation' or component.get('files') != []
                or component.get('hooks') != {'apply':'scripts/apply.sh','verify':'scripts/verify.sh','restore':'scripts/restore.sh'}):
            raise MaintenanceError('Running Engine journal is not the fixed authenticated model component')
        seen=[False]
        def matches(candidate,value):
            if Path(candidate) == path and value == saved and not seen[0]:
                seen[0]=True
                return True
            return False
        return matches,lambda:seen[0]

    def _verify_fresh_current(self,bound,pair):
        """Accept only the authenticated bridge's fresh in-progress slot."""
        _check_pair(pair,self.source)
        label=bound['slot']
        if label not in ('A','B') or pair['slots'][label]['target'] != _model_target(bound):
            raise MaintenanceError('Current adapter slot differs from the protected fresh pair')
        adapter=self.root/'home/.boiler/oxp3-adapter'/(label+'.json')
        with boot._file(adapter,private=True) as (stream,info):
            if info.st_size > 2*1024*1024:
                raise MaintenanceError('Current adapter record is oversized')
            record=boot._decode_json(stream.read(2*1024*1024+1))
        if (not isinstance(record,dict)
                or set(record) != {'schema_version','phase','selection','previous_runtime_link'}
                or record['schema_version'] != 1 or record['phase'] != 'applying'
                or record['selection'] != self.selection or record['previous_runtime_link'] is not None
                or boot.digest(record['selection']) != self.source['selection_sha256']):
            raise MaintenanceError('Current adapter is not the reviewed fresh applying record')
        base=self.root/'home/.oxp3-installer/integration'/label
        with boot._directory(base):
            pass
        if any(os.path.lexists(base/name) for name in ('result.json','split-lock','gm7-resume')):
            raise MaintenanceError('Current slot already has a result or boot repair journal')
        expected=dict(schema=1,installer_language=self.selection['installer_language'],
                      kde_language=self.selection['kde_language'],
                      repairs=[item.removeprefix('oxp3:') for item in self.selection['repairs']],
                      plugins=[item.removeprefix('oxp3:') for item in self.selection['plugins']])
        with boot._file(base/'request.json',private=True) as (stream,info):
            if info.st_size > 65536 or boot._decode_json(stream.read(65537)) != expected:
                raise MaintenanceError('Current legacy request differs from the reviewed selection')
        with boot._file(base/'target-disk.json',private=True) as (stream,info):
            if info.st_size != 5 or stream.read(6) != b'null\n':
                raise MaintenanceError('Current legacy target record is not the fixed bridge input')
        allowed={item.removeprefix('oxp3:') for item in self.selection['repairs']+self.selection['plugins']}
        allowed.update(('shared-storage','system-update-hold','serial-console-policy','deck-access',
                        'decky-order','decky-order-light','kde-language','maintenance-tool',
                        'desktop-session','release-note','runtime'))
        fixed=(boot.TARGET,DROPIN,LICENSE,boot.DROPIN)
        for group in (base,self.root/'home/.oxp3-installer/integration/shared'):
            if not os.path.lexists(group):
                continue
            shared=group.name == 'shared'
            with boot._directory(group) as descriptor:
                with os.scandir(group if os.name == 'nt' else descriptor) as entries:
                    count=0
                    for entry in entries:
                        count+=1
                        if count > 20000 or entry.is_symlink():
                            raise MaintenanceError('Unsafe current legacy journal inventory')
                        if not entry.is_dir(follow_symlinks=False):
                            if (shared or not entry.is_file(follow_symlinks=False)
                                    or entry.name not in ('request.json','target-disk.json')):
                                raise MaintenanceError('Unknown current legacy staging state')
                            continue
                        if entry.name not in allowed or entry.name in ('split-lock','gm7-resume'):
                            raise MaintenanceError('Unknown current legacy component journal')
                        journal=group/entry.name/'state.json'
                        with boot._file(journal,private=True) as (stream,info):
                            if info.st_size > 128*1024*1024:
                                raise MaintenanceError('Current legacy journal exceeds limit')
                            value=boot._decode_json(stream.read(128*1024*1024+1))
                        paths=value.get('allowed') if isinstance(value,dict) else None
                        if (not isinstance(paths,list) or not paths or any(not isinstance(path,str) for path in paths)
                                or len(paths) != len(set(paths)) or value.get('phase') != 'installed'
                                or not isinstance(value.get('original'),dict) or not isinstance(value.get('applied'),dict)
                                or set(value['original']) != set(paths) or set(value['applied']) != set(paths)):
                            raise MaintenanceError('Current legacy component journal is invalid or incomplete')
                        for name in paths:
                            target=boot.relative_path(name).casefold()
                            if name.startswith('home/') != shared:
                                raise MaintenanceError('Current legacy journal has the wrong slot scope')
                            if any(target == item.casefold() or target.startswith(item.casefold()+'/')
                                   or item.casefold().startswith(target+'/') for item in fixed):
                                raise MaintenanceError('Current legacy component already owns a fixed boot target')

    def _verify_fresh_sibling(self,bound,pair):
        _check_pair(pair,self.source)
        if bound['slot'] != 'B' or self.sibling_root is None:
            raise MaintenanceError('Existing legacy ownership lacks the exact fresh sibling handoff')
        sibling=ModelEFI(self.sibling_root,self.context,self.source,self.selection)
        proof=sibling._proof_locked()
        expected=pair['slots']['A']
        if proof['target'] != expected['target'] or proof['applied_sha256'] != expected['model_efi_sha256']:
            raise MaintenanceError('Fresh sibling model proof differs from the two-slot authorization')
        if os.stat(self.root/'home').st_dev != os.stat(self.sibling_root/'home').st_dev:
            raise MaintenanceError('Fresh sibling does not use the same mounted shared home')
        _verify_adapter_proof(self.sibling_root,self.source,model_record_path=proof['record_path'])

    def plan(self, action):
        return self._inspect(action)[0]

    def _proof_locked(self):
        plan,current,record,_,_ = self._inspect('verify')
        original,applied = _model_record(record,plan['target'])
        return _make_proof(self.root,plan['target'],record)

    def proof(self):
        return self._proof_locked()

    def execute(self, reviewed, *, reviewed_sha256):
        if os.name != 'posix' or os.geteuid() != 0:
            raise MaintenanceError('Model EFI writes require Linux root')
        if (not isinstance(reviewed,dict) or reviewed.get('review_sha256') != reviewed_sha256
                or boot.digest({k:v for k,v in reviewed.items() if k != 'review_sha256'}) != reviewed_sha256):
            raise MaintenanceError('Model EFI review differs')
        with self.store.lock():
            return self._execute_locked(reviewed, reviewed_sha256=reviewed_sha256)

    def _execute_locked(self, reviewed, *, reviewed_sha256):
        fresh,current,record,pending,companions = self._inspect(reviewed.get('action'))
        if fresh != reviewed:
            raise MaintenanceError('Model EFI target or state changed; review again')
        bound, action = fresh['target'], fresh['action']
        if action == 'verify':
            return dict(ok=True,status='verified',changed=False)
        if action == 'recover':
            return self._recover(bound,current,record,pending)
        if action == 'restore':
            after = boot._decode(record['original'])
            originals = record['companions_original']
        else:
            original = current if record is None else boot._decode(record['original'])
            after = _render(original)
            if record is not None and record['status']=='installed' and current == after:
                return dict(ok=True,status='already_configured',changed=False)
            originals = companions if record is None else record['companions_original']
            if companions != originals:
                raise MaintenanceError('Split-lock companion originals changed before reapply')
        intent = dict(version=2,target=_model_target(bound),source=self.source,action=action,
                      before=boot._encode(current),after=boot._encode(after),old=record,
                      companions_original=originals)
        self.store._state_write('model-pending.json',intent)
        if boot.binding(self.root,installer_context=self.context) != bound:
            raise MaintenanceError('Model EFI topology changed before write')
        if current != after:
            boot._replace_efi(self.root,current,after,bound)
        if boot._target(self.root,bound) != after or boot.binding(self.root,installer_context=self.context) != bound:
            raise MaintenanceError('Model EFI bytes or topology changed during write')
        # The bridge must complete/rollback the GRUB-excluded POSIX journal;
        # neither its final result.json nor a caller assertion clears intent.
        return dict(ok=True,status='awaiting_companions',changed=current != after)

    def _intent(self,bound,pending):
        keys={'version','target','source','action','before','after','old','companions_original'}
        if (not isinstance(pending,dict) or set(pending) != keys or pending['version'] != 2
                or pending['target'] != _model_target(bound) or pending['source'] != self.source
                or pending['action'] not in ('apply','restore')
                or not isinstance(pending['companions_original'],dict)
                or set(pending['companions_original']) != set(COMPANIONS)):
            raise MaintenanceError('Invalid combined model EFI intent')
        before,after=boot._decode(pending['before']),boot._decode(pending['after'])
        old=pending['old']
        if old is not None:
            original,applied=_model_record(old,bound)
            if old['source'] != self.source or old['companions_original'] != pending['companions_original']:
                raise MaintenanceError('Combined intent changed the first originals or source')
            expected_before=applied if old['status']=='installed' else original
        else:
            original,applied=before,_render(before)
            expected_before=original
        expected_after=applied if pending['action']=='apply' else original
        if before != expected_before or after != expected_after or (old is None and pending['action'] != 'apply'):
            raise MaintenanceError('Combined model EFI intent has invalid byte lineage')
        return before,after,old,original,applied

    def finalize(self):
        if os.name != 'posix' or os.geteuid() != 0:
            raise MaintenanceError('Model EFI finalization requires Linux root')
        with self.store.lock():
            return self._finalize_locked()

    def _finalize_locked(self):
        bound=boot.binding(self.root,installer_context=self.context)
        pending=self.store._state_read('model-pending.json')
        before,after,old,original,applied=self._intent(bound,pending)
        saved=self.store._state_read(self._name(bound))
        if boot._target(self.root,bound) != after:
            raise MaintenanceError('Combined model EFI bytes changed before finalization')
        if pending['action']=='apply':
            sha=_companion_journal(self.root,bound['slot'],pending['companions_original'],phase='installed')
            new=dict(version=2,target=_model_target(bound),source=self.source,
                     original=boot._encode(original),applied=boot._encode(applied),status='installed',
                     companions_original=pending['companions_original'],journal_sha256=sha)
        else:
            _companion_journal(self.root,bound['slot'],pending['companions_original'],phase='rolled_back')
            new=dict(old,status='restored')
        if saved not in (old,new) or boot.binding(self.root,installer_context=self.context) != bound:
            raise MaintenanceError('Combined model EFI state or topology changed before finalization')
        self.store._state_write(self._name(bound),new)
        self._remove_pending()
        return dict(ok=True,status='configured' if pending['action']=='apply' else 'restored')

    def _remove_pending(self):
        (self.store.state/'model-pending.json').unlink()
        self.store._sync_state()

    def _recover(self,bound,current,record,pending):
        before,after,old,original,applied=self._intent(bound,pending)
        if record != old:
            found_original,found_applied=_model_record(record,bound)
            if (found_original,found_applied) != (original,applied) or record['source'] != self.source \
                    or record['companions_original'] != pending['companions_original'] \
                    or record['status'] != ('installed' if pending['action']=='apply' else 'restored'):
                raise MaintenanceError('Combined model EFI recovery record differs; preserve it')
        if current not in (before,after):
            raise MaintenanceError('Combined model EFI recovery bytes differ; preserve them')
        _ssd_restore_barrier(self.root,bound,self.source)
        _companion_journal(self.root,bound['slot'],pending['companions_original'],phase='rolled_back')
        # Both interrupted apply and interrupted restore end with first originals.
        if boot.binding(self.root,installer_context=self.context) != bound:
            raise MaintenanceError('Model EFI topology changed before recovery')
        if current != original:
            boot._replace_efi(self.root,current,original,bound)
        if boot._target(self.root,bound) != original or boot.binding(self.root,installer_context=self.context) != bound:
            raise MaintenanceError('Model EFI bytes or topology changed during recovery')
        if old is None:
            path=self.store.state/self._name(bound)
            if path.exists(): path.unlink(); self.store._sync_state()
        else:
            self.store._state_write(self._name(bound),dict(old,status='restored'))
        self._remove_pending()
        return dict(ok=True,status='recovered',changed=current != original)


def model_efi_plan(root, host_context, source_identity, selection, action='apply', *, sibling_root=None):
    return ModelEFI(root,host_context,source_identity,selection,sibling_root=sibling_root).plan(action)


def model_efi_apply(root, host_context, source_identity, selection, reviewed, *, reviewed_sha256, sibling_root=None):
    if reviewed.get('action') != 'apply':
        raise MaintenanceError('Expected reviewed model EFI apply plan')
    return ModelEFI(root,host_context,source_identity,selection,sibling_root=sibling_root).execute(reviewed,reviewed_sha256=reviewed_sha256)


def model_efi_proof(root, host_context, source_identity, selection):
    return ModelEFI(root,host_context,source_identity,selection).proof()


def model_efi_restore(root, host_context, source_identity, selection, reviewed, *, reviewed_sha256):
    if reviewed.get('action') != 'restore':
        raise MaintenanceError('Expected reviewed model EFI restore plan')
    return ModelEFI(root,host_context,source_identity,selection).execute(reviewed,reviewed_sha256=reviewed_sha256)


def model_efi_recover(root, host_context, source_identity, selection, reviewed, *, reviewed_sha256):
    if reviewed.get('action') != 'recover':
        raise MaintenanceError('Expected reviewed model EFI recovery plan')
    return ModelEFI(root,host_context,source_identity,selection).execute(reviewed,reviewed_sha256=reviewed_sha256)


def model_efi_finalize(root, host_context, source_identity, selection):
    return ModelEFI(root,host_context,source_identity,selection).finalize()
