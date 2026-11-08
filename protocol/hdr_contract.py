"""Fixed game HDR policy. No executable catalog hooks or arbitrary parameters."""
import copy
import hashlib
import json

KIND = 'gamma22-hdr-game-v1'
IDENTITY = 'resource:gamma22-hdr-game'
TARGET = 'home/deck/.config/gamescope/scripts/'
COMMON_SIZE = 6340
COMMON_SHA256 = 'a2a2ba7728de035e5f067c622ab0a409617e2e0cf8b7ea95b57ce106315924ed'
PROFILES = {
    'oxp3': dict(full_frame=472, peak=1100, vendor='SDC', model='AMS881KB01-0', timing='oxp3-public'),
    'x2-mini': dict(full_frame=472, peak=1100, vendor=None, model=None, timing=None),
}
VERSION = '1.0.0-hdr.local2'
LEGACY_PROFILES = dict(PROFILES, **{'x2-mini': dict(PROFILES['x2-mini'], full_frame=475, peak=700)})
FACTS = dict(gamescope_version=['3.16.30-1'], gamescope_user=['deck'], hdr_session=['game'],
             hdr_existing_override=['absent'])

def profile_bytes(key, profiles=PROFILES):
    def lua(value): return 'nil' if value is None else json.dumps(value)
    fields=dict(id=key,**profiles[key])
    return ('-- Fixed model parameters; luminance is software mapping, not a measured panel value.\nboiler_hdr_profile = {'+
        ','.join(k+'='+lua(v) for k,v in fields.items())+'}\nif boiler_hdr_register ~= nil then boiler_hdr_register(boiler_hdr_profile) end\n').encode()

def validate_contract(resource):
    profiles = {VERSION: PROFILES, '1.0.0-hdr.local1': LEGACY_PROFILES}.get(resource.get('version'))
    if (resource.get('id') != IDENTITY or resource.get('install', {}).get('kind') != KIND
            or resource.get('verify') != {'kind':'sha256-v1'}
            or resource.get('restore') != {'kind':'transaction-backup-v1'}
            or resource.get('hooks') or resource.get('dynamic_handler')
            or resource.get('dependencies') != []
            or profiles is None or resource.get('profiles') != profiles or resource.get('hardware_requires') != FACTS):
        raise ValueError('Unsupported fixed game HDR contract')
    expected = [dict(source='payload/game/common.lua', target=TARGET+'98-boiler-gamma22.lua', scope='shared-home', mode=420)]
    # The profile target is rendered by trusted code, never an arbitrary path.
    if resource['install'].get('files') != expected:
        raise ValueError('Invalid HDR installation targets')
    if len(resource['payload']['files'])!=3 or {e['path'] for e in resource['payload']['files']} != {
            'payload/game/common.lua','payload/game/oxp3.lua','payload/game/x2-mini.lua'}:
        raise ValueError('Invalid HDR payload inventory')
    entries={e['path']:e for e in resource['payload']['files']}
    common=entries['payload/game/common.lua']
    if common['size']!=COMMON_SIZE or common['sha256']!=COMMON_SHA256:
        raise ValueError('HDR common implementation differs from the reviewed fixed handler')
    for key in profiles:
        raw=profile_bytes(key, profiles);entry=entries['payload/game/'+key+'.lua']
        if entry['size']!=len(raw) or entry['sha256']!=hashlib.sha256(raw).hexdigest():
            raise ValueError('HDR profile bytes do not encode the fixed parameters')
    return resource

def render(resource, parameters):
    validate_contract(resource)
    if not isinstance(parameters,dict) or set(parameters) != {'profile'} or parameters['profile'] not in PROFILES:
        raise ValueError('HDR requires one explicit fixed profile parameter')
    key = parameters['profile']; profile = resource['profiles'][key]
    result = copy.deepcopy(resource)
    result['install']['kind'] = 'files-v1'
    result['install']['files'].append(dict(source='payload/game/'+key+'.lua',
        target=TARGET+'00-boiler-hdr-profile.lua', scope='shared-home', mode=420))
    # The unknown X2 selector is a missing fact, never a guessed EDID product ID.
    result['hardware_requires'].update(gamescope_display_vendor=[profile['vendor']],
        gamescope_display_model=[profile['model']], hdr_panel_policy=[profile['timing']])
    result['hdr_selected_profile'] = key
    return result
