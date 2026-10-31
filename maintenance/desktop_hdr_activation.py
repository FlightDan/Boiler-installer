"""Fixed desktop HDR activation; never restarts KWin or enables HDR."""
from pathlib import Path
from .resource_runtime import ResourceError
from .resource_activation import LiveSystem

IDENTITY='resource:gamma22-hdr-desktop'

def _bound(identity,root,pool,selection):
    if Path(root)!=Path('/') or identity!=IDENTITY or identity not in selection:
        raise ResourceError('Desktop HDR activation requires the live selected resource')
    resource=pool.resource(identity)
    binding=resource.get('desktop_hdr_panel_binding')
    from .desktop_hdr_panel import validate_binding
    binding=validate_binding(binding)
    if resource.get('hdr_selected_profile')!=binding['profile'] or resource.get('desktop_bundle_sha256')!=binding['bundle_sha256']:
        raise ResourceError('Installed desktop profile or bundle differs from binding')
    return binding

def activate(identity,*,root,pool,facts,selection,record_before_write,approve=None):
    binding=_bound(identity,root,pool,selection)
    if not callable(record_before_write):raise ResourceError('Desktop HDR recovery callback required')
    system=LiveSystem(root);uid,bus=system.user()
    if uid!=binding['account']['uid']:raise ResourceError('Desktop account UID differs from binding')
    import pwd
    account=pwd.getpwnam('deck')
    if account.pw_gid!=binding['account']['gid'] or account.pw_dir!=binding['account']['home']:
        raise ResourceError('Desktop account GID or home differs from binding')
    recovery=dict(kind='BoilerDesktopHdrActivationV1',identity=identity,generation=binding['generation'],
                  profile=binding['profile'],bundle_sha256=binding['bundle_sha256'],daemon_reload_pending=bool(bus))
    record_before_write(recovery)
    if bus:
        result=system.userctl('daemon-reload')
        if result is None or result[0]!=0:raise ResourceError('Desktop user manager reload failed')
        recovery['daemon_reload_pending']=False;record_before_write(recovery)
    return dict(status='configured',runtime='configured-next-desktop-login' if bus else 'pending-user-session',recovery=recovery)

def verify(identity,*,root,pool,facts,selection,state=None):
    binding=_bound(identity,root,pool,selection)
    if not isinstance(state,dict) or state.get('kind')!='BoilerDesktopHdrActivationV1' or state.get('identity')!=identity or state.get('generation')!=binding['generation'] or state.get('profile')!=binding['profile'] or state.get('bundle_sha256')!=binding['bundle_sha256']:
        raise ResourceError('Desktop activation recovery differs from binding')
    uid,bus=LiveSystem(root).user()
    if uid!=binding['account']['uid']:raise ResourceError('Desktop account UID differs from binding')
    import pwd
    account=pwd.getpwnam('deck')
    if account.pw_gid!=binding['account']['gid'] or account.pw_dir!=binding['account']['home']:
        raise ResourceError('Desktop account GID or home differs from binding')
    return dict(status='configured',runtime='configured-next-desktop-login' if bus else 'pending-user-session',recovery=state)

def restore(identity,*,root,state,record_before_write):
    if Path(root)!=Path('/') or identity!=IDENTITY or not isinstance(state,dict) or state.get('kind')!='BoilerDesktopHdrActivationV1' or state.get('identity')!=identity:
        raise ResourceError('Invalid desktop activation recovery')
    if not callable(record_before_write):raise ResourceError('Desktop restore recovery callback required')
    recovery=dict(kind='BoilerDesktopHdrRestoreV1',identity=identity,generation=state.get('generation'),disable_pending=True)
    record_before_write(recovery)
    system=LiveSystem(root);_,bus=system.user()
    session=[]
    if bus:
        environment=system.userctl('show-environment')
        if environment is None or environment[0]!=0:raise ResourceError('Cannot inspect actual user session environment')
        allowed={'XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS','WAYLAND_DISPLAY'}
        for line in environment[1].splitlines():
            key,sep,value=line.partition('=')
            if sep and key in allowed and value and not any(ord(c)<32 for c in value):session.append(key+'='+value)
    result=system.run('runuser','-u','deck','--','/usr/bin/env',*session,'/usr/bin/python3','-I','-B',
        '/opt/oxp3-desktop-gamma22/6.7.3-1.6/libexec/boiler-desktop-hdr/hdr_trial.py','--disable')
    if result[0]!=0:raise ResourceError('Desktop trial did not stop safely')
    recovery['disable_pending']=False;record_before_write(recovery)
    return dict(status='restored',runtime='pending-files-removal-and-user-manager-reload',recovery=recovery)

def resume_original(identity,*,root,state,record_before_write,prior_resume=None):
    if Path(root)!=Path('/') or identity!=IDENTITY or not isinstance(state,dict) or state.get('kind')!='BoilerDesktopHdrActivationV1' or state.get('identity')!=identity or not callable(record_before_write):
        raise ResourceError('Invalid desktop post-restore recovery')
    if prior_resume is not None:
        if (type(prior_resume) is not dict or set(prior_resume)!={'kind','identity','generation','reload_pending'}
                or prior_resume['kind']!='BoilerDesktopHdrPostRestoreV1' or prior_resume['identity']!=identity
                or prior_resume['generation']!=state.get('generation') or type(prior_resume['reload_pending']) is not bool):
            raise ResourceError('Desktop post-restore progress differs from activation generation')
        if not prior_resume['reload_pending']:
            return dict(status='restored',runtime='validated',recovery=prior_resume)
    system=LiveSystem(root);_,bus=system.user()
    recovery=dict(kind='BoilerDesktopHdrPostRestoreV1',identity=identity,generation=state.get('generation'),reload_pending=bool(bus))
    record_before_write(recovery)
    if bus:
        result=system.userctl('daemon-reload')
        if result is None or result[0]!=0:raise ResourceError('Desktop post-restore user manager reload failed; retry required')
        recovery['reload_pending']=False;record_before_write(recovery)
    return dict(status='restored',runtime='validated',recovery=recovery)
