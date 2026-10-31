"""Publish/validate both slot plans for the authenticated resource entrypoint."""
import copy
import json
import os
from pathlib import Path
import stat

from maintenance.resource_runtime import ResourceError
from protocol.manifest import validate_manifest, load_json


def entry_options(options):
    from integration import validate_options
    generic = {k: copy.deepcopy(v) for k, v in options.items() if not k.startswith('resource_')}
    validate_options(generic)
    if (generic.get('plugins') != ['core:maintenance'] or generic.get('repairs')
            or generic.get('wireless') or generic.get('graphics_profile') or generic.get('device_package')
            or generic.get('device_components') or generic.get('set_deck_password') or generic.get('enable_ssh')
            or generic.get('kde_language', 'system') != 'system'):
        raise ResourceError('This resource entry requires only the retained core:maintenance; combined generic installation needs a complete shared preflight')
    return generic


def retained_core(payload):
    from integration import load_manifest
    from payload_lock import checked
    row = load_manifest(Path(payload)).get('core:maintenance')
    if not isinstance(row, dict) or row.get('available') is not True:
        raise ResourceError('Retained maintenance payload is unavailable')
    manifest = copy.deepcopy(row['component'])
    validate_manifest(manifest, 'ComponentManifest')
    if manifest['id'] != 'core:maintenance' or manifest.get('hooks') or manifest.get('dynamic_handler'):
        raise ResourceError('A fixed retained maintenance manifest is required')
    for record in row.get('files', []): checked(Path(payload)/'integration', record)
    return manifest


def require_matching_core(session, expected):
    from maintenance.engine import Engine
    for slot, target in session.slots.items():
        engine = Engine(target['root'], read_only=True)
        installed = engine._state()['components'].get('core:maintenance')
        if installed is None:
            raise ResourceError('Install the retained maintenance engine before this resource-only entry; fresh generic+resource installation is not connected')
        journal = load_json(engine._journal(installed['transaction']))
        if journal.get('component') != expected or not engine.verify_readonly('core:maintenance')['ok']:
            raise ResourceError('Retained core differs from the current locked maintenance payload in slot ' + slot)


def _private_path(path, *, directory):
    from file_transaction import directory_fd
    if directory:
        descriptor = directory_fd(path)
    else:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        info = os.fstat(descriptor)
        if (os.name == 'posix' and (info.st_uid != 0 or info.st_mode & 0o022)) or (
                not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)):
            raise ResourceError('Integration metadata is not private tool-owned state')
    finally: os.close(descriptor)


def preflight_metadata(roots):
    for slot, root in roots.items():
        base = Path(root)/'home/.boiler'
        for path in (base, base/'integration', base/'integration'/slot):
            if path.exists() or path.is_symlink(): _private_path(path, directory=True)
        for name in ('result.json', 'install-plan.json'):
            path = base/'integration'/slot/name
            if path.exists() or path.is_symlink(): _private_path(path, directory=False)


def require_clean(session):
    for engine in [*session.engines.values(), session.shared]:
        if (engine.state_dir/'adoption-pending.json').exists():
            raise ResourceError('Pending adoption requires explicit recovery before resource installation')
        for path in engine.journals.glob('*.json'):
            if load_json(path).get('status') not in ('committed', 'restored', 'rolled_back'):
                raise ResourceError('Pending transaction requires explicit recovery before resource installation')
    for path in (session.shared.state_dir/'resource-actions').glob('*.json'):
        if load_json(path).get('status') not in ('committed', 'reconciled'):
            raise ResourceError('Incomplete resource group requires explicit recovery before another installation')


def reject_state_targets(prepared):
    for root, shared in prepared:
        for entry in shared['files']:
            target = entry['target'].casefold(); reserved = '.boiler/integration'
            if target == reserved or target.startswith(reserved+'/') or reserved.startswith(target+'/'):
                raise ResourceError('Resources cannot own installer result/plan metadata')


