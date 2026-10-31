"""Unintegrated transaction core; backend and durable writer are host-owned.

This does not itself invoke Flatpak or confer permission to change a remote.
"""
from copy import deepcopy

MIRROR = 'https://mirrors.ustc.edu.cn/flathub'
SCOPES = ('system', 'user')


class Conflict(RuntimeError):
    pass


def _observation(value):
    if not isinstance(value, dict) or type(value.get('exists')) is not bool:
        raise Conflict('Invalid Flatpak observation')
    if value['exists'] and any(not isinstance(value.get(key), str) or not value[key]
                               for key in ('url', 'key_digest', 'configuration_digest')):
        raise Conflict('Incomplete Flatpak remote observation')
    if value['exists'] and not isinstance(value.get('configuration'),dict):
        raise Conflict('Missing Flatpak remote configuration')
    return deepcopy(value)


def _owned_url_change(before, after, url):
    expected=deepcopy(before['configuration']);expected['url-is-set']='true'
    return (after.get('exists') and after.get('url')==url and after.get('key_digest')==before['key_digest']
            and after.get('configuration')==expected)


def _scopes(scopes):
    if not isinstance(scopes,tuple) or not scopes or len(set(scopes))!=len(scopes) or set(scopes)-set(SCOPES):
        raise Conflict('Invalid fixed Flatpak installation scope')
    return scopes


def prepare(backend, *, scopes=SCOPES):
    return {scope: _observation(backend.observe(scope)) for scope in _scopes(scopes)}


def activate(backend, before, record, *, rows=None, scopes=SCOPES):
    """Caller serializes operations and must persist every record durably."""
    scopes=_scopes(scopes)
    if set(before) != set(scopes): raise Conflict('Incomplete installation set')
    rows = deepcopy(rows or {})
    for scope in scopes:
        original = _observation(before[scope])
        if original['exists'] and original['configuration'].get('url-is-set') not in (None,'true','false'):
            raise Conflict('Unexpected existing Flatpak URL flag')
        if scope in rows:
            row=rows[scope]
            if row.get('before')!=original:raise Conflict('Flatpak retry before-state differs')
            if row.get('phase') in ('applied','unchanged'):
                if _observation(backend.observe(scope))!=row['after']:raise Conflict('Flatpak changed after its recorded write')
                continue
            if row.get('phase')!='prepared':raise Conflict('Flatpak transaction requires reconciliation first')
        else:
            if _observation(backend.observe(scope)) != original:
                raise Conflict('Flatpak state changed after review')
            row = {'scope': scope, 'before': original, 'phase': 'prepared'}
            rows[scope] = row
            record(deepcopy(rows))
        if _observation(backend.observe(scope))!=original:raise Conflict('Flatpak state changed before write')
        if original['exists'] and original['url'] == MIRROR:
            row.update(phase='unchanged', after=original)
        else:
            row['phase'] = 'writing'; record(deepcopy(rows))
            if original['exists']:
                backend.set_url(scope, MIRROR)
            else:
                backend.create(scope, MIRROR)
            after = _observation(backend.observe(scope))
            if not after['exists'] or after['url'] != MIRROR:
                raise Conflict('Flatpak remote write could not be verified')
            if original['exists'] and not _owned_url_change(original,after,MIRROR):
                raise Conflict('Flatpak key or unrelated remote configuration changed')
            if not original['exists'] and (not after.get('flathub_key') or after['configuration'].get('gpg-verify')!='true'):
                raise Conflict('Created Flathub remote did not retain its verified key and GPG policy')
            row.update(phase='applied', after=after)
        record(deepcopy(rows))
    return rows


def verify(backend, rows):
    result = {}
    for scope, row in rows.items():
        if scope not in SCOPES or row.get('phase') not in ('unchanged', 'applied'):
            raise Conflict('Interrupted or invalid Flatpak transaction')
        result[scope] = _observation(backend.observe(scope)) == row['after']
    return result


def restore(backend, rows, record, *, scopes=SCOPES):
    for scope in reversed(_scopes(scopes)):
        if scope not in rows: continue
        row = rows[scope]
        if row.get('phase') in ('unchanged','restored'): continue
        if row.get('phase') != 'applied': raise Conflict('Flatpak interruption requires reconciliation')
        if _observation(backend.observe(scope)) != row['after']:
            raise Conflict('Flatpak remote changed after installation; preserved')
        row['phase'] = 'restoring'; record(deepcopy(rows))
        if row['before']['exists']:
            backend.set_url(scope, row['before']['url'])
            middle=_observation(backend.observe(scope))
            if not _owned_url_change(row['before'],middle,row['before']['url']):
                raise Conflict('Flatpak restore changed key or unrelated configuration')
            backend.restore_url_flag(scope,row['before'])
            if _observation(backend.observe(scope)) != row['before']:
                raise Conflict('Flatpak remote restore could not be verified')
        else:
            # Native backend must refuse deletion if installed refs use remote;
            # never force-remove application or runtime data.
            backend.delete_without_force(scope)
            if _observation(backend.observe(scope))['exists']:
                raise Conflict('Flatpak remote still exists after restore')
        row['phase'] = 'restored'; record(deepcopy(rows))
    return rows


def reconcile(backend, rows, record):
    """Reconcile only states whose before/after observations are durable."""
    for scope, row in rows.items():
        if scope not in SCOPES: raise Conflict('Unknown installation scope')
        current = _observation(backend.observe(scope))
        if row.get('phase') == 'writing':
            if current == row['before']:row['phase'] = 'prepared'
            elif row['before']['exists'] and _owned_url_change(row['before'],current,MIRROR):
                row.update(phase='applied',after=current)
            elif not row['before']['exists'] and backend.created_matches(current):
                row.update(phase='applied',after=current)
            else:raise Conflict('Interrupted Flatpak write needs explicit inspection')
        elif row.get('phase') == 'restoring':
            if current == row['after']:
                row['phase'] = 'applied'
            elif current == row['before']:
                row['phase'] = 'restored'
            elif row['before']['exists'] and _owned_url_change(row['before'],current,row['before']['url']):
                backend.restore_url_flag(scope,row['before'])
                if _observation(backend.observe(scope))!=row['before']:
                    raise Conflict('Interrupted Flatpak flag restoration differs')
                row['phase']='restored'
            else:
                raise Conflict('Interrupted Flatpak restore conflicts with external state')
        record(deepcopy(rows))
    return rows
