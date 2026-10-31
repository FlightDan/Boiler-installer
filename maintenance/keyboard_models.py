"""Fixed keyboard lifecycle behind the installer and privileged maintenance CLI.

An authenticated package selects this host capability as data. No package code,
hooks, executable path, merge keys or state-directory override is accepted.
"""
from pathlib import Path
from protocol.manifest import load_json
from protocol.model_catalog import ROOT, bundled_model_package
from .engine import MaintenanceError
from . import models
from .keyboard_contract import IDENTITY, check
from .keyboard_target import binding
from .keyboard_runtime.service import Keyboard, digest, NOTICE, MUTABLE_NOTICE, VERSION


def _trust(identity, trust):
    models._privileged()
    if identity != 'device:x2-mini' or trust is not True:
        raise MaintenanceError('Keyboard requires the selected X2 package and explicit local-test session trust')


def _offered(runtime_root):
    item = bundled_model_package('device:x2-mini', trust_local_test=True, root=runtime_root)
    components = [row for row in item['package']['components'] if row['id'] == IDENTITY]
    if len(components) != 1:
        raise MaintenanceError('The authenticated package does not offer the fixed keyboard component')
    check(components[0])
    return dict(id=item['package']['id'],version=item['package']['version'],manifest_sha256=item['manifest_sha256'])


def options(root, identity, *, trust_local_test=False, runtime_root=ROOT, state_dir=None):
    _trust(identity, trust_local_test)
    if state_dir is not None:
        raise MaintenanceError('Keyboard uses its single canonical shared-home ledger')
    bound = binding(root)
    keyboard = Keyboard(root)
    result = keyboard.options()
    descriptor, reason = None, result.get('reason')
    try:
        descriptor = _offered(runtime_root)
    except (ValueError,RuntimeError,OSError) as error:
        reason = str(error)
    installed = bound['slot'] in result['installed_slots']
    if descriptor is None and installed and not result.get('lifecycle_reason'):
        # The trusted installed core owns this fixed capability already. An
        # unavailable cache cannot gate its existing lifecycle. This grants no
        # package hooks, targets or arguments and cannot authorize a new slot.
        descriptor = dict(id=IDENTITY,version=VERSION,authorization='retained-fixed-host-state')
        reason = result.get('reason')
    pending = None
    if result['pending']:
        source=result['pending_slot']
        valid=isinstance(source,str) and source.startswith('partuuid-') and not result.get('lifecycle_reason')
        can_recover=valid and source==bound['slot']
        if not valid:
            source=None
            message={'en':'The keyboard recovery journal is corrupt. Recovery is disabled; preserve the journal for inspection.',
                     'zh':'键盘恢复账本已损坏；已禁止恢复，请保留账本供检查。'}
        else:
            message={'en':'Recover this interrupted keyboard transaction on its original slot '+source+'.' if can_recover else 'This keyboard transaction belongs to original slot '+source+'. Return to that slot to recover; this slot cannot recover it.',
                     'zh':'请在原槽 '+source+' 恢复中断的键盘事务。' if can_recover else '键盘事务属于原槽 '+source+'；必须回到该槽恢复，当前槽不能代为恢复。'}
        pending=dict(source='keyboard-store',component=IDENTITY,slot=source,current_slot=bound['slot'],recover_available=can_recover,reason=message)
        reason=message

    available = descriptor is not None and result['available']
    return dict(shared_adaptations=True,dynamic_adapter='keyboard',model={'id':identity},
        executable=available or installed or pending is not None,install_available=available,
        capabilities=[dict(id=IDENTITY,name={'zh':'KDE 屏幕键盘配置','en':'KDE virtual keyboard configuration'},
            category='repairs',available=available,default=False,dependencies=[],reason=reason or '',
            notes=MUTABLE_NOTICE)],
        current=dict(repairs=[IDENTITY],plugins=[]) if installed else None,pending=pending,
        package=descriptor or {},scope='shared-home-keyboard-only',target=dict(root=str(root),**bound),
        warnings=[NOTICE,MUTABLE_NOTICE,{'en':'Only the fixed desktop entry presence is checked; activation is not validated.',
            'zh':'仅核对固定桌面入口文件存在；未验证其启动或实际键盘行为。'}])


def plan(root, identity, repairs=(), plugins=(), *, action='apply', trust_local_test=False, runtime_root=ROOT, state_dir=None):
    current = options(root,identity,trust_local_test=trust_local_test,runtime_root=runtime_root,state_dir=state_dir)
    if action not in ('apply','verify','restore','recover') or plugins:
        raise MaintenanceError('Unknown fixed keyboard action')
    if action == 'apply':
        if list(repairs) != [IDENTITY] or not current['install_available'] or current['pending'] is not None:
            raise MaintenanceError('Select only the available keyboard component; recover interruptions first')
    elif action == 'recover':
        if current['pending'] is None:
            raise MaintenanceError('No interrupted keyboard transaction')
        if not current['pending']['recover_available']:
            raise MaintenanceError(current['pending']['reason']['en'])
    elif current['current'] is None or current['pending'] is not None:
        raise MaintenanceError('No installed keyboard for this partition, or recovery is required')
    review = Keyboard(root).plan(action,current['target']['label'])
    body = dict(schema_version=1,kind='ReviewedKeyboardModelPlan',model_id=identity,action=action,
        dynamic_adapter='keyboard',scope=current['scope'],target=current['target'],
        package=current['package'] if action == 'apply' else {},keyboard_plan=review,
        selection=dict(repairs=[IDENTITY],plugins=[]),warnings=current['warnings'],
        summary=dict(file_count=1,added=[IDENTITY] if action=='apply' else [],removed=[IDENTITY] if action=='restore' else []))
    body['review_sha256'] = digest(body)
    return body


def execute(root, reviewed, *, reviewed_sha256, trust_local_test=False, runtime_root=ROOT, state_dir=None):
    reviewed = load_json(reviewed) if isinstance(reviewed,(str,Path)) else reviewed
    body = {k:v for k,v in reviewed.items() if k != 'review_sha256'}
    if reviewed.get('kind') != 'ReviewedKeyboardModelPlan' or reviewed.get('review_sha256') != reviewed_sha256 or digest(body) != reviewed_sha256:
        raise MaintenanceError('Keyboard model review digest does not match')
    fresh = plan(root,reviewed['model_id'],[IDENTITY] if reviewed['action']=='apply' else [],
        action=reviewed['action'],trust_local_test=trust_local_test,runtime_root=runtime_root,state_dir=state_dir)
    if reviewed != fresh:
        raise MaintenanceError('Keyboard target or state changed; review again')
    inner = reviewed['keyboard_plan']
    result = Keyboard(root).execute(inner,reviewed_sha256=inner['review_sha256'])
    verified = result['operation'].get('ok',True)
    return dict(status='verified' if verified else 'verification-failed',
        result=dict(verified=verified,components=[result]),restart_required=result['restart_required'])


def installer_plan(root, component, slot):
    check(component)
    return Keyboard(root).plan('apply',slot)


def installer_execute(root, component, slot, reviewed):
    check(component)
    if reviewed.get('action') != 'apply' or reviewed.get('target_binding',{}).get('label') != slot:
        raise MaintenanceError('Installer keyboard plan does not match the selected target slot')
    return Keyboard(root).execute(reviewed,reviewed_sha256=reviewed['review_sha256'])


def installer_verify(root, component, slot):
    check(component)
    result = Keyboard(root).verify_readonly(slot)
    if not result['ok']:
        raise MaintenanceError('Installed keyboard configuration changed or is absent')
    return result
