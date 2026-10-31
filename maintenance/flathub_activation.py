"""Fixed Flatpak remote activation; catalog data cannot choose a command."""
from pathlib import Path
import contextlib,copy,os,tempfile
from .adoption_blobs import _directory,_file
from .resource_runtime import ResourceError
from . import flathub_native,flathub_transaction,flathub_user_store
from protocol.flathub_contract import IDENTITY,TARGET,validate_contract


def _intent(root,identity,selection):
    from .resource_firstboot import checked_intent
    value=checked_intent(root)
    if identity!=IDENTITY or identity not in selection or set(selection)!=set(value['selection']['components']):
        raise ResourceError('Flathub selection differs from the authenticated intent')
    return value


def _backend(root,pool):
    if Path(root)!=Path('/') or os.name!='posix' or os.geteuid()!=0:
        raise ResourceError('Flatpak activation requires the actual Linux root')
    import pwd
    validate_contract(pool.resource(IDENTITY))
    account=pwd.getpwnam('deck')
    if account.pw_uid<1000 or account.pw_dir!='/home/deck':raise ResourceError('Unexpected deck account')
    with _directory(Path('/home/deck'),user_from=Path('/home/deck'),user_uid=account.pw_uid):pass
    with _file(Path('/')/TARGET) as (stream,info):
        if info.st_size!=4040:raise ResourceError('Flathub definition size differs')
        definition=stream.read()
    prefix=['/usr/bin/runuser','-u','deck','--','/usr/bin/env','HOME=/home/deck',
            'XDG_DATA_HOME=/home/deck/.local/share','LC_ALL=C']
    bindings={'system':{'flatpak':['/usr/bin/flatpak','--system'],'ostree':['/usr/bin/ostree'],
                        'repo':'/var/lib/flatpak/repo'},
              'user':{'flatpak':prefix+['/usr/bin/flatpak','--user'],'ostree':prefix+['/usr/bin/ostree'],
                      'repo':'/home/deck/.local/share/flatpak/repo',
                      'user_owner':{'user_from':Path('/home/deck'),'user_uid':account.pw_uid}}}
    return flathub_native.Backend(bindings,definition)


@contextlib.contextmanager
def offline_backend(root,pool):
    """Use a private Flatpak installation descriptor for a probed inactive slot."""
    root=Path(root).absolute()
    if root==Path('/') or os.name!='posix' or os.geteuid()!=0:
        raise ResourceError('Offline Flathub backend requires a mounted inactive target')
    import pwd
    from .engine import Engine
    from .adoption_blobs import _target_user
    account=pwd.getpwnam('deck')
    user=_target_user(Engine(root,read_only=True),root/'home/deck/.local/share/flatpak/repo/config')
    if account.pw_uid<1000 or account.pw_dir!='/home/deck' or user.get('user_uid')!=account.pw_uid:
        raise ResourceError('Inactive slot deck account differs from live user')
    validate_contract(pool.resource(IDENTITY))
    with _file(root/TARGET) as (stream,info):
        if info.st_size!=4040:raise ResourceError('Inactive Flathub definition differs')
        definition=stream.read()
    install=root/'var/lib/flatpak'
    if '\n' in str(install) or '\r' in str(install):raise ResourceError('Invalid fixed inactive target')
    with tempfile.TemporaryDirectory(prefix='boiler-flatpak-slot-',dir='/run') as temporary:
        folder=Path(temporary);(folder/'installations.d').mkdir(mode=0o700)
        (folder/'installations.d/boiler-inactive.conf').write_text('[Installation "boiler-inactive"]\nPath='+str(install)+'\n')
        prefix=['/usr/bin/runuser','-u','deck','--','/usr/bin/env','HOME=/home/deck',
                'XDG_DATA_HOME=/home/deck/.local/share','LC_ALL=C']
        bindings={'system':{'flatpak':['/usr/bin/env','FLATPAK_CONFIG_DIR='+str(folder),'/usr/bin/flatpak','--installation=boiler-inactive'],
                            'ostree':['/usr/bin/ostree'],'repo':str(install/'repo')},
                  'user':{'flatpak':prefix+['/usr/bin/flatpak','--user'],'ostree':prefix+['/usr/bin/ostree'],
                          'repo':'/home/deck/.local/share/flatpak/repo',
                          'user_owner':{'user_from':Path('/home/deck'),'user_uid':account.pw_uid}}}
        yield flathub_native.Backend(bindings,definition)


