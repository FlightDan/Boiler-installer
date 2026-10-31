"""Fixed local-test Waydroid host packages and durable Android initialization."""
import copy
from pathlib import Path
from . import waydroid_alpm_transaction as alpm, waydroid_android_init as android
from .waydroid_alpm_backend import LivePacman, PINS

IDENTITY='resource:common-waydroid'
def _backend(pool):
 if pool.local_test is not True:raise RuntimeError('Waydroid unsigned build inputs require the explicit local-test pool')
 return LivePacman({name:dict(size=size,sha256=sha) for name,(size,sha) in PINS.items()},trust_local_test=True)

def _binding(root,identity,selection):
 from .resource_firstboot import checked_intent
 intent=checked_intent(root)
 if identity!=IDENTITY or identity not in selection or intent['selection'].get('trust')!='local-test':raise RuntimeError('Waydroid requires the installed local-test selection')
 return intent

def _validate_recovery(state,intent):
 if not isinstance(state,dict) or state.get('kind')!='BoilerWaydroidRecovery' or any(state.get(k)!=v for k,v in (('identity',IDENTITY),('generation',intent['generation']),('slot',intent['slot']))):raise RuntimeError('Waydroid recovery binding differs')
 host=state.get('host',{});images=state.get('android',{})
 if host.get('generation')!=intent['generation'] or host.get('slot')!=intent['slot'] or images.get('generation')!=intent['generation']:raise RuntimeError('Waydroid nested recovery generation differs')

def _apply(root,pool,state,record):
 state=copy.deepcopy(state);backend=_backend(pool)
 def host_record(value):state['host']=copy.deepcopy(value);record(copy.deepcopy(state))
 def android_record(value):state['android']=copy.deepcopy(value);record(copy.deepcopy(state))
 from .live_root import write_scope
 from .engine import Engine
 with write_scope(Engine(root)):
  state['host']=alpm.retry(backend,state['host'],host_record)
 state['android']=android.activate(root,state['android'],android_record)
 if state['android'].get('phase')!='ready':raise RuntimeError('Android initialization failed; retry when the official image service is reachable: '+state['android'].get('error',''))
 record(copy.deepcopy(state))
 return dict(status='configured',runtime='initialized-awaiting-user-launch',recovery=state,evidence=dict(host_packages='verified',android_images='initialized',container='not-tested'))

def activate(identity,*,root=Path('/'),pool,facts,selection,record_before_write=None,approve=None,system=None):
 intent=_binding(root,identity,selection)
 if record_before_write is None:raise RuntimeError('Waydroid requires a durable recorder')
 # Check existing Android data before starting any package transaction.
 images=android.prepare(root,intent['generation']);host=alpm.prepare(_backend(pool),intent['generation'],intent['slot'])
 state=dict(kind='BoilerWaydroidRecovery',identity=identity,generation=intent['generation'],slot=intent['slot'],host=host,android=images)
 record_before_write(copy.deepcopy(state))
 return _apply(root,pool,state,record_before_write)

def resume(identity,*,root=Path('/'),pool,facts,selection,state,record_before_write=None,approve=None,system=None):
 intent=_binding(root,identity,selection)
 if record_before_write is None:raise RuntimeError('Waydroid requires a durable recorder')
 _validate_recovery(state,intent)
 return _apply(root,pool,state,record_before_write)

def verify(identity,*,root=Path('/'),pool,facts,selection,state=None,system=None):
 intent=_binding(root,identity,selection)
 _validate_recovery(state,intent)
 alpm.verify(_backend(pool),state['host']);android.verify(root,state['android'])
 return dict(status='configured',runtime='initialized-awaiting-user-launch',evidence=dict(host_packages='verified',android_images='initialized',container='not-tested'))

def preflight_slot(root,intent,state):
 if state is None:return None
 if intent['selection'].get('trust')!='local-test':raise RuntimeError('Waydroid slot is not authorized for local test')
 _validate_recovery(state,intent)
 backend=LivePacman({name:dict(size=size,sha256=sha) for name,(size,sha) in PINS.items()},trust_local_test=True,root=root)
 backend.require_exact_target();host=state['host'];alpm._validate(host)
 values=alpm._observe(backend,host,allow_added_installed=host['phase'] not in ('prepared','restored'))
 return dict(phase=host['phase'],packages=values)

def restore_slot(root,intent,state,record_before_write):
 preflight_slot(root,intent,state)
 backend=LivePacman({name:dict(size=size,sha256=sha) for name,(size,sha) in PINS.items()},trust_local_test=True,root=root)
 state=copy.deepcopy(state)
 def record_host(value):state['host']=copy.deepcopy(value);record_before_write(copy.deepcopy(state))
 from .live_root import write_scope
 from .engine import Engine
 with write_scope(Engine(root)):
  state['host']=alpm.restore(backend,state['host'],record_host)
 record_before_write(copy.deepcopy(state))
 return state

def restore(identity,*,root=Path('/'),state,record_before_write,pool=None,system=None):
 if identity!=IDENTITY or state.get('kind')!='BoilerWaydroidRecovery':raise RuntimeError('Waydroid restore binding differs')
 state=copy.deepcopy(state)
 def record_host(value):state['host']=copy.deepcopy(value);record_before_write(copy.deepcopy(state))
 # The firstboot intent was explicitly local-test; no signed-release path inherits this.
 from .resource_firstboot import checked_intent
 intent=checked_intent(root)
 if intent['selection'].get('trust')!='local-test':raise RuntimeError('Waydroid restore is not authorized for local test')
 _validate_recovery(state,intent)
 backend=LivePacman({name:dict(size=size,sha256=sha) for name,(size,sha) in PINS.items()},trust_local_test=True)
 if backend.installed('waydroid') is not None and not android._inactive():raise RuntimeError('Stop the Waydroid container before removing its host packages')
 from .live_root import write_scope
 from .engine import Engine
 with write_scope(Engine(root)):
  state['host']=alpm.restore(backend,state['host'],record_host)
 record_before_write(copy.deepcopy(state))
 return dict(status='restored',runtime='validated',preserved_android_data=True)
