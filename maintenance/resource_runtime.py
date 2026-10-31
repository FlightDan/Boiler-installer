"""Data-only resource execution using Boiler's existing journaled file engine.

This module never imports a model hook or a legacy maintenance package. The
application must authenticate catalog bytes and probe physical targets before
calling it. Local-test trust is an explicit, separate constructor.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import uuid

from maintenance.engine import Engine, MaintenanceError, _json_write
from protocol.manifest import confined, validate_manifest, verify_file, load_json
from protocol.firmware_contract import KIND as FIRMWARE_KIND, validate_contract as validate_firmware
from protocol.cpu_contract import KIND as CPU_KIND, IDENTITY as CPU_ID, validate_contract as validate_cpu, POLICY as CPU_POLICY


class ResourceError(MaintenanceError):
    pass


class PartialInstall(ResourceError):
    def __init__(self, completed, cause, before_slots):
        self.completed = completed
        self.receipt = dict(kind='ResourceInstallReceipt', transactions=copy.deepcopy(completed), before_slots=before_slots)
        super().__init__('Resource installation stopped; completed transactions are retained: ' + str(cause))


class ResourcePool:
    def __init__(self, root, catalog, *, local_test):
        self.root = Path(root)
        self.catalog = copy.deepcopy(catalog)
        self.local_test = local_test

    @classmethod
    def authenticated(cls, root, authenticate):
        """authenticate(root) must verify the signed inventory and return its catalog.

        Supply the host's signature/index verifier, never a model-supplied
        function. A hash stored alongside a pool is not authentication.
        """
        return cls(root, authenticate(Path(root)), local_test=False)

    @classmethod
    def for_local_test(cls, root, *, trust_local_test=False):
        if trust_local_test is not True:
            raise ResourceError('Explicit local-test resource trust is required')
        from protocol.resource_pool import validate_pool
        return cls(root, validate_pool(Path(root)), local_test=True)

    def resource(self, identity):
        rows = self.catalog['resources']
        if isinstance(rows, dict):
            row = rows.get(identity)
        else:
            row = next((r for r in rows if r['id'] == identity), None)
        if row is None:
            raise ResourceError('Unknown resource: ' + identity)
        # Catalogs may inline the record, or carry its parsed record explicitly.
        record = copy.deepcopy(row.get('record', row))
        if record.get('id') != identity or not re.fullmatch(r'resource:[a-z0-9][a-z0-9_.-]*', identity):
            raise ResourceError('Invalid resource identity')
        return record


def resolve(pool, references, selected):
    """Resolve only model-authorized resource IDs, including explicit dependencies."""
    allowed = {r['resource']: r for r in references}
    if len(allowed) != len(references) or len(set(selected)) != len(selected):
        raise ResourceError('Duplicate model resource reference or selection')
    output, visiting, done = [], set(), set()
    def visit(identity):
        if identity not in allowed:
            raise ResourceError('Dependency is not an explicit model resource reference: ' + identity)
        if identity in visiting:
            raise ResourceError('Resource dependency cycle')
        if identity in done:
            return
        row = pool.resource(identity)
        if row.get('install', {}).get('kind') == FIRMWARE_KIND:
            validate_firmware(row)
        elif row.get('install', {}).get('kind') == CPU_KIND:
            validate_cpu(row)
            if pool.local_test is not True:
                raise ResourceError('CPU activation/production deployment remains blocked pending public provider and service acceptance')
        elif row.get('install', {}).get('kind') != 'files-v1':
            raise ResourceError('Resource needs an unimplemented fixed handler: ' + identity)
        elif row.get('verify') != {'kind': 'sha256-v1'} or row.get('restore') != {'kind': 'transaction-backup-v1'}:
            raise ResourceError('Unsupported resource verification/recovery contract')
        if allowed[identity].get('parameters'):
            raise ResourceError('Parameterized resource requires a fixed parameter renderer: ' + identity)
        visiting.add(identity)
        for dependency in row.get('dependencies', []):
            visit(dependency)
        visiting.remove(identity)
        done.add(identity)
        output.append(row)
    for identity in selected:
        visit(identity)
    return output


def check_hardware(resource, facts):
    """Requirements are positive exact matches against trusted probe output."""
    for name, values in resource.get('hardware_requires', {}).items():
        actual = facts.get(name)
        if not isinstance(values, list) or not values or not any(
                type(actual) is type(expected) and actual == expected for expected in values):
            raise ResourceError('Hardware requirement not established: ' + name)


def guard_cpu(pool, slots, identities, *, recovery=False):
    if CPU_ID not in identities: return
    resource = validate_cpu(pool.resource(CPU_ID))
    if pool.local_test is not True:
        raise ResourceError('CPU resource is restricted to explicit local-test offline staging')
    from protocol.manifest import compatible
    for target in slots.values():
        if recovery:
            safety={name: resource['hardware_requires'][name] for name in ('cpu_service_state','cpu_service_enablement','cpu_staging_target')}
            check_hardware({'hardware_requires':safety},target['hardware'])
        else:
            check_hardware(resource, target['hardware'])
        if not recovery and not compatible(resource, target['environment']):
            raise ResourceError('CPU staging compatibility not established')
    from maintenance.cpu_resources import quiescent_layout
    from maintenance.adoption_blobs import observe
    from protocol.cpu_contract import CONFIG
    for target in slots.values():
        quiescent_layout(target['root'])
        engine = Engine(target['root'], read_only=True)
        observe(engine, engine._target(CONFIG))


def guard_pending_cpu(slots, home, *, allow_cpu_roots=False):
    if not allow_cpu_roots:
        for target in slots.values(): Engine(target['root'],read_only=True)._cpu_barrier()
    shared=Engine(home,Path(home)/'.boiler/resource-maintenance',read_only=True,target_domain='shared-home',account_roots=[v['root'] for v in slots.values()])
    shared._cpu_barrier()


def component(resource, scope, *, shared_opt=False):
    inventory = {entry['path']: entry for entry in resource['payload']['files']}
    files = []
    for original in resource['install']['files']:
        row = dict(original)
        # opt.mount is a bind of the shared home filesystem on supported
        # SteamOS images. Render that physical transaction domain once, after
        # the coordinator verifies the effective mount in every selected slot.
        if shared_opt and row.get('scope') == 'root-slots' and (
                row['target'] == 'opt' or row['target'].startswith('opt/')):
            row['scope'] = 'shared-home'
            row['target'] = 'home/.steamos/offload/' + row['target']
        if row.get('scope') not in ('root-slots', 'shared-home'):
            raise ResourceError('Unknown resource target scope')
        if row['scope'] != scope:
            continue
        if scope == 'root-slots':
            target = row['target'].casefold()
            protected = 'var/lib/overlays/etc'
            if target == protected or target.startswith(protected + '/') or protected.startswith(target + '/'):
                raise ResourceError('Persistent /etc overlay storage is private; target the logical etc/ namespace')
        source = row['source']
        if source not in inventory:
            raise ResourceError('Resource source is outside payload inventory')
        entry = inventory[source]
        target = row['target']
        if scope == 'root-slots' and (target == 'home' or target.startswith('home/')):
            raise ResourceError('Shared home must have its own single transaction domain')
        if scope == 'shared-home':
            if not target.startswith('home/'):
                raise ResourceError('Shared home target must use the guest home/ namespace')
            target = target.removeprefix('home/')
        files.append(dict(source=source, target=target, size=entry['size'], sha256=entry['sha256'], mode=row.get('mode', 420)))
    value = dict(schema_version=1, kind='ComponentManifest', id=resource['id'],
                 version=resource['version'], files=files,
                 compatibility=resource.get('compatibility', {}), requires=[], conflicts=[])
    if resource['install']['kind'] == CPU_KIND and scope == 'root-slots':
        value['cpu_mutable_config'] = dict(CPU_POLICY)
    if resource.get('hdr_selected_profile'):
        from protocol.hdr_contract import PROFILES
        profile = resource['hdr_selected_profile']
        value['hdr_profile'] = dict(kind='fixed-gamma22-profile-v1', profile=profile, **PROFILES[profile])
    validate_manifest(value, 'ComponentManifest')
    return value


class ResourceSession:
    """One pool and authoritative target set shared by installer and maintenance.

    slots: {A: {root: Path, environment: dict, hardware: dict}, B: ...}.
    home is the separately mounted shared home filesystem (not /).
    The caller owns the existing disk lease, topology probes and mount lifetime.
    Shared resources install once and restore explicitly after all slot copies.
    """
    def __init__(self, pool, slots, home, *, read_only=False, planned_core=False):
        if not slots or set(slots) - {'A', 'B'}:
            raise ResourceError('An explicit probed slot set is required')
        for path in [*(Path(v['root']).absolute() for v in slots.values()), Path(home).absolute()]:
            for ancestor in (path, *path.parents):
                if ancestor.is_symlink() or (hasattr(ancestor, 'is_junction') and ancestor.is_junction()):
                    raise ResourceError('Resource target roots must not traverse links')
        roots = [Path(v['root']).resolve() for v in slots.values()]
        if len(set(roots)) != len(roots):
            raise ResourceError('Slots must be distinct mounted roots')
        self.pool, self.slots, self.home = pool, copy.deepcopy(slots), Path(home)
        self.read_only = read_only
        if type(planned_core) is not bool: raise ResourceError('Invalid planned core mode')
        self.planned_core = planned_core
        opt_values = [v.get('opt_offload', False) for v in slots.values()]
        if any(not isinstance(value, bool) for value in opt_values) or len(set(opt_values)) != 1:
            raise ResourceError('Both slot /opt offload mount states must agree')
        self.shared_opt = all(opt_values)
        self.engines = {slot: Engine(v['root'], read_only=read_only) for slot, v in slots.items()}
        self.shared = Engine(self.home, self.home / '.boiler/resource-maintenance',
                             read_only=read_only, target_domain='shared-home',
                             account_roots=[v['root'] for v in slots.values()])

    def prepare(self, references, selected):
        resources = resolve(self.pool, references, selected)
        guard_cpu(self.pool, self.slots, {r['id'] for r in resources})
        prepared = []
        self._firmware_prepared = {}
        self.cpu_config_plans = {}
        for resource in resources:
            for target in self.slots.values():
                check_hardware(resource, target['hardware'])
            root = component(resource, 'root-slots', shared_opt=self.shared_opt)
            shared = component(resource, 'shared-home', shared_opt=self.shared_opt)
            from .core_retention import installed_core, inherited, decorate, verify_core
            retain_core=(self.planned_core or any(installed_core(e) for e in self.engines.values()) or
                         any(inherited(e,resource['id']) for e in [*self.engines.values(),self.shared]))
            if retain_core:
                root,shared=decorate(root,shared=False),decorate(shared,shared=True)
                if not self.planned_core:
                    if shared['files'] and set(self.engines)!={'A','B'}:
                        raise ResourceError('Shared core retention requires both root slots')
                    for engine in self.engines.values(): verify_core(Engine(engine.root,read_only=True))
            from protocol.manifest import compatible
            if any(not compatible(root, target['environment']) for target in self.slots.values()):
                raise ResourceError('Resource compatibility not established for every slot')
            # Require an explicit scope-local dependency so Engine can enforce
            # dependency retention on restore. Cross-scope-only dependencies
            # require a future fixed handler and fail before any writes.
            for current, scope in ((root, 'root-slots'), (shared, 'shared-home')):
                if not current['files']:
                    continue
                for dependency in resource.get('dependencies', []):
                    if not component(self.pool.resource(dependency), scope, shared_opt=self.shared_opt)['files']:
                        raise ResourceError('Cross-scope dependency requires a fixed handler')
                    current['requires'].append(dependency)
            for manifest in (root, shared):
                for entry in manifest['files']:
                    verify_file(confined(self.pool.root, entry['source']), entry)
            if resource['install']['kind'] == FIRMWARE_KIND:
                from maintenance.firmware_resources import prepare as prepare_firmware
                for slot, engine in self.engines.items():
                    self._firmware_prepared[(resource['id'], slot)] = prepare_firmware(resource, root, engine)
            if resource['install']['kind'] == CPU_KIND:
                from maintenance.cpu_resources import configuration_plan
                self.cpu_config_plans[resource['id']] = {slot: configuration_plan(engine, root) for slot, engine in self.engines.items()}
            prepared.append((root, shared))
        # Reject conflicts among selected resources before creating any group
        # journal or writing a single target. A/B and shared-home namespaces
        # are distinct; within each namespace the Engine owns every path.
        from maintenance.engine import _overlapping_targets
        for index, scope in ((0, 'root-slots'), (1, 'shared-home')):
            targets = [entry['target'] for pair in prepared for entry in pair[index]['files']]
            if _overlapping_targets(targets):
                raise ResourceError('Selected resources have overlapping targets in ' + scope)
            engines = self.engines.items() if index == 0 else (('shared-home', self.shared),)
            for scope_name, engine in engines:
                owners = engine._state()['owners']
                for pair in prepared:
                    manifest = self._firmware_prepared.get((pair[index]['id'], scope_name), pair[index])
                    for entry in manifest['files']:
                        target = entry['target'].casefold()
                        for existing, owner in owners.items():
                            old = existing.casefold()
                            if (target == old or target.startswith(old+'/') or old.startswith(target+'/')) and owner['component'] != manifest['id']:
                                raise ResourceError('Target already owned by another component in ' + scope)
        return prepared

    def install(self, references, selected):
        if self.read_only:
            raise ResourceError('Read-only resource session cannot install')
        resolved = resolve(self.pool, references, selected)
        guard_cpu(self.pool, self.slots, {r['id'] for r in resolved})
        cpu_selected=any(r['id']==CPU_ID for r in resolved)
        guard_pending_cpu(self.slots,self.home,allow_cpu_roots=cpu_selected)
        if any(r['install']['kind'] in (FIRMWARE_KIND, CPU_KIND) for r in resolved):
            # A killed file transaction must roll back before deciding which
            # firmware belongs to the OS. Otherwise partial writes could be
            # mistaken for pre-existing missing-only files. Recovery failure
            # propagates; no new firmware/group transaction has started.
            for slot, engine in self.engines.items():
                if cpu_selected: engine.recover_cpu(pool=self.pool,slots=self.slots,slot=slot)
                else: engine.recover()
        prepared = self.prepare(references, selected)
        identities = {root['id'] for root, _ in prepared}
        before_slots = {slot: {identity: copy.deepcopy(engine._state()['components'].get(identity))
                              for identity in identities} for slot, engine in self.engines.items()}
        group = uuid.uuid4().hex
        directory = self.shared.state_dir / 'resource-actions'
        from maintenance.adoption_blobs import _directory
        with _directory(directory, create=True):
            pass
        group_path = directory / (group + '.json')
        record = dict(kind='ResourceInstallReceipt', id=group, status='prepared',
            local_test=self.pool.local_test, before_slots=before_slots,
            targets={slot: str(engine.root) for slot, engine in self.engines.items()},
            shared_root=str(self.shared.root), transactions=[],
            resources=sorted(identities), receipt_path=str(group_path))
        _json_write(group_path, record)
        completed = []
        try:
            # Preflight all payloads and hardware above; shared is installed
            # once even when both roots are selected.
            for root, shared in prepared:
                root['resource_group'] = group
                shared['resource_group'] = group
                if shared['files']:
                    environments = [v['environment'] for v in self.slots.values()]
                    from protocol.manifest import compatible
                    if any(not compatible(shared, e) for e in environments):
                        raise ResourceError('Shared resource incompatible with one of the slots')
                    receipt = self.shared.apply(shared, self.pool.root, environment=environments[0])
                    completed.append(dict(receipt, scope='shared-home'))
                    record.update(status='applying', transactions=completed)
                    _json_write(group_path, record)
                if root['files']:
                    for slot, engine in self.engines.items():
                        current_root = root
                        key = (root['id'], slot)
                        if key in self._firmware_prepared:
                            from maintenance.firmware_resources import prepare as prepare_firmware
                            base = copy.deepcopy(root); base.pop('resource_group', None)
                            current_root = prepare_firmware(self.pool.resource(root['id']), base, engine)
                            if current_root != self._firmware_prepared[key]:
                                raise ResourceError('Firmware presence/ownership changed after slot preflight')
                            current_root['resource_group'] = group
                        if root['id']==CPU_ID:
                            receipt=engine.apply_cpu(current_root,self.pool.root,pool=self.pool,slots=self.slots,slot=slot,environment=self.slots[slot]['environment'])
                        else:
                            receipt = engine.apply(current_root, self.pool.root, environment=self.slots[slot]['environment'])
                        completed.append(dict(receipt, scope=slot))
                        record.update(status='applying', transactions=completed)
                        _json_write(group_path, record)
        except Exception as error:
            record.update(status='partial', transactions=completed)
            _json_write(group_path, record)
            exception = PartialInstall(completed, error, before_slots)
            exception.receipt = copy.deepcopy(record)
            raise exception from error
        record.update(status='committed', transactions=completed)
        _json_write(group_path, record)
        return record

    def _bound_receipt(self, identity):
        """Read the retained group without recovering or trusting supplied rows."""
        if not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{32}', identity):
            raise ResourceError('Invalid resource action identity')
        path = confined(self.shared.state_dir, 'resource-actions/' + identity + '.json')
        record = load_json(path)
        if (not isinstance(record, dict) or record.get('kind') != 'ResourceInstallReceipt' or record.get('id') != identity or
                record.get('targets') != {s: str(e.root) for s, e in self.engines.items()} or
                record.get('shared_root') != str(self.shared.root)):
            raise ResourceError('Resource receipt target binding changed')
        resources = record.get('resources')
        if not isinstance(resources, list) or any(not isinstance(r, str) for r in resources) or len(set(resources)) != len(resources):
            raise ResourceError('Invalid resource receipt selection')
        for resource in resources:
            self.pool.resource(resource)
        self._check_receipt_transactions(record)
        return record

    def _check_receipt_transactions(self, record):
        rows = record.get('transactions')
        if not isinstance(rows, list): raise ResourceError('Invalid resource receipt transactions')
        seen = set()
        for row in rows:
            if not isinstance(row, dict): raise ResourceError('Invalid resource receipt transaction')
            scope, transaction = row.get('scope'), row.get('transaction')
            engine = self.shared if scope == 'shared-home' else self.engines.get(scope)
            if (engine is None or row.get('component') not in record['resources'] or not isinstance(transaction, str)
                    or not re.fullmatch('[0-9a-f]{32}', transaction) or (scope, transaction) in seen):
                raise ResourceError('Resource receipt transaction is outside its group')
            journal = load_json(engine._journal(transaction))
            if (journal.get('root') != engine.identity or journal.get('component', {}).get('id') != row['component']
                    or journal.get('component', {}).get('resource_group') != record['id']):
                raise ResourceError('Resource receipt transaction is outside its group')
            seen.add((scope, transaction))

    def retained_receipt(self, identity):
        """Reconcile the target-bound durable group after Engine recovery.

        The marker inside each ordinary Engine journal closes a process-kill
        window between committing a file transaction and recording its receipt.
        """
        if self.read_only:
            raise ResourceError('Read-only resource session cannot reconcile receipts')
        record = self._bound_receipt(identity)
        cpu_selected=self._cpu_group_requires_recovery(record)
        guard_cpu(self.pool, self.slots, [CPU_ID] if cpu_selected else [],recovery=True)
        guard_pending_cpu(self.slots,self.home,allow_cpu_roots=cpu_selected)
        before = copy.deepcopy(record)
        path = confined(self.shared.state_dir, 'resource-actions/' + identity + '.json')
        known = {(r['scope'], r['transaction']) for r in record['transactions']}
        for scope, engine in [*self.engines.items(), ('shared-home', self.shared)]:
            if cpu_selected and scope in self.slots: engine.recover_cpu(pool=self.pool,slots=self.slots,slot=scope)
            else: engine.recover()
            for cid, installed in engine._state()['components'].items():
                if cid not in record['resources']:
                    continue
                journal = load_json(engine._journal(installed['transaction']))
                if journal.get('component', {}).get('resource_group') != identity:
                    continue
                key = (scope, installed['transaction'])
                if key not in known:
                    record['transactions'].append(dict(scope=scope, transaction=installed['transaction'], component=cid, status='committed'))
                    known.add(key)
        self._check_receipt_transactions(record)
        # Keep an already published receipt stable when recovery found no work.
        if record.get('status') == 'committed' and record == before:
            return record
        record['status'] = 'reconciled'
        _json_write(path, record)
        return record

    def _cpu_group_requires_recovery(self, record):
        if CPU_ID not in record['resources']: return False
        if record.get('status') not in ('committed','reconciled'): return True
        for row in record['transactions']:
            if row['component']!=CPU_ID: continue
            engine=self.engines.get(row['scope'])
            if engine is None: return True
            journal=load_json(engine._journal(row['transaction']))
            current=engine._state()['components'].get(CPU_ID,{})
            if current.get('transaction')==row['transaction'] or journal.get('status') not in ('restored','rolled_back'):
                return True
        return False

    def verify(self, identities):
        result = []
        for scope, engine in [*self.engines.items(), ('shared-home', self.shared)]:
            state = engine._state()
            for identity in identities:
                resource = self.pool.resource(identity)
                manifest = component(resource, 'shared-home' if scope == 'shared-home' else 'root-slots', shared_opt=self.shared_opt)
                if not manifest['files']:
                    if identity in state['components']:
                        result.append(dict(component=identity, scope=scope, ok=False, reason='unexpected-installed-scope'))
                    continue
                if identity in state['components']:
                    if state['components'][identity]['version'] != resource['version']:
                        result.append(dict(component=identity, scope=scope, ok=False, reason='version-mismatch'))
                    else:
                        journal = load_json(engine._journal(state['components'][identity]['transaction']))
                        installed = dict(journal.get('component', {}))
                        installed.pop('resource_group', None)
                        from .core_retention import is_resource,decorate
                        if is_resource(installed):manifest=decorate(manifest,shared=(scope=='shared-home'))
                        for dependency in resource.get('dependencies', []):
                            manifest['requires'].append(dependency)
                        if resource['install']['kind'] == FIRMWARE_KIND:
                            from maintenance.firmware_resources import verification
                            firmware = verification(resource, manifest, journal, engine.root)
                            proof = engine.verify_readonly(identity) if self.read_only else engine.verify(identity)
                            result.append(dict(proof, **{k: v for k, v in firmware.items() if k != 'ok'},
                                               ok=proof['ok'] and firmware['ok'], scope=scope))
                            continue
                        if installed != manifest:
                            result.append(dict(component=identity, scope=scope, ok=False, reason='manifest-mismatch'))
                        else:
                            proof = engine.verify_readonly(identity) if self.read_only else engine.verify(identity)
                            if resource['install']['kind'] == CPU_KIND:
                                proof = dict(proof, verification_scope='inactive-files-and-mutable-config-only', activation_verified=False,
                                    production_execution_allowed=False, mutable_configuration=True)
                            result.append(dict(proof, scope=scope))
                else:
                    result.append(dict(component=identity, scope=scope, ok=False, reason='not-installed'))
        return result

    def restore(self, receipt, scope):
        if self.read_only:
            raise ResourceError('Read-only resource session cannot restore')
        if not isinstance(receipt, dict) or not receipt.get('id'):
            raise ResourceError('Restore requires a retained resource group identity')
        receipt = self.retained_receipt(receipt['id'])
        rows = receipt['transactions']
        if scope == 'shared-home':
            if set(self.engines) != {'A', 'B'}:
                raise ResourceError('Shared restore requires inspection of both probed slots')
            # Keep shared functionality while either slot still owns its copy.
            shared_ids = {row['component'] for row in rows if row['scope'] == 'shared-home'}
            for identity in shared_ids:
                for slot, slot_engine in self.engines.items():
                    before = receipt.get('before_slots', {}).get(slot, {})
                    if identity not in before:
                        raise ResourceError('Shared restore requires both slot pre-install generations')
                    prior = before[identity]
                    current = slot_engine._state()['components'].get(identity)
                    if current != prior:
                        raise ResourceError('Restore both slot copies to their prior generations before restoring shared resources')
            engine = self.shared
        elif scope in self.engines:
            engine = self.engines[scope]
        else:
            raise ResourceError('Unknown restore scope')
        result = []
        for row in reversed(rows):
            if row['scope'] != scope:
                continue
            journal = load_json(engine._journal(row['transaction']))
            if journal.get('status') == 'restored':
                continue
            if row['component']==CPU_ID:
                result.append(engine.restore_cpu(row['transaction'],pool=self.pool,slots=self.slots,slot=scope))
            else: result.append(engine.restore(row['transaction']))
        return result

    def recover(self, *, cpu_recovery=False):
        """Use existing durable journals; never infer success across slot domains."""
        if self.read_only:
            raise ResourceError('Read-only resource session cannot recover')
        identities = {identity for engine in self.engines.values() for identity in engine._state()['components']}
        for engine in self.engines.values():
            for path in engine.journals.glob('*.json'):
                journal=load_json(path)
                if journal.get('status') not in ('committed','restored','rolled_back'):
                    identities.add(journal.get('component', {}).get('id'))
        records=[self._bound_receipt(path.stem) for path in sorted((self.shared.state_dir/'resource-actions').glob('*.json'))]
        if any(self._cpu_group_requires_recovery(record) for record in records): identities.add(CPU_ID)
        if CPU_ID in identities and not cpu_recovery: raise ResourceError('CPU journals require the explicitly selected fixed offline recovery entry')
        guard_cpu(self.pool, self.slots, identities,recovery=True)
        guard_pending_cpu(self.slots,self.home,allow_cpu_roots=cpu_recovery)
        engines = {scope: engine.recover_cpu(pool=self.pool,slots=self.slots,slot=scope) if cpu_recovery and CPU_ID in identities and scope in self.slots else engine.recover()
            for scope, engine in [*self.engines.items(), ('shared-home', self.shared)]}
        groups = [self.retained_receipt(path.stem) for path in sorted((self.shared.state_dir / 'resource-actions').glob('*.json'))]
        return dict(engines=engines, resource_actions=groups)


def installer_install(pool, references, selected, *, slots, shared_home):
    identities={r['id'] for r in resolve(pool,references,selected)}
    guard_cpu(pool, slots, identities)
    guard_pending_cpu(slots,shared_home,allow_cpu_roots=CPU_ID in identities)
    return ResourceSession(pool, slots, shared_home).install(references, selected)


def maintenance_action(pool, references, selected, *, slots, shared_home,
                       action, receipt=None, scope=None):
    if action in ('apply', 'restore', 'recover'):
        identities={r['id'] for r in resolve(pool,references,selected)}
        guard_cpu(pool, slots, identities,recovery=action!='apply')
        guard_pending_cpu(slots,shared_home,allow_cpu_roots=CPU_ID in identities)
    session = ResourceSession(pool, slots, shared_home, read_only=(action == 'verify'))
    if action == 'apply':
        return session.install(references, selected)
    if action == 'verify':
        return session.verify([r['id'] for r in resolve(pool, references, selected)])
    if action == 'restore':
        identities = {r['id'] for r in resolve(pool, references, selected)}
        if not isinstance(receipt, dict) or not receipt.get('id'):
            raise ResourceError('Restore requires a retained resource group identity')
        group = session._bound_receipt(receipt['id'])
        if set(group['resources']) != identities:
            raise ResourceError('Restore selection differs from retained resource group')
        return session.restore(receipt, scope)
    if action == 'recover':
        return session.recover(cpu_recovery=CPU_ID in {r['id'] for r in resolve(pool,references,selected)})
    raise ResourceError('Unsupported resource maintenance action')