def _firmware_manifest(session, expected, receipt, slot):
    """Read the committed slot decision, never rerender missing-only after apply."""
    from maintenance.engine import Engine, _same
    from maintenance.firmware_resources import verification
    if session is None:
        raise ResourceError('Firmware results require the actual target-bound resource session')
    if session._bound_receipt(receipt['id']) != receipt or receipt.get('status') != 'committed':
        raise ResourceError('Firmware results require the unchanged committed durable group')
    engine = session.engines[slot]
    rows = [r for r in receipt['transactions'] if r['component'] == expected['id'] and r['scope'] == slot]
    if len(rows) != 1 or rows[0].get('status') != 'committed':
        raise ResourceError('Firmware result requires one committed slot transaction')
    transaction = rows[0]['transaction']
    if engine._state()['components'].get(expected['id'], {}).get('transaction') != transaction:
        raise ResourceError('Firmware result no longer refers to the installed transaction generation')
    journal = load_json(engine._journal(transaction))
    if (journal.get('id') != transaction or journal.get('root') != engine.identity
            or journal.get('status') != 'committed'
            or journal.get('component', {}).get('resource_group') != receipt['id']):
        raise ResourceError('Firmware journal differs from its current durable group')
    resource = session.pool.resource(expected['id'])
    if not verification(resource, expected, journal, engine.root)['ok']:
        raise ResourceError('Firmware slot decisions differ from the reviewed policy or preserved presence')
    if not Engine(engine.root, state_dir=engine.state_dir, read_only=True).verify_readonly(expected['id'])['ok']:
        raise ResourceError('Firmware written or retained target changed')
    installed = copy.deepcopy(journal['component']); installed.pop('resource_group')
    declared = {r['target']: r for r in installed['files']}
    retained = []
    previous = journal.get('before_state', {})
    prior_id = previous.get('components', {}).get(expected['id'], {}).get('transaction')
    prior = load_json(engine._journal(prior_id)) if prior_id else None
    for operation in journal['operations']:
        target, snapshot = operation['target'], operation['after']
        if operation.get('retained'):
            owner = previous.get('owners', {}).get(target, {})
            prior_operations = [] if prior is None else [op for op in prior.get('operations', []) if op['target'] == target]
            if (prior is None or not installed.get('retain_owned_paths') or target in declared or operation['before'] != snapshot
                    or owner.get('component') != expected['id'] or owner.get('transaction') != prior_id
                    or owner.get('sha256') != snapshot.get('sha256')
                    or prior.get('id') != prior_id or prior.get('root') != engine.identity
                    or prior.get('status') != 'committed'
                    or prior.get('component', {}).get('id') != expected['id']
                    or len(prior_operations) != 1 or not _same(snapshot, prior_operations[0]['after'])):
                raise ResourceError('Firmware retained operation lacks its exact prior owned generation')
            retained.append(dict(target=target, snapshot=copy.deepcopy(snapshot), prior_transaction=prior_id))
        else:
            entry = declared.get(target)
            if (entry is None or not snapshot.get('exists') or any(
                    snapshot.get(key) != entry.get(key, 0o644 if key == 'mode' else None)
                    for key in ('sha256', 'size', 'mode'))):
                raise ResourceError('Firmware written operation differs from its declared payload')
    installed['firmware_retained'] = retained
    evidence = dict(policy_sha256=installed['firmware_policy_sha256'],
        written=copy.deepcopy(installed['files']), preserved=copy.deepcopy(installed['firmware_preserved']),
        retained=copy.deepcopy(retained))
    return installed, evidence


