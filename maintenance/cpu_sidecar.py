"""Fixed service-side CPU ledger entry points; only the real Linux root is accepted."""
import hashlib
import json
import os
from pathlib import Path
import re
from maintenance import cpu_live_probe, cpu_sidecar_ledger, cpu_sidecar_store
from maintenance.adoption_blobs import _file
from maintenance.cpu_live_resources import fixed_component
from maintenance.engine import Engine
from maintenance.resource_firstboot import checked_intent, read_private
from protocol.cpu_contract import IDENTITY
from protocol.cpu_live_contract import validate_contract

class SidecarError(RuntimeError): pass
ROOT=Path('/')
GATE=ROOT/'var/lib/boiler-resource-approved/intel-g3e-public-cpu-controls.json'
PUBLIC_STATE=ROOT/'run/oxp3-cpu/state.json'


def _current_root(*, require_gate=True, verify_dependencies=True):
    if os.name!='posix' or os.geteuid()!=0:raise SidecarError('CPU sidecar requires Linux root')
    from maintenance.live_root import RootCommands
    intent=checked_intent(ROOT,identity=RootCommands().identity()['partuuid'])
    if IDENTITY not in intent['selection'].get('components',[]):raise SidecarError('CPU is not selected')
    if require_gate and read_private(GATE)!={'generation':intent['generation'],'identity':IDENTITY}:
        raise SidecarError('CPU approval gate differs')
    engine=Engine(ROOT,read_only=True)
    required=('core:maintenance','core:resource-firstboot',IDENTITY)
    if verify_dependencies:required+=('resource:common-intel-rapl','resource:steamos-manager')
    for identity in required:
        if not engine.verify_readonly(identity)['ok']:raise SidecarError('Installed transaction failed verification: '+identity)
    active=engine._state()['components'].get(IDENTITY)
    if not active:raise SidecarError('CPU transaction is not installed')
    from maintenance.resource_firstboot import read_private as read_journal
    journal=read_journal(engine._journal(active['transaction']))
    fixed_component(journal['component'])
    from maintenance.resource_firstboot import open_pool
    with open_pool(ROOT,intent) as (pool,references):
        validate_contract(pool.resource(IDENTITY))
    return intent,active['transaction']


