"""Closed fixed overlay resource and immutable file targets."""
IDENTITY='resource:common-telemetry-overlay'
KIND='boiler-telemetry-overlay-v1'
FILES=[('collector', 'common/telemetry/overlay/files/telemetry.py', 'var/lib/oxp3-overlay-telemetry/telemetry.py', 420, 6691, '0052e7a97a869b7ed62faf846580c3401dbd407cbaeea4669e9c41dbc5382fb5'), ('system_unit', 'common/telemetry/overlay/files/boiler-overlay-read.service', 'etc/systemd/system/boiler-overlay-read.service', 420, 559, '2490451476a7ed694a7e47107553587125e9c5ae652706f24f18dd6f942faa82'), ('user_unit', 'common/telemetry/overlay/files/oxp3-overlay-telemetry.service', 'etc/systemd/user/oxp3-overlay-telemetry.service', 420, 215, '5417c143996ee59b2eb51ac42a5e467ffc94bacb579252321ea3e47f9c172647'), ('resume_hook', 'common/telemetry/overlay/files/boiler-overlay-resume', 'usr/lib/systemd/system-sleep/boiler-overlay-resume', 493, 93, '25c58cb25b4b0e36abc39c1d4ff0c65c75b240062c0040be15b3bb0ae1cf122f'), ('schema', 'devices/CPU/Intel/G3E/telemetry-schema.toon', 'var/lib/oxp3-overlay-telemetry/telemetry-schema.toon', 420, 199, 'd0964ed9658fe6d53fd09c3f03a7cb97ca742f5780fcd7802d518759a98fa51f')]

def validate_contract(resource):
    payload=resource.get('payload',{}).get('files',[])
    install=resource.get('install',{})
    expected_payload=[dict(path=src,size=size,sha256=digest) for key,src,target,mode,size,digest in FILES]
    expected_files=[dict(source=src,target=target,mode=mode,scope='root-slots') for key,src,target,mode,size,digest in FILES]
    if (resource.get('id')!=IDENTITY or install!={'kind':KIND,'files':expected_files} or payload!=expected_payload
        or resource.get('verify')!={'kind':'sha256-v1'} or resource.get('restore')!={'kind':'transaction-backup-v1'}
        or resource.get('dependencies')!=[] or resource.get('external_component_dependencies')!=[] or resource.get('hardware_requires')!={}):
        raise ValueError('Invalid fixed telemetry overlay contract')
    return resource