def _hdr_evidence(session, root, shared, receipt):
    """Bind displayed parameters to the one actual shared-home generation."""
    if session is None or root['files'] or not shared['files']:
        raise ResourceError('HDR result requires one committed shared-home transaction')
    rows=[r for r in receipt['transactions'] if r['component']==root['id']]
    if len(rows)!=1 or rows[0]['scope']!='shared-home':
        raise ResourceError('HDR receipt must have exactly one shared transaction')
    engine=session.shared;transaction=rows[0]['transaction']
    if engine._state()['components'].get(root['id'],{}).get('transaction')!=transaction:
        raise ResourceError('HDR result no longer owns its recorded generation')
    journal=load_json(engine._journal(transaction));actual=copy.deepcopy(journal.get('component',{}))
    if actual.pop('resource_group',None)!=receipt['id'] or journal.get('status')!='committed' or actual!=shared:
        raise ResourceError('HDR durable manifest differs from the selected profile and payload')
    from maintenance.engine import Engine
    readonly=Engine(engine.root,engine.state_dir,read_only=True,target_domain='shared-home',account_roots=engine.account_roots)
    if not readonly.verify_readonly(root['id'])['ok']:
        raise ResourceError('HDR owned files changed before publishing their result')
    if actual.get('hdr_profile')!=root.get('hdr_profile'):
        raise ResourceError('HDR slot/shared profile binding differs')
    return copy.deepcopy(actual['hdr_profile'])

def documents(options, sources, core, prepared, receipt, templates, *, session=None):
    """One shared receipt is referenced by both views; never install home twice."""
    selected = options['resource_selection']
    rows = {}
    for slot in ('A', 'B'):
        plan = copy.deepcopy(templates[slot]); plan['components'] = [copy.deepcopy(core)]
        plan['resource_pool'] = dict(catalog_sha256=selected['catalog_sha256'], model_id=selected['model_id'],
                                     trust='development-session', shared_home_once=True, receipt_id=receipt['id'])
        components = {'core:maintenance': dict(status='files_installed', activation='retained-verified')}
        for root, shared in prepared:
            logical = copy.deepcopy(root)
            if root['id']=='resource:common-boot-fixes':
                from maintenance.engine import Engine
                engine=session.engines[slot]
                installed=engine._state()['components'].get(root['id'])
                if not installed:raise ResourceError('Boot settings slot transaction is missing')
                journal=load_json(engine._journal(installed['transaction']))
                if journal.get('status')!='committed' or journal['component'].get('resource_group')!=receipt['id']:raise ResourceError('Boot result no longer belongs to its committed generation')
                from maintenance.boot_resources import verification
                if not verification(session.pool.resource(root['id']),journal,engine)['ok'] or not Engine(engine.root,read_only=True).verify_readonly(root['id'])['ok']:raise ResourceError('Boot generated files or policy changed')
                logical=copy.deepcopy(journal['component']);logical.pop('resource_group')
            firmware = None
            from protocol.firmware_contract import IDENTITY
            if root['id'] == IDENTITY:
                logical, firmware = _firmware_manifest(session, root, receipt, slot)
            logical['files'].extend(dict(entry, target='home/'+entry['target']) for entry in shared['files'])
            logical['requires'] = list(dict.fromkeys(root.get('requires', []) + shared.get('requires', [])))
            validate_manifest(logical, 'ComponentManifest')
            plan['components'].append(logical)
            domains = ([slot] if root['files'] else []) + (['shared-home'] if shared['files'] else [])
            transactions = [dict(row) for row in receipt['transactions'] if row['component'] == root['id'] and row['scope'] in domains]
            if {row['scope'] for row in transactions} != set(domains):
                raise ResourceError('Committed resource receipt is missing a selected transaction domain')
            components[root['id']] = dict(status='files_installed', runtime='not_validated',
                resource_receipt=receipt['id'], installed_domains=domains, transactions=transactions)
            if root['id']=='resource:common-boot-fixes':components[root['id']]['boot_policy']=copy.deepcopy(logical['boot_policy'])
            if firmware is not None:
                components[root['id']]['firmware'] = firmware
            from protocol.hdr_contract import IDENTITY as HDR_ID
            if root['id']==HDR_ID:
                components[root['id']]['hdr_profile']=_hdr_evidence(session,root,shared,receipt)
        rows[slot] = dict(schema=1, slot=slot, status='files_installed', source=copy.deepcopy(sources[slot]),
            options={k: copy.deepcopy(v) for k,v in options.items() if k != '_target_disk'},
            components=components, install_plan=plan, resource_receipt=copy.deepcopy(receipt))
    return rows