def restore_bound(backend,recovery,record_before_write,*,partuuid,record_user_before_write=None):
    recovery=copy.deepcopy(recovery)
    store=flathub_user_store.Store()
    store.preflight_release(recovery['generation'],partuuid.lower())
    def save(rows):recovery['rows']=rows;record_before_write(copy.deepcopy(recovery))
    recovery['rows']=flathub_transaction.reconcile(backend,recovery['rows'],save)
    recovery['rows']=flathub_transaction.restore(backend,recovery['rows'],save,scopes=('system',))
    def save_user(state):
        recovery['user_ledger']=dict(epoch=state['epoch'],status=state['status'])
        (record_user_before_write or record_before_write)(copy.deepcopy(recovery))
    recovery['user_restore']=store.release(backend,recovery['generation'],partuuid.lower(),save_user)
    return recovery


def _apply(identity,root,pool,selection,record_before_write,approve,previous=None):
    intent=_intent(root,identity,selection);backend=_backend(root,pool)
    if previous is None:
        recovery=dict(kind='BoilerFlathubRecovery',generation=intent['generation'],slot=intent['slot'],
                      before=flathub_transaction.prepare(backend,scopes=('system',)),rows={})
        record_before_write(copy.deepcopy(recovery));approve(identity)
    else:
        recovery=copy.deepcopy(previous)
        if recovery.get('kind')!='BoilerFlathubRecovery' or recovery.get('generation')!=intent['generation'] or recovery.get('slot')!=intent['slot']:
            raise ResourceError('Flathub recovery belongs to another generation')
        approve(identity)
        def save_reconciled(rows):recovery['rows']=rows;record_before_write(copy.deepcopy(recovery))
        if any(row.get('phase') in ('writing','restoring') for row in recovery['rows'].values()):
            recovery['rows']=flathub_transaction.reconcile(backend,recovery['rows'],save_reconciled)
    def save(rows):recovery['rows']=rows;record_before_write(copy.deepcopy(recovery))
    recovery['rows']=flathub_transaction.activate(backend,recovery['before'],save,rows=recovery['rows'],scopes=('system',))
    def save_user(state):
        recovery['user_ledger']=dict(epoch=state['epoch'],status=state['status']);record_before_write(copy.deepcopy(recovery))
    flathub_user_store.Store().acquire(backend,intent['generation'],intent['root_partuuid'].lower(),save_user)
    return dict(status='configured',runtime='remote-configured-offline',network_validated=False,recovery=recovery)


def activate(identity,*,root=Path('/'),pool,facts,selection,record_before_write,approve):
    return _apply(identity,root,pool,selection,record_before_write,approve)


def resume(identity,*,root=Path('/'),pool,facts,selection,state,record_before_write,approve):
    return _apply(identity,root,pool,selection,record_before_write,approve,previous=state)


def verify(identity,*,root=Path('/'),pool,facts,selection,state=None):
    intent=_intent(root,identity,selection)
    if not state or state.get('generation')!=intent['generation'] or state.get('slot')!=intent['slot']:
        raise ResourceError('Flathub recovery belongs to another slot')
    backend=_backend(root,pool);result=flathub_transaction.verify(backend,state['rows'])
    shared=flathub_user_store.Store().verify(backend,intent['generation'],intent['root_partuuid'].lower())
    if set(result)!={'system'} or not all(result.values()) or not shared['ok'] or shared['epoch']!=state.get('user_ledger',{}).get('epoch'):
        raise ResourceError('Flathub remote changed after installation')
    result['user']=shared['ok']
    return dict(status='configured',runtime='remote-configured-offline',network_validated=False,installations=result)


def restore(identity,*,root=Path('/'),state,record_before_write):
    from .resource_firstboot import checked_intent,open_pool
    intent=checked_intent(root)
    if identity!=IDENTITY or state.get('generation')!=intent['generation'] or state.get('slot')!=intent['slot']:
        raise ResourceError('Flathub restore belongs to another slot')
    with open_pool(root,intent) as (pool,refs):
        backend=_backend(root,pool);recovery=restore_bound(backend,state,record_before_write,partuuid=intent['root_partuuid'])
    return dict(status='restored',network_validated=False,recovery=recovery)
