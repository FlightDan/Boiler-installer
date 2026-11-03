"""Explicit, reviewed model maintenance; guides never authorize execution.

Local test bundles remain a separate, session-authorized path. Normal signed
updates retain their existing trust rules. A model package owns its own durable
file generations; Engine supplies serialization and the live root write window.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

from protocol import compatible, validate_package
from protocol.manifest import confined, load_json
from protocol.model_catalog import ROOT, MODELS, bundled_model_package, load_models
from protocol.model_options import validated_selection
from .engine import Engine, MaintenanceError, _json_write, _sync_dir, build_hook_environment
from .updates import _authenticated_payload, _digest, target_environment
from .adoption_blobs import _directory, _file

CONTRACT = {'kind': 'boiler-model-maintenance-v1', 'entry': 'scripts/maintenance.sh'}
SCOPE = 'active-root-and-shared-home'


def _privileged():
    if os.name != 'posix' or os.geteuid() != 0:
        raise MaintenanceError('Model target inspection and maintenance require Linux root authorization')


def _private_json(path):
    with _file(path, private=True) as (stream, info):
        if info.st_size > 2*1024*1024:
            raise MaintenanceError('Model receipt exceeds size limit')
        from protocol.model_catalog import document
        return document(stream.read())


def pending(engine):
    path = engine.state_dir/'models/pending.json'
    if not os.path.lexists(path):
        return None
    value = _private_json(path)
    if (value.get('kind') != 'PendingModelAction' or value.get('model_id') not in MODELS
            or not re.fullmatch('[0-9a-f]{32}', value.get('id', ''))):
        raise MaintenanceError('Invalid pending model action')
    return value


def check_pending(engine, allowed=None):
    value = pending(engine)
    if value and value['id'] != allowed:
        raise MaintenanceError('Interrupted model maintenance requires explicit model verification/recovery: ' + value['id'])


def catalog(*, runtime_root=ROOT, online=True):
    return load_models(root=runtime_root, online=online, include_bundles=False)


def _package(identity, engine, runtime_root, *, trust_local_test):
    if not trust_local_test:
        raise MaintenanceError('Explicit session trust is required for the local test model package')
    if identity not in MODELS:
        raise MaintenanceError('Unknown model identity')
    pointer = engine.state_dir/'models'/('package-' + identity.removeprefix('device:') + '.json')
    if os.path.lexists(pointer):
        saved = _private_json(pointer)
        digest = saved.get('manifest_sha256', '')
        if saved.get('model_id') != identity or not re.fullmatch('[0-9a-f]{64}', digest):
            raise MaintenanceError('Invalid retained model package identity')
        directory = confined(engine.state_dir, 'models/packages/' + digest)
        package = validate_package(directory, expected_manifest_sha256=digest)
        item = dict(directory=str(directory), manifest_sha256=digest, package=package)
    else:
        item = bundled_model_package(identity, trust_local_test=True, root=runtime_root)
    package = item['package']
    if package.get('id') != identity or package.get('maintenance_contract') != CONTRACT:
        raise MaintenanceError('This package does not declare an on-device maintenance entry')
    if CONTRACT['entry'] not in {entry['path'] for entry in package['files']}:
        raise MaintenanceError('Model maintenance entry is outside its authenticated inventory')
    if identity == 'device:x2-mini' and package.get('x2_efi_contract'):
        from components.x2_efi_provider import verify
        verify(Path(item['directory']), Path(__file__).resolve().parents[1]/'components/x2_efi_provider.lock.json')
    if identity == 'device:oxp3' and package.get('oxp3_full_efi_contract'):
        from components.oxp3_full_efi_provider import verify
        verify(Path(item['directory']), Path(__file__).resolve().parents[1]/'components/oxp3_full_efi_provider.lock.json')
    return item


def _run(package_directory, action, root, request, transaction='0'*32):
    if action not in ('inspect', 'plan', 'apply', 'verify', 'restore'):
        raise MaintenanceError('Unsupported model action')
    with tempfile.TemporaryDirectory(prefix='boiler-model-request-') as temporary:
        request_path = Path(temporary)/'request.json'
        _json_write(request_path, request)
        result = subprocess.run(['/bin/bash', str(confined(package_directory, CONTRACT['entry'])),
                                 action, str(root), str(request_path)],
                                cwd=package_directory, env=build_hook_environment(root, transaction),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=3600, check=False)
    rows = [line[len('BOILER_MODEL_RESULT='):] for line in result.stdout.decode('utf-8').splitlines()
            if line.startswith('BOILER_MODEL_RESULT=')]
    if result.returncode or len(rows) != 1:
        error = result.stderr.decode('utf-8', errors='replace')[-3000:]
        if len(rows) == 1:
            try:
                error = str(json.loads(rows[0]).get('error', error))
            except (ValueError, TypeError):
                pass
        raise MaintenanceError('Model ' + action + ' failed; originals and receipts preserved. ' + error)
    from protocol.model_catalog import document
    value = document(rows[0].encode())
    if not isinstance(value, dict) or value.get('ok') is False:
        raise MaintenanceError('Model action returned an unsuccessful result')
    return value


def _review_model_companions(root, bound, originals):
    from . import model_efi as model, ssd_boot as boot
    journal_path=Path(root)/'home/.oxp3-installer/integration'/bound['slot']/'split-lock/state.json'
    observed=model._companions(root)
    if not os.path.lexists(journal_path):
        if observed != originals: raise MaintenanceError('Missing split-lock companion journal')
        return dict(journal_sha256=boot.digest(None),snapshot_sha256=boot.digest(observed))
    journal=_private_json(journal_path)
    allowed=list(model.COMPANIONS)
    if (journal.get('allowed') != allowed or journal.get('original') != originals
            or journal.get('phase') not in ('installed','installing','failed','partial_restore','rolled_back')
            or not isinstance(journal.get('applied'),dict) or not set(journal['applied']) <= set(allowed)
            or not isinstance(journal.get('accepted',{}),dict) or not set(journal.get('accepted',{})) <= set(allowed)
            or journal.get('metadata') != {name:{} for name in allowed}):
        raise MaintenanceError('Invalid split-lock companion journal')
    for name in allowed:
        original=originals[name]; actual=observed[name]
        applied=journal['applied'].get(name)
        accepted=journal.get('accepted',{}).get(name,[])
        if not isinstance(accepted,list): raise MaintenanceError('Invalid split-lock accepted snapshots')
        if applied is not None: model._expected_bytes(name,applied)
        if any(value not in (applied, original.get('data')) for value in accepted):
            raise MaintenanceError('Unknown split-lock accepted bytes')
        if actual == original: continue
        if (not actual.get('exists') or applied is None or actual.get('data') != applied):
            raise MaintenanceError('Split-lock companion differs from known original or applied bytes')
        if original['exists']:
            expected={key:original[key] for key in ('mode','uid','gid')}
        else:
            parent=(Path(root)/name).parent.stat()
            expected=dict(mode=0o644,uid=0,gid=parent.st_gid if parent.st_mode & 0o2000 else 0)
        if any(actual[key] != value for key,value in expected.items()):
            raise MaintenanceError('Split-lock companion metadata changed')
    return dict(journal_sha256=boot.digest(journal),snapshot_sha256=boot.digest(observed))


def _retained_model_efi(root, item, action):
    """Review only an authenticated, already-owned fixed OXP3 EFI generation."""
    identity=item['package'].get('id')
    contracts={'device:oxp3': ('oxp3_efi_contract','oxp3-split-lock-efi-v1'),
               'device:x2-mini': ('x2_efi_contract','x2-split-lock-efi-v1')}
    key,kind=contracts.get(identity,(None,None))
    if identity=='device:oxp3' and item['package'].get('oxp3_full_efi_contract') is not None:
        key,kind='oxp3_full_efi_contract','oxp3-full-split-lock-efi-v1'
    if item['package'].get(key) != {'kind':kind,'component':'oxp3:legacy-adaptation'}:
        if os.path.lexists(Path(root)/'home/.boiler/boot-ownership-v1'):
            raise MaintenanceError('Existing fixed model EFI ownership requires its authenticated provider')
        return None
    if key == 'oxp3_full_efi_contract':
        from components.oxp3_full_efi_provider import verify
        lock_name='oxp3_full_efi_provider.lock.json'
    elif identity == 'device:oxp3':
        from components.oxp3_efi_provider import verify
        lock_name='oxp3_efi_provider.lock.json'
    else:
        from components.x2_efi_provider import verify
        lock_name='x2_efi_provider.lock.json'
    from . import model_efi as model, ssd_boot as boot
    from .keyboard_runtime.vendor.transactions import Store
    package=Path(item['directory'])
    verify(package, Path(__file__).resolve().parents[1]/'components'/lock_name)
    root=Path(root).absolute(); state=root/model.STATE
    if not os.path.lexists(state): return None
    bound=boot.binding(root,installer_context=None)
    store=Store(root,state)
    pair=store._state_read('pair.json')
    record=store._state_read('model-esp-'+bound['esp_partuuid']+'.json')
    intent=store._state_read('model-pending.json')
    if record is None and intent is None:
        raise MaintenanceError('Protected model EFI ownership has no current slot record')
    adapter=_private_json(root/'home/.boiler/oxp3-adapter'/(bound['slot']+'.json'))
    selection=adapter.get('selection')
    if not isinstance(selection,dict): raise MaintenanceError('Missing protected model selection')
    checked=validated_selection(item['package'],selection.get('repairs'),selection.get('plugins'),
        installer_language=selection.get('installer_language'),kde_language=selection.get('kde_language'),
        acknowledge_update_hold=selection.get('acknowledge_legacy_update_hold') is True)
    if (selection.get('slot') != 'guest' or set(selection) != set(checked)|{'slot'}
            or any(set(checked[k]) != set(selection[k]) for k in ('repairs','plugins'))
            or any(checked[k] != selection[k] for k in checked if k not in ('repairs','plugins'))):
        raise MaintenanceError('Protected model selection differs from authenticated capabilities')
    source=dict(package_id=identity,manifest_sha256=item['manifest_sha256'],selection_sha256=boot.digest(selection))
    if identity == 'device:oxp3': model._source(source,selection)
    else: model._x2_source(source,selection)
    model._check_pair(pair,source)
    if pair['slots'][bound['slot']]['target'] != model._model_target(bound):
        raise MaintenanceError('Actual target differs from protected model pair')
    if record is not None:
        if identity == 'device:oxp3': model._record(record,bound)
        else: model._x2_record(record,bound)
        if record['source'] != source: raise MaintenanceError('Protected model source differs')
    if intent is not None:
        pending_before,pending_after,_,_,_=model.ModelEFI(root,None,source,selection)._intent(bound,intent)
        if boot._target(root,bound) not in (pending_before,pending_after):
            raise MaintenanceError('Interrupted model EFI bytes differ from protected intent')
    if intent is not None and action != 'restore':
        raise MaintenanceError('Interrupted model EFI operation requires Restore')
    if record is not None and record['status']=='restored' and intent is None:
        return dict(status='restored',source=source,selection=selection,target=model._model_target(bound))
    if adapter.get('phase') != 'applied' and intent is None:
        raise MaintenanceError('Model adapter generation is incomplete')
    obj=model.ModelEFI(root,None,source,selection)
    operation='recover' if intent is not None else action
    review=obj.plan(operation)
    if intent is None: model._verify_adapter_proof(root,source,bound['slot'],model_record_path=model.STATE+'/model-esp-'+bound['esp_partuuid']+'.json')
    originals=(intent if intent is not None else record)['companions_original']
    companions=_review_model_companions(root,bound,originals)
    result=dict(status='installed',source=source,selection=selection,target=model._model_target(bound),review=review,
                companions=companions)
    if key == 'oxp3_full_efi_contract': result['provider']=key
    return result


def _rollback_model_companions(root, package, bound, originals, identity, provider=None):
    """Invoke only the authenticated public transaction on the two POSIX targets."""
    import base64
    import importlib.util
    from . import model_efi as model, ssd_boot as boot
    if identity == 'device:oxp3' and provider == 'oxp3_full_efi_contract':
        from components.oxp3_full_efi_provider import verify
        lock_name='oxp3_full_efi_provider.lock.json'
    elif identity == 'device:oxp3':
        from components.oxp3_efi_provider import verify
        lock_name='oxp3_efi_provider.lock.json'
    elif identity == 'device:x2-mini':
        from components.x2_efi_provider import verify
        lock_name='x2_efi_provider.lock.json'
    else: raise MaintenanceError('Unknown fixed model EFI provider')
    verify(package,Path(__file__).resolve().parents[1]/'components'/lock_name)
    module_path=Path(package)/'payload/legacy/components/file_transaction.py'
    spec=importlib.util.spec_from_file_location('_boiler_fixed_splitlock_files',module_path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    directory=Path(root)/'home/.oxp3-installer/integration'/bound['slot']/'split-lock'
    journal=directory/'state.json'
    if not os.path.lexists(journal):
        if model._companions(root) != originals: raise MaintenanceError('Missing split-lock companion journal')
        return
    value=_private_json(journal)
    allowed=list(model.COMPANIONS)
    if (value.get('allowed') != allowed or value.get('original') != originals
            or value.get('metadata') != {name:{} for name in allowed}
            or value.get('phase') not in ('installed','installing','failed','partial_restore','rolled_back')):
        raise MaintenanceError('Split-lock companion journal differs from fixed targets')
    class FixedFiles(module.FileTransaction):
        def __init__(self):
            self.root=Path(root).absolute(); self.files=value['metadata']; self.owner=(0,0)
            super().__init__(directory,[self.root/path for path in allowed])
        def snapshot(self,path): return module.snapshot(path,maximum=64*1024*1024)
        def installed_metadata(self,path,original):
            if original['exists']: return super().installed_metadata(path,original)
            metadata=self.files[str(Path(path).relative_to(self.root))]
            uid,gid=self.owner if metadata.get('user') else (0,0)
            return {'mode':metadata.get('mode',0o644),'uid':uid,'gid':gid}
        def load(self):
            saved=module.snapshot(self.state,maximum=256*1024*1024)
            if saved['uid'] != 0 or saved['mode'] & 0o077: raise RuntimeError('Untrusted repair journal')
            result=json.loads(base64.b64decode(saved['data']))
            if result['allowed'] != allowed or result.get('metadata') != self.files: raise RuntimeError('Repair targets changed')
            result['allowed']=list(self.allowed)
            for name in ('original','applied','accepted'):
                result[name]={str(self.root/path):v for path,v in result.get(name,{}).items()}
            return result
        def save(self,result):
            stored=dict(result,allowed=[str(Path(path).relative_to(self.root)) for path in result['allowed']],
                        metadata={path:{k:v for k,v in item.items() if k in ('user','mode')} for path,item in self.files.items()})
            for name in ('original','applied','accepted'):
                stored[name]={str(Path(path).relative_to(self.root)):v for path,v in result.get(name,{}).items()}
            module.atomic_write(self.state,(json.dumps(stored)+'\n').encode(),0o600)
    operation=FixedFiles()
    errors=operation.rollback()
    if errors: raise MaintenanceError('Split-lock companion recovery preserved conflicts: '+str(errors))
    model._companion_journal(root,bound['slot'],originals,phase='rolled_back')


def _ordinary_model_action(root, target):
    from .keyboard_runtime.vendor.transactions import Store
    root=Path(root).absolute(); store=Store(root,root/'home/.boiler/model-maintenance/oxp3')
    index=store._index(); pending=store._state_read('pending.json')
    slot=target.get('id') if isinstance(target,dict) else None
    if not isinstance(slot,str) or not slot: raise MaintenanceError('Missing actual model slot identity')
    current=index['slots'].get(slot,{}).get('current') or {}
    paths=list(current.get('paths',{}))
    if pending is not None:
        if pending.get('slot') != slot or not isinstance(pending.get('operations'),list):
            raise MaintenanceError('Ordinary model pending state belongs to another slot')
        paths.extend(op.get('path') for op in pending['operations'] if isinstance(op,dict))
    from . import model_efi as model, ssd_boot as boot
    for path in paths:
        target=boot.relative_path(path).casefold()
        for fixed in (boot.TARGET,model.DROPIN,model.LICENSE):
            fixed=fixed.casefold()
            if target == fixed or target.startswith(fixed+'/') or fixed.startswith(target+'/'):
                raise MaintenanceError('Ordinary model generation overlaps fixed model EFI ownership')
    return bool(current.get('paths') or pending is not None)


def _execute_retained_model_efi(root, package, review, ordinary_action=None):
    """Use the Engine owner once; do not hold the boot lock across a subprocess."""
    import contextlib
    from . import model_efi as model, ssd_boot as boot
    from .keyboard_runtime.vendor.transactions import Store
    if review['status'] == 'restored':
        ordinary=ordinary_action() if ordinary_action else None
        return dict(ok=True,verified=True,status='not-installed',changed=False,ordinary=ordinary)
    obj=model.ModelEFI(root,None,review['source'],review['selection'])
    ssd=Store(Path(root).absolute(),Path(root).absolute()/boot.STATE)
    def ssd_lock(): return ssd.lock() if os.path.lexists(ssd.state) else contextlib.nullcontext()
    with obj.store.lock():
        with ssd_lock():
            bound=boot.binding(root,installer_context=None)
            operation=review['review']['action']
            if operation in ('restore','recover'): model._ssd_restore_barrier(root,bound,review['source'])
            fresh=obj.plan(operation)
            if fresh != review['review']: raise MaintenanceError('Model EFI target or ownership changed while acquiring locks')
            pending=obj.store._state_read('model-pending.json'); record=obj.store._state_read(obj._name(bound))
            originals=(pending if pending is not None else record)['companions_original']
            if _review_model_companions(root,bound,originals) != review['companions']:
                raise MaintenanceError('Split-lock companion state changed after review')
            if operation == 'verify':
                obj._proof_locked(); changed=False
            elif operation == 'restore':
                result=obj._execute_locked(fresh,reviewed_sha256=fresh['review_sha256'])
                changed=result['changed']
            elif operation == 'recover':
                # Validate before invoking any ordinary recovery; intent remains durable.
                before,after,_,_,_=obj._intent(bound,pending)
                if boot._target(root,bound) not in (before,after):
                    raise MaintenanceError('Interrupted EFI bytes differ from protected intent')
                changed=False
            else: raise MaintenanceError('Unexpected fixed model EFI action')
    ordinary=ordinary_action() if ordinary_action else None
    if operation == 'verify': return dict(ok=True,verified=True,status='verified',changed=False,ordinary=ordinary)
    with obj.store.lock():
        with ssd_lock():
            rebound=boot.binding(root,installer_context=None)
            if rebound != bound: raise MaintenanceError('Model EFI topology changed during ordinary maintenance')
            model._ssd_restore_barrier(root,bound,review['source'])
            pending=obj.store._state_read('model-pending.json')
            before,after,_,_,_=obj._intent(bound,pending)
            if boot._target(root,bound) not in (before,after):
                raise MaintenanceError('Interrupted EFI bytes differ from protected intent')
            if _review_model_companions(root,bound,originals) != review['companions']:
                raise MaintenanceError('Split-lock companion state changed during ordinary maintenance')
            model._ssd_restore_barrier(root,bound,review['source'])
            _rollback_model_companions(root,package,bound,originals,review['source']['package_id'],review.get('provider'))
            if operation == 'restore':
                obj._finalize_locked()
            else:
                fresh=obj.plan('recover')
                result=obj._execute_locked(fresh,reviewed_sha256=fresh['review_sha256'])
                changed=result['changed']
    return dict(ok=True,verified=True,status='restored',changed=changed,ordinary=ordinary,
                recovered=operation == 'recover')


def options(root, identity, *, trust_local_test=False, runtime_root=ROOT, state_dir=None, online=False):
    _privileged()
    data = catalog(runtime_root=runtime_root, online=online)
    model = next((item for item in data['models'] if item['id'] == identity), None)
    if model is None:
        raise MaintenanceError('Unknown model identity')
    view = dict(model=model, capabilities=[], current=None, executable=False,
                reasons=[], source=data['source'], offline=data['offline'], scope=SCOPE)
    if trust_local_test is not True:
        view['reasons'] = ['Select and explicitly trust a model package to inspect executable maintenance.']
        return view
    engine = Engine(root, state_dir, read_only=True)
    item = _package(identity, engine, runtime_root, trust_local_test=trust_local_test)
    environment = target_environment(root, device_id=identity.removeprefix('device:'))
    if not compatible(item['package'], environment):
        view['reasons'] = ['The actual OS, architecture or kernel is incompatible with this model package.']
        view['capabilities'] = [dict(row, available=False, reason=view['reasons'][0]) for row in item['package']['legacy_capabilities']]
        return view
    # An interrupted fixed EFI intent can precede the legacy final result.
    # Its protected state is sufficient for a recovery-only view; never adopt
    # or report the remaining legacy selection as Boiler-owned.
    fixed_pending = identity in ('device:oxp3','device:x2-mini') and os.path.lexists(Path(root)/'home/.boiler/boot-ownership-v1/model-pending.json')
    if fixed_pending:
        retained=_retained_model_efi(root,item,'restore')
        target=retained['target']
        report=dict(executable=True,current={'repairs':['oxp3:split-lock'],'plugins':[]},
                    current_source='boiler-model-efi',capabilities=[],reasons=[],
                    target=dict(root=str(Path(root).resolve()),id='partuuid-'+target['root_partuuid'].lower(),
                                label=target['slot'],partuuid=target['root_partuuid']))
    else:
        report = _run(item['directory'], 'inspect', Path(root), dict(schema_version=1, scope=SCOPE))
        if identity in ('device:oxp3','device:x2-mini') and (item['package'].get('oxp3_efi_contract') or item['package'].get('oxp3_full_efi_contract') or item['package'].get('x2_efi_contract')):
            retained=_retained_model_efi(root,item,'verify')
            if retained and retained['status'] == 'installed' and report.get('current_source') != 'boiler-model':
                report=dict(report,executable=True,current={'repairs':['oxp3:split-lock'],'plugins':[]},
                            current_source='boiler-model-efi')
    view.update(report)
    view.update(model=model, package=dict(id=identity, version=item['package']['version'],
                manifest_sha256=item['manifest_sha256'], trust='local-test'), environment=environment,
                pending=pending(engine) or (dict(kind='PendingModelEfi') if fixed_pending else None), scope=SCOPE)
    if 'capabilities' not in report or 'target' not in report:
        raise MaintenanceError('Model inspection omitted its target or capabilities')
    return view


def plan_model(root, identity, repairs, plugins, *, action='apply', trust_local_test=False,
               acknowledge_update_hold=False, installer_language='en-US', kde_language='system',
               runtime_root=ROOT, state_dir=None):
    if action not in ('apply', 'verify', 'restore'):
        raise MaintenanceError('Unsupported model plan action')
    _privileged()
    if action == 'apply':
        # Consent, choice whitelist and dependencies are data-only gates. Do
        # not invoke even the model's inspection code before these pass.
        initial = _package(identity, Engine(root, state_dir, read_only=True), runtime_root,
                           trust_local_test=trust_local_test)
        validated_selection(initial['package'], repairs, plugins, installer_language=installer_language,
                            kde_language=kde_language, acknowledge_update_hold=acknowledge_update_hold)
    current = options(root, identity, trust_local_test=trust_local_test, runtime_root=runtime_root, state_dir=state_dir)
    engine = Engine(root, state_dir, read_only=True)
    item = _package(identity, engine, runtime_root, trust_local_test=trust_local_test)
    retained = _retained_model_efi(root,item,action) if identity in ('device:oxp3','device:x2-mini') and action in ('verify','restore') else None
    if not current.get('executable') and retained is None:
        raise MaintenanceError('; '.join(current.get('reasons', [])) or 'No executable model maintenance is available')
    if current.get('pending') and action != 'restore':
        raise MaintenanceError('Interrupted model action requires an explicit Restore plan')
    choice = validated_selection(item['package'], repairs, plugins, installer_language=installer_language,
                                 kde_language=kde_language, acknowledge_update_hold=acknowledge_update_hold) if action == 'apply' else current.get('current')
    if action == 'apply':
        available = {row['id']: row for row in current['capabilities']}
        for identity_option in choice['repairs'] + choice['plugins']:
            row = available.get(identity_option, {})
            if row.get('available') is not True:
                raise MaintenanceError('Model option is unavailable on this target: ' + identity_option + ': ' + str(row.get('reason', '')))
    request = dict(schema_version=1, action=action, selection=choice, scope=SCOPE,
                   recovery_requested=current.get('pending') is not None)
    ordinary = _ordinary_model_action(root,current['target']) if retained is not None else True
    backend = _run(item['directory'], 'plan', Path(root), request) if ordinary else dict(kind='NoOrdinaryModelTransaction',executable=True)
    if retained is not None:
        backend = dict(kind='RetainedModelEfiAction',executable=backend.get('executable') is True,
                       ordinary=backend if ordinary else None, model_efi=retained)
    if backend.get('executable') is not True:
        if backend.get('replacement_requires_restore'):
            raise MaintenanceError('replacement_requires_restore: Restore the installed adaptations before installing a different selection')
        raise MaintenanceError('Model plan is blocked: ' + str(backend.get('reason', backend.get('reasons', ''))))
    info = Path(root).resolve().stat()
    body = dict(schema_version=1, kind='ReviewedModelPlan', model_id=identity, action=action, scope=SCOPE,
                selection=choice, package=current['package'], target=current['target'], environment=current['environment'],
                root=dict(path=str(Path(root).resolve()), device=info.st_dev, inode=info.st_ino),
                state_sha256=_digest(engine._state()), pending=current.get('pending'),
                backend=backend, summary=backend.get('summary', {}), warnings=backend.get('warnings', []))
    body['review_sha256'] = _digest(body)
    return body


def _retain(engine, item):
    parent = engine.state_dir/'models/packages'
    with _directory(parent, create=True):
        pass
    destination = parent/item['manifest_sha256']
    if not destination.exists():
        with _authenticated_payload(item['directory'], item['manifest_sha256'], parent) as snapshot:
            snapshot.rename(destination)
            # TemporaryDirectory removes only the original name after the rename.
    validate_package(destination, expected_manifest_sha256=item['manifest_sha256'])
    _json_write(engine.state_dir/'models'/('package-' + item['package']['id'].removeprefix('device:') + '.json'),
                dict(kind='RetainedModelPackage', model_id=item['package']['id'], manifest_sha256=item['manifest_sha256']))
    return destination


@contextlib.contextmanager
def _retained_legacy_guard(engine):
    """Serialize the fixed EFI lifecycle with the existing legacy maintainer."""
    from .adoption_blobs import _legacy_lock
    legacy=engine.root/'home/.oxp3-maintenance'
    present=os.path.lexists(legacy)
    if present and not os.path.lexists(legacy/'lock'):
        raise MaintenanceError('Existing legacy maintenance has no lock')
    with _legacy_lock(engine,blocking=False) if present else contextlib.nullcontext():
        yield


def execute_model(root, plan, *, reviewed_sha256, trust_local_test=False, runtime_root=ROOT, state_dir=None):
    _privileged()
    if not trust_local_test:
        raise MaintenanceError('Explicit session trust is required for local test execution')
    plan = load_json(plan) if isinstance(plan, (str, Path)) else plan
    if not isinstance(plan, dict):
        raise MaintenanceError('A reviewed model plan is required')
    body = {key: value for key, value in plan.items() if key != 'review_sha256'}
    if (not re.fullmatch('[0-9a-f]{64}', reviewed_sha256 or '') or plan.get('review_sha256') != reviewed_sha256
            or _digest(body) != reviewed_sha256 or plan.get('kind') != 'ReviewedModelPlan'):
        raise MaintenanceError('The reviewed model plan hash does not match')
    choice = plan.get('selection') or {}
    fresh = plan_model(root, plan['model_id'], choice.get('repairs', []), choice.get('plugins', []),
                       action=plan['action'], trust_local_test=True, runtime_root=runtime_root, state_dir=state_dir,
                       acknowledge_update_hold=choice.get('acknowledge_legacy_update_hold') is True,
                       installer_language=choice.get('installer_language', 'en-US'), kde_language=choice.get('kde_language', 'system'))
    if fresh != plan:
        raise MaintenanceError('Target, selected options or installed state changed after review; review again')
    engine = Engine(root, state_dir)
    previous = pending(engine)
    # An explicitly reviewed Restore is also the model UI's interrupted
    # protection-window recovery route; the existing identity checks apply.
    with engine._lock(live_recover=plan['action']=='restore'):
        retained_guard=plan['backend'].get('kind') == 'RetainedModelEfiAction'
        with _retained_legacy_guard(engine) if retained_guard else contextlib.nullcontext():
            engine._recover(allow_model_action=previous['id'] if previous else None)
            if _digest(engine._state()) != plan['state_sha256'] or pending(engine) != previous:
                raise MaintenanceError('Maintenance state changed while waiting for the model lock')
            item = _package(plan['model_id'], engine, runtime_root, trust_local_test=True)
            payload = _retain(engine, item)
            # Recheck the model's own journal/target fingerprint under the write lock.
            request = dict(schema_version=1, action=plan['action'], selection=plan['selection'], scope=SCOPE,
                           recovery_requested=plan['pending'] is not None)
            retained=plan['backend'].get('kind') == 'RetainedModelEfiAction'
            if retained:
                refreshed=_retained_model_efi(root,dict(item,directory=str(payload)),plan['action'])
                if refreshed != plan['backend']['model_efi']:
                    raise MaintenanceError('Model EFI ownership changed after review')
                ordinary=plan['backend']['ordinary']
                if ordinary is not None and _run(payload,'plan',Path(root),request) != ordinary:
                    raise MaintenanceError('Ordinary model files changed after review')
            elif _run(payload, 'plan', Path(root), request) != plan['backend']:
                raise MaintenanceError('Model files or target changed after review; review again')
            identity = uuid.uuid4().hex
            directory = engine.state_dir/'models/actions'
            with _directory(directory, create=True):
                pass
            receipt_path = directory/(identity + '.json')
            receipt = dict(schema_version=1, kind='ModelActionReceipt', id=identity, model_id=plan['model_id'],
                           action=plan['action'], package=plan['package'], plan_sha256=reviewed_sha256,
                           status='prepared', scope=SCOPE, previous_pending=previous)
            _json_write(receipt_path, receipt)
            marker = engine.state_dir/'models/pending.json'
            _json_write(marker, dict(kind='PendingModelAction', id=identity, model_id=plan['model_id']))
            try:
                if retained:
                    ordinary=plan['backend']['ordinary']
                    def run_ordinary():
                        request['expected_plan_sha256']=ordinary.get('plan_sha256')
                        ordinary_result=_run(payload,plan['action'],Path(root),request,identity)
                        if ordinary_result.get('ok') is not True or plan['action']=='verify' and ordinary_result.get('verified') is not True:
                            raise MaintenanceError('Ordinary model operation did not verify')
                        return ordinary_result
                    model_result=_execute_retained_model_efi(Path(root),payload,plan['backend']['model_efi'],
                                                             run_ordinary if ordinary is not None else None)
                    result=dict(ok=True,verified=model_result.get('verified') is True,status=model_result['status'],
                                model_efi=model_result,ordinary=model_result.get('ordinary'))
                else:
                    request['expected_plan_sha256'] = plan['backend'].get('plan_sha256')
                    result = _run(payload, plan['action'], Path(root), request, identity)
                if result.get('ok') is not True:
                    raise MaintenanceError('Model action did not report verified success')
                status = 'verification-failed' if plan['action']=='verify' and result.get('verified') is not True else 'verified'
                receipt.update(status=status, result=result)
                _json_write(receipt_path, receipt)
                marker.unlink(); _sync_dir(marker.parent)
                return dict(receipt, receipt_path=str(receipt_path),
                            restart_required=plan['action'] != 'verify' and result.get('status') not in ('not-managed','not-installed'))
            except BaseException as error:
                receipt.update(status='failed-preserved', error=str(error))
                _json_write(receipt_path, receipt)
                raise MaintenanceError('Model operation failed; original evidence and recovery receipt preserved at ' + str(receipt_path)) from error
