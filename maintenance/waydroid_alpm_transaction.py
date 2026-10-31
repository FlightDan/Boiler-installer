"""Fixed Waydroid local-test packages and their two official dependencies."""
import copy,re

IDENTITY='resource:common-waydroid'
VERSIONS={'lxc':'1:7.0.0-1','dnsmasq':'2.93-1','libglibutil':'1.0.80-1','libgbinder':'1.1.52-1','python-gbinder':'1.3.1-1','waydroid':'1.6.3-1','steamos-waydroid-binder':'1-2'}
ORDER=('lxc','dnsmasq','libglibutil','libgbinder','python-gbinder','waydroid','steamos-waydroid-binder')


def prepare(backend,generation,slot):
 if not re.fullmatch('[0-9a-f]{32}',generation) or slot not in ('A','B'):raise RuntimeError('Invalid Waydroid transaction identity')
 backend.require_exact_target()
 before={name:backend.installed(name) for name in ORDER}
 for name,version in before.items():
  if version is not None and version!=VERSIONS[name]:raise RuntimeError('Pre-existing Waydroid package version conflicts: '+name)
 backend.verify_inputs()
 return dict(kind='BoilerWaydroidPackageAction',generation=generation,slot=slot,phase='prepared',before=before,added=[name for name in ORDER if before[name] is None])


def _validate(state):
 if (state.get('kind')!='BoilerWaydroidPackageAction' or not re.fullmatch('[0-9a-f]{32}',state.get('generation','')) or state.get('slot') not in ('A','B') or state.get('phase') not in ('prepared','installing','installed','removing','restored') or set(state.get('before',{}))!=set(ORDER)):
  raise RuntimeError('Invalid Waydroid package action')
 if state.get('added')!=[name for name in ORDER if state['before'][name] is None]:raise RuntimeError('Waydroid package ownership differs')
 if any(value not in (None,VERSIONS[name]) for name,value in state['before'].items()):raise RuntimeError('Waydroid before state differs')


def _observe(backend,state,*,allow_added_installed):
 values={name:backend.installed(name) for name in ORDER}
 for name in ORDER:
  allowed=({None,VERSIONS[name]} if allow_added_installed else {None}) if name in state['added'] else {VERSIONS[name]}
  if values[name] not in allowed:raise RuntimeError('Waydroid package changed outside this action: '+name)
 return values


def activate(backend,state,record):
 state=copy.deepcopy(state);_validate(state);backend.require_exact_target();backend.verify_inputs()
 if state['phase']=='installed':verify(backend,state);return state
 if state['phase'] not in ('prepared','installing'):raise RuntimeError('Waydroid package action requires restore')
 values=_observe(backend,state,allow_added_installed=state['phase']=='installing')
 state['phase']='installing';record(copy.deepcopy(state));missing=[name for name in state['added'] if values[name] is None]
 if missing:backend.install_fixed(missing)
 if any(backend.installed(name)!=VERSIONS[name] for name in ORDER):raise RuntimeError('Waydroid package transaction did not complete; retry')
 state['phase']='installed';record(copy.deepcopy(state));return state


def verify(backend,state):
 _validate(state);backend.require_exact_target()
 if state['phase']!='installed' or any(backend.installed(name)!=VERSIONS[name] for name in ORDER):raise RuntimeError('Waydroid installed package state changed')
 return dict(status='host-configured',android_ready=False)


def restore(backend,state,record):
 state=copy.deepcopy(state);_validate(state);backend.require_exact_target()
 if state['phase']=='restored':return state
 if state['phase']=='prepared':
  _observe(backend,state,allow_added_installed=False)
  state['phase']='restored';record(copy.deepcopy(state));return state
 if state['phase'] not in ('installing','installed','removing'):raise RuntimeError('Waydroid package action cannot restore yet')
 values=_observe(backend,state,allow_added_installed=True)
 state['phase']='removing';record(copy.deepcopy(state));owned=[name for name in reversed(ORDER) if name in state['added'] and values[name] is not None]
 if owned:backend.remove_fixed(owned)
 if any(backend.installed(name) is not None for name in state['added']):raise RuntimeError('Waydroid removal incomplete; retry')
 _observe(backend,state,allow_added_installed=False);state['phase']='restored';record(copy.deepcopy(state));return state


def retry(backend,state,record):
 return restore(backend,state,record) if state.get('phase')=='removing' else activate(backend,state,record)
