"""Closed host-defined contract for the shared KDE keyboard capability."""
IDENTITY = 'resource:common-kde-keyboard'
INSTALL = {'kind': 'boiler-kde-keyboard-v1', 'files': []}
VERIFY = {'kind': 'boiler-kde-keyboard-verify-v1'}
RESTORE = {'kind': 'boiler-kde-keyboard-restore-v1'}


def validate_contract(resource):
    if (resource.get('id') != IDENTITY or resource.get('install') != INSTALL
            or resource.get('verify') != VERIFY or resource.get('restore') != RESTORE
            or resource.get('payload') != {'files': []}
            or resource.get('dependencies') != []
            or resource.get('external_component_dependencies') != []
            or resource.get('hardware_requires') != {}):
        raise ValueError('Invalid fixed KDE keyboard resource contract')
    return resource