def publish(roots, rows):
    from file_transaction import atomic_write, directory_fd
    preflight_metadata(roots)
    for slot, root in roots.items():
        base = Path(root)/'home/.boiler'
        for directory in (base, base/'integration', base/'integration'/slot):
            directory.mkdir(mode=0o700, exist_ok=True)
            descriptor = directory_fd(directory); os.close(descriptor)
        state = base/'integration'/slot
        atomic_write(state/'result.json', (json.dumps(rows[slot], indent=2)+'\n').encode(), 0o600)
        atomic_write(state/'install-plan.json', (json.dumps(rows[slot]['install_plan'], indent=2)+'\n').encode(), 0o600)


def verify_records(roots, rows):
    preflight_metadata(roots)
    from protocol.resource_pool import json_document
    for slot, root in roots.items():
        state = Path(root)/'home/.boiler/integration'/slot
        for name, expected in (('result.json', rows[slot]), ('install-plan.json', rows[slot]['install_plan'])):
            with (state/name).open('rb') as stream:
                raw = stream.read(16*1024*1024+1)
            if len(raw) > 16*1024*1024 or json_document(raw) != expected:
                raise ResourceError('Persisted resource selection/plan/result differs from the verified current request')


def receipt_readonly(session, roots, prepared):
    import re
    from protocol.resource_pool import json_document
    preflight_metadata(roots)
    with (Path(roots['A'])/'home/.boiler/integration/A/result.json').open('rb') as stream:
        raw = stream.read(16*1024*1024+1)
    if len(raw) > 16*1024*1024: raise ResourceError('Resource result exceeds the verification limit')
    recorded = json_document(raw).get('resource_receipt', {})
    identity = recorded.get('id')
    if not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{32}', identity):
        raise ResourceError('A durable resource receipt identity is required')
    receipt = load_json(session.shared.state_dir/'resource-actions'/(identity+'.json'))
    if (receipt != recorded or receipt.get('status') != 'committed'
            or receipt.get('local_test') is not session.pool.local_test
            or receipt.get('targets') != {s: str(e.root) for s,e in session.engines.items()}
            or receipt.get('shared_root') != str(session.shared.root)):
        raise ResourceError('Resource group receipt is incomplete or belongs to another target set')
    expected = {(slot, root['id']) for root, shared in prepared for slot in session.engines if root['files']}
    expected.update(('shared-home', shared['id']) for root, shared in prepared if shared['files'])
    rows = receipt.get('transactions', [])
    if len(rows) != len(expected) or {(r.get('scope'), r.get('component')) for r in rows} != expected:
        raise ResourceError('Resource group does not exactly cover selected transaction domains')
    for row in rows:
        engine = session.shared if row['scope'] == 'shared-home' else session.engines[row['scope']]
        installed = engine._state()['components'].get(row['component'], {})
        if installed.get('transaction') != row['transaction']:
            raise ResourceError('Resource result no longer refers to the installed transaction generation')
        journal = load_json(engine._journal(row['transaction']))
        if journal.get('status') != 'committed' or journal.get('component', {}).get('resource_group') != identity:
            raise ResourceError('Resource transaction differs from its durable group receipt')
    return receipt


def emit(rows):
    for slot in ('A', 'B'):
        print('OXP_INTEGRATION_RESULT='+json.dumps(rows[slot]), flush=True)
        print('BOILER_INSTALL_PLAN_JSON='+json.dumps(rows[slot]['install_plan']), flush=True)
        for identity, value in rows[slot]['components'].items():
            print('OXP_COMPONENT_RESULT='+json.dumps(dict(slot=slot, id=identity, **value)), flush=True)
