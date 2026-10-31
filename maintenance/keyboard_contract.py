"""A fixed host capability; package data can never supply executable code."""
from .engine import MaintenanceError

IDENTITY = 'x2-mini:keyboard'
HANDLER = 'x2-keyboard-v1'
TARGET = 'home/deck/.config/kwinrc'
VERSION = '0.1.0-localtest.20261031.keyboard.1'


def check(component):
    if (component.get('id') != IDENTITY or component.get('version') != VERSION
            or component.get('dynamic_handler') != HANDLER
            or component.get('minimum_engine_version') != '0.1.1'
            or component.get('default') is not False
            or component.get('selection_policy') != 'explicit-opt-in'
            or component.get('files') != [] or component.get('hooks') != {}
            or component.get('requires') != [] or component.get('conflicts') != []
            or component.get('dynamic_targets') != [TARGET]
            or component.get('compatibility') != {'os_ids':['steamos']}):
        raise MaintenanceError('Unexpected fixed keyboard component contract')
    allowed = {'schema_version','kind','id','version','minimum_engine_version','name','description',
        'default','optional','selection_policy','files','hooks','requires','conflicts',
        'dynamic_handler','dynamic_targets','compatibility'}
    if set(component) - allowed:
        raise MaintenanceError('Keyboard component cannot provide runtime arguments or code')
    return component