def _dependencies():
    from maintenance.resource_activation import LiveSystem
    system=LiveSystem(ROOT)
    limits=[]
    for directory in ('intel-rapl:0','intel-rapl-mmio:0'):
        path=ROOT/'sys/class/powercap'/directory
        for name,want in (('name','package-0'),('enabled','1'),('constraint_0_name','long_term'),('constraint_1_name','short_term')):
            if (path/name).read_text().strip()!=want:raise SidecarError('RAPL interface identity differs')
        pair=[]
        for name in ('constraint_0_power_limit_uw','constraint_1_power_limit_uw'):
            value=(path/name).read_text().strip()
            if not value.isdecimal() or int(value)<=0 or int(value)%1000000:raise SidecarError('RAPL limit unavailable')
            pair.append(int(value)//1000000)
        limits.append(pair)
    for unit in ('boiler-intel-rapl.service','steamos-manager.service'):
        if system.service(unit)!='active' or not re.fullmatch('[0-9a-f]{32}',system.invocation(unit) or ''):
            raise SidecarError('RAPL dependency service is not active: '+unit)
    script=('import dbus,json; from gi.repository import GLib; '
            "b=dbus.SystemBus(); o=b.get_object('org.onexplayer.SteamOSManager.PowerControl','/org/onexplayer/SteamOSManager/PowerControl'); "
            "r=dbus.Interface(o,'org.onexplayer.SteamOSManager.PowerLimits1').GetLimits(); "
            "print(json.dumps({'pl1_values':[int(x) for x in r['pl1_values']], 'pl2_values':[int(x) for x in r['pl2_values']]}))")
    rc,output=system.run('python3','-I','-B','-c',script,check=False)
    if rc:raise SidecarError('RAPL D-Bus GetLimits or Python runtime unavailable')
    try:observed=json.loads(output)
    except ValueError as exc:raise SidecarError('Invalid RAPL D-Bus readback') from exc
    if observed!={'pl1_values':[p[0] for p in limits],'pl2_values':[p[1] for p in limits]}:
        raise SidecarError('RAPL D-Bus limits differ from the fixed domains')
    if not (ROOT/'var/lib/boiler/decky/runtime/services/PluginLoader').is_file() or not (ROOT/'etc/systemd/system/plugin_loader.service').is_file():
        raise SidecarError('Decky runtime unavailable')
    return system


def _observations(probe):
    result={}
    for row in probe['policies']:
        base='/sys/devices/system/cpu/cpufreq/'+row['policy']+'/'
        result[base+'scaling_max_freq']=str(row['current'])
        result[base+'energy_performance_preference']=row['epp']
    return result


def _public_state():
    try:
        with _file(PUBLIC_STATE,private=True) as (stream,info):
            if info.st_size>65536:raise SidecarError('Public CPU runtime state exceeds limit')
            return json.loads(stream.read(65537).decode('utf-8'))
    except FileNotFoundError:return None


def _context(*, dependencies=True):
    intent,transaction=_current_root(verify_dependencies=dependencies)
    if dependencies: system=_dependencies()
    else:
        from maintenance.resource_activation import LiveSystem
        system=LiveSystem(ROOT)
    probe=cpu_live_probe.probe() if dependencies else cpu_live_probe.probe_observe()
    invocation=os.environ.get('INVOCATION_ID','')
    if not re.fullmatch('[0-9a-f]{32}',invocation):raise SidecarError('Missing systemd invocation identity')
    return intent,transaction,system.boot_id(),invocation,probe


def prestart():
    intent,transaction,boot,invocation,probe=_context()
    engine=Engine(ROOT,read_only=True)
    def validate_handover(old,attempt):
        from maintenance import retry_documents
        from maintenance.resource_firstboot import STATE
        state=read_private(ROOT/STATE)
        row=state.get('resources',{}).get(IDENTITY,{})
        reference=row.get('previous_restore',{})
        action=retry_documents.hydrate(reference.get('document'),ROOT/STATE)
        closed=retry_documents.hydrate(state.get('maintenance_retry'),ROOT/STATE)
        slot=intent['slot']
        if (not isinstance(action,dict) or action.get('kind')!='BoilerResourceRetryAction' or
            action.get('generation')!=intent['generation'] or action.get('phase')!='states' or
            action.get('status')!='reinstalling' or not isinstance(closed,dict) or
            closed.get('phase')!='closed' or closed.get('status')!='closed' or
            any(closed.get(k)!=action.get(k) for k in ('token','group','generation','target_roles','receipt','original_receipts','original_resources','closure')) or
            IDENTITY not in action.get('closure',[]) or
            reference.get('slot')!=slot or reference.get('identity')!=IDENTITY or
            row.get('retry_token')!=action.get('token') or action.get('target_roles')!=intent.get('target_roles')):
            raise SidecarError('CPU handover retry action differs')
        old_receipt=action.get('original_receipts',{}).get(IDENTITY,{})
        new_receipt=intent.get('resource_receipts',{}).get(IDENTITY,intent.get('resource_receipt',{}))
        def only(receipt,tx):
            return len([r for r in receipt.get('transactions',[]) if r.get('scope')==slot and
                        r.get('component')==IDENTITY and r.get('transaction')==tx])==1
        if (not only(old_receipt,old['transaction']) or not only(new_receipt,transaction) or
            new_receipt!=action.get('receipt') or new_receipt.get('id')!=action.get('group')):
            raise SidecarError('CPU handover receipt differs')
        prior=action.get('original_resources',{}).get(slot,{}).get(IDENTITY,{})
        proof=prior.get('restore_result',{})
        old_intent=action.get('original_intents',{}).get(slot,{})
        if (prior.get('status')!='restored' or
            any(old_intent.get(k)!=intent.get(k) for k in ('generation','slot','root_partuuid','target_roles','selection')) or
            proof.get('kind')!='BoilerCpuLiveRestore' or proof.get('phase')!='stopped' or
            (proof.get('generation'),proof.get('transaction'),proof.get('boot_id'),proof.get('invocation'))!=
            (intent['generation'],old['transaction'],attempt.get('boot'),attempt.get('invocation'))):
            raise SidecarError('CPU handover stop proof differs')
        for tx,status in ((old['transaction'],'restored'),(transaction,'committed')):
            journal=read_private(engine._journal(tx));fixed_component(journal.get('component'))
            group=old_receipt.get('id') if tx==old['transaction'] else action.get('group')
            if ((journal.get('id'),journal.get('root'),journal.get('status'))!=(tx,engine.identity,status) or
                journal['component'].get('resource_group')!=group):
                raise SidecarError('CPU handover journal differs')
        if engine._state()['components'].get(IDENTITY,{}).get('transaction')!=transaction:
            raise SidecarError('CPU handover installed transaction differs')
    cpu_sidecar_store._handover(ROOT,generation=intent['generation'],transaction=transaction,
                                engine_identity=engine.identity,validate=validate_handover)
    before=_observations(probe);public_state=_public_state();expected=cpu_sidecar_ledger.expected_after(before,probe['desired'],public_state)
    expected_config=probe['config'] if probe['enabled'] else {'enabled':False}
    expected_digest=hashlib.sha256(json.dumps(expected_config).encode()).hexdigest()
    return cpu_sidecar_store._transition(ROOT,'prepare',generation=intent['generation'],boot=boot,
        invocation=invocation,transaction=transaction,config_sha256=probe['config_sha256'],expected_config_sha256=expected_digest,before=before,
        desired=probe['desired'],expected_after=expected,public_state=public_state)


def poststart():
    intent,transaction,boot,invocation,probe=_context()
    return cpu_sidecar_store._transition(ROOT,'complete',generation=intent['generation'],boot=boot,
        invocation=invocation,transaction=transaction,after=_observations(probe),config_sha256=probe['config_sha256'])


def poststop():
    intent,transaction,boot,invocation,probe=_context(dependencies=False)
    try:
        cpu_sidecar_store._snapshot(ROOT)
    except FileNotFoundError:
        # The first reviewed restart may stop a pre-existing invocation that
        # predates this sidecar. Its complete before-state must already be
        # durable in firstboot's recovery record before systemctl is called.
        from maintenance.resource_firstboot import STATE
        state=read_private(ROOT/STATE);row=state.get('resources',{}).get(IDENTITY,{})
        recovery=row.get('activation_recovery',{});services=recovery.get('services',[])
        if (state.get('generation')!=intent['generation'] or row.get('status')!='activating' or
            recovery.get('kind')!='BoilerCpuLiveRecovery' or recovery.get('generation')!=intent['generation'] or
            recovery.get('transaction')!=transaction or recovery.get('boot_id')!=boot or
            recovery.get('original_invocation')!=invocation or len(services)!=1 or
            services[0].get('before')!='active' or services[0].get('start_pending') is not True):
            raise SidecarError('Unowned CPU stop has no durable reviewed before-state')
        return cpu_sidecar_store._transition(ROOT,'original_stop',generation=intent['generation'],boot=boot,
            invocation=invocation,transaction=transaction,before=recovery['initial_observations'],after=_observations(probe))
    return cpu_sidecar_store._transition(ROOT,'stopped',generation=intent['generation'],boot=boot,
        invocation=invocation,transaction=transaction,after=_observations(probe))




