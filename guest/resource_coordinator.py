"""Prepared-image resource phase; authentication stays with the trusted caller.

Only /dev/vdc in the managed guest is accepted. Its QEMU DMI identifies the
execution sandbox, never the physical handheld. No JSON option creates trust.
The production host-to-guest authenticated authorization provider is not yet
implemented: entrypoints without that provider deliberately fail before mount.
"""
import copy
import json
from pathlib import Path
import re
import subprocess

from maintenance.resource_runtime import ResourceError, ResourceSession, resolve
from offload_mounts import mount_etc, mount_opt


DISK = '/dev/vdc'
REQUIRED = {'rootfs-A', 'rootfs-B', 'var-A', 'var-B', 'home'}


def selected(options):
    return any(isinstance(key, str) and key.startswith('resource_') and value is not None
               for key, value in options.items())


def selection(options):
    """Typed UI shape, still untrusted until the authorization provider binds it."""
    value = options.get('resource_selection')
    if not isinstance(value, dict) or set(value) != {
            'schema_version', 'model_id', 'catalog_sha256', 'components', 'trust'}:
        raise ResourceError('A typed resource_selection is required')
    if (value['schema_version'] != 1 or value['trust'] not in ('local-test', 'authenticated')
            or not isinstance(value['model_id'], str)
            or not re.fullmatch(r'device:[a-z][a-z0-9-]*', value['model_id'])
            or not isinstance(value['catalog_sha256'], str)
            or not re.fullmatch(r'[0-9a-f]{64}', value['catalog_sha256'])
            or not isinstance(value['components'], list) or not 0 < len(value['components']) <= 128
            or any(not isinstance(v, str) or not re.fullmatch(r'resource:[a-z0-9][a-z0-9_.-]*', v)
                   for v in value['components'])
            or len(set(value['components'])) != len(value['components'])):
        raise ResourceError('Invalid typed resource selection')
    if any(key.startswith('resource_') and key != 'resource_selection' and value is not None
           for key, value in options.items() if isinstance(key, str)):
        raise ResourceError('Ambiguous resource selection fields')
    if options.get('device_package') or options.get('device_components'):
        raise ResourceError('Resources cannot execute a legacy model package')
    if options.get('install_mode') == 'imported':
        raise ResourceError('Imported image resource mutation is not authorized')
    if 'core:maintenance' not in options.get('plugins', []):
        raise ResourceError('Resources require retained core:maintenance')
    return copy.deepcopy(value)


def checked_parts(table):
    """Use only the prepared virtual disk; duplicate labels/nodes are ambiguous."""
    if table.get('device') != DISK or table.get('label') != 'gpt':
        raise ResourceError('The prepared resource target must be /dev/vdc GPT')
    parts, nodes = {}, set()
    for row in table.get('partitions', []):
        name, node = row.get('name'), row.get('node')
        if (not isinstance(name, str) or name in parts or not isinstance(node, str)
                or not re.fullmatch(r'/dev/vdc[1-9][0-9]*', node) or node in nodes):
            raise ResourceError('Ambiguous prepared-image partition topology')
        parts[name] = node; nodes.add(node)
    if not REQUIRED <= set(parts):
        raise ResourceError('Prepared image requires both A/B roots, vars and shared home')
    return parts


class PreparedMounts:
    """Mount both slots together and a single shared-home transaction domain."""
    def __init__(self, parts, *, readonly, base='/mnt/boiler-resource-target', run=subprocess.run,
                 output=subprocess.check_output, etc=mount_etc, opt=mount_opt, efi=False):
        self.parts, self.readonly, self.base = parts, readonly, Path(base)
        self.efi=efi
        if efi and not {'efi-A','efi-B'}<=set(parts):raise ResourceError('Both slot ESPs are required for selected boot resources')
        self.run, self.output, self.etc, self.opt = run, output, etc, opt
        self.mounts, self.unprotected = [], []
        self.opt_offload = {}
        self.roots = {slot: self.base / slot for slot in ('A', 'B')}
        self.home = self.base / 'shared-home'

    def command(self, *args):
        self.run(list(args), check=True)

    def mount(self, source, target, *flags, external=False):
        target = Path(target)
        if any(p.is_symlink() for p in (target, *target.parents)):
            raise ResourceError('Redirected target mountpoint: ' + str(target))
        if external:
            target.mkdir(parents=True, exist_ok=True)
        if not target.is_dir() or target.is_mount():
            raise ResourceError('Missing or redirected target mountpoint: ' + str(target))
        self.command('mount', *flags, str(source), str(target))
        self.mounts.append(target)

    def open(self):
        self.mount(self.parts['home'], self.home, '-t', 'ext4',
                   *(['-o', 'ro,noload'] if self.readonly else []), external=True)
        for slot, root in self.roots.items():
            self.mount(self.parts['rootfs-' + slot], root, '-t', 'btrfs',
                       *(['-o', 'ro,rescue=nologreplay'] if self.readonly else []), external=True)
            if self.output(['btrfs', 'property', 'get', '-ts', str(root), 'ro'], text=True).strip() != 'ro=true':
                raise ResourceError('Prepared root protection missing: ' + slot)
            if not self.readonly:
                self.unprotected.append(root)
                self.command('btrfs', 'property', 'set', '-ts', str(root), 'ro', 'false')
            self.mount(self.parts['var-' + slot], root / 'var', '-t', 'ext4',
                       *(['-o', 'ro,noload'] if self.readonly else []))
            if self.efi:
                from maintenance.resource_targets import _check_vfat_mounts
                node=self.parts['efi-'+slot]
                _check_vfat_mounts(node,run=self.run)
                self.mount(node,root/'efi','-t','vfat','-o',('ro,' if self.readonly else '')+'fmask=0133,dmask=0022')
                _check_vfat_mounts(node,run=self.run)
            self.mount(self.home, root / 'home', '--bind')
            if self.readonly:
                self.command('mount', '-o', 'remount,bind,ro', str(root / 'home'))
            self.etc(root, self.mounts, readonly=self.readonly)
            self.opt_offload[slot] = bool(self.opt(root, self.mounts, readonly=self.readonly))
        if len(set(self.opt_offload.values())) != 1:
            raise ResourceError('Prepared slot /opt offload mounts disagree')
        return self

    def close(self):
        errors = []
        # Restore every root even when one protection or unmount operation fails.
        for root in reversed(self.unprotected):
            try: self.command('btrfs', 'property', 'set', '-ts', str(root), 'ro', 'true')
            except Exception as error: errors.append(str(error))
        for path in reversed(self.mounts):
            try: self.command('umount', str(path))
            except Exception as error: errors.append(str(error))
        if errors:
            raise ResourceError('Resource cleanup incomplete; preserve prepared image: ' + '; '.join(errors))

    def __enter__(self):
        try: return self.open()
        except BaseException as original:
            try: self.close()
            except Exception as cleanup: raise cleanup from original
            raise

    def __exit__(self, kind, value, traceback):
        self.close()


def unavailable_authorization(*args, **kwargs):
    raise ResourceError('Resource deployment blocked: authenticated pool/session and physical '
                        'hardware facts transport under the existing target lease are unavailable')


def execute(payload, options, *, readonly=False, authorize=None):
    return _execute(payload, options, readonly=readonly, authorize=authorize, development_session=False)


def execute_local_session(payload, options, *, readonly=False):
    """Fixed development entry; external host launch commitment is mandatory."""
    from resource_local_session import local_authorizer
    return _execute(payload, options, readonly=readonly,
                    authorize=local_authorizer(readonly=readonly), development_session=True)


def execute_local_entrypoint(payload, options, *, readonly=False):
    """Resource-only entry for an image with the exact retained core already installed."""
    from resource_local_session import local_authorizer
    return _execute(payload, options, readonly=readonly, authorize=local_authorizer(readonly=readonly),
                    development_session=True, entry=True)


def _execute(payload, options, *, readonly, authorize, development_session, entry=False):
    """Trusted embedder must supply authorize(payload, reviewed_selection, disk).

    It is a context manager that keeps the existing host target lease alive and
    returns an object with pool (authenticated ResourcePool), hardware (trusted
    physical facts), and revalidate(disk, selection, partition_table). This must
    authenticate exact catalog bytes, selected resources, prepared source/image
    identity and target ownership. It must reject local-test UI trust unless an
    explicit, separately implemented local-test authorization policy applies.
    No provider can be loaded from payload, options, model metadata or env vars.
    """
    reviewed = selection(options)
    provider = authorize or unavailable_authorization
    # Fail closed before probing/mounting, and never create a replacement lease.
    with provider(Path(payload), copy.deepcopy(reviewed), DISK) as authorization:
        if authorization.pool.local_test and not (development_session and authorization.mode == 'development-session'):
            raise ResourceError('Production resource coordinator requires an authenticated resource pool')
        models = [m for m in authorization.pool.catalog['models'] if m['id'] == reviewed['model_id']]
        if len(models) != 1:
            raise ResourceError('Authenticated model reference is not unique')
        references = models[0]['resources']
        original_references = references
        from maintenance.hdr_resources import bind as bind_hdr, preflight as hdr_preflight, check_legacy
        execution_pool, references = bind_hdr(authorization.pool, references, reviewed['components'])
        identities = [r['id'] for r in resolve(execution_pool, references, reviewed['components'])]
        if set(identities) != set(reviewed['components']):
            raise ResourceError('The host-reviewed selection must explicitly contain its full dependency closure')
        facts = copy.deepcopy(authorization.hardware)
        if not isinstance(facts, dict) or not facts or facts.get('manufacturer') == 'QEMU':
            raise ResourceError('Authenticated physical hardware facts are required; guest QEMU is not the handheld')
        sources, templates = {}, {}
        if entry:
            from resource_results import entry_options, retained_core
            generic = entry_options(options); core = retained_core(payload)
        if Path('/sys/class/dmi/id/sys_vendor').read_text().strip() != 'QEMU':
            raise ResourceError('Managed VM required')
        if Path('/sys/class/block/vdc/ro').read_text().strip() != ('1' if readonly else '0'):
            raise ResourceError('Prepared virtual block protection differs from requested action')
        table = json.loads(subprocess.check_output(['sfdisk', '--json', DISK]))['partitiontable']
        parts = checked_parts(table)
        authorization.revalidate(DISK, copy.deepcopy(reviewed), copy.deepcopy(table))

        def session(mounts, read_only):
            from integration import release, install_plan
            slots = {}
            for slot, root in mounts.roots.items():
                source = release(root)
                sources[slot] = source
                if entry:
                    templates[slot] = install_plan(generic, source, slot)
                    templates[slot]['maintenance']['version'] = core['version']
                environment = dict(architecture='x86_64', os_id=source['ID'],
                                   os_version=source['VERSION_ID'], kernels=source['KERNELS'])
                slots[slot] = dict(root=root, environment=environment, hardware=copy.deepcopy(facts),
                                   opt_offload=mounts.opt_offload[slot])
            if not readonly:
                hdr_preflight(authorization.pool, original_references, reviewed['components'], slots)
                check_legacy(mounts.home, reviewed['components'])
            value = ResourceSession(execution_pool, slots, mounts.home, read_only=read_only)
            if entry:
                from resource_results import require_matching_core
                require_matching_core(value, core)
                return value
            # An options checkbox is not proof the retained maintenance engine
            # was installed successfully in both slot domains.
            from maintenance.engine import Engine
            for root in mounts.roots.values():
                proof = Engine(root, read_only=True).verify_readonly('core:maintenance')
                if not proof['ok']:
                    raise ResourceError('Retained maintenance verification failed')
            return value

        # Readonly preflight never creates state dirs or unlocks a root. Payload,
        # hardware, compatibility, ownership and cross-resource overlaps are all
        # checked across both slots before the first writable transaction.
        with PreparedMounts(parts, readonly=True) as mounts:
            candidate = session(mounts, True)
            prepared = candidate.prepare(references, reviewed['components'])
            if entry:
                from resource_results import require_clean, reject_state_targets, preflight_metadata
                require_clean(candidate); reject_state_targets(prepared); preflight_metadata(mounts.roots)
            if readonly:
                proof = candidate.verify(identities)
                if not proof or any(not row['ok'] for row in proof):
                    raise ResourceError('Readonly resource verification failed: ' + json.dumps(proof))
                result = dict(status='files-verified-readonly', runtime='not_validated', verification=proof)
                if entry:
                    from resource_results import receipt_readonly, documents, verify_records
                    receipt = receipt_readonly(candidate, mounts.roots, prepared)
                    rows = documents(options, sources, core, prepared, receipt, templates, session=candidate)
                    verify_records(mounts.roots, rows); result['slot_results'] = rows
                return result
        current = json.loads(subprocess.check_output(['sfdisk', '--json', DISK]))['partitiontable']
        if current != table:
            raise ResourceError('Prepared partition topology changed after readonly preflight')
        checked_parts(current)
        authorization.revalidate(DISK, copy.deepcopy(reviewed), copy.deepcopy(current))
        with PreparedMounts(parts, readonly=False) as mounts:
            candidate = session(mounts, False)
            # Engine journals retain partial completion; never silently roll back
            # shared data or claim all-or-nothing A/B disk atomicity.
            receipt = candidate.install(references, reviewed['components'])
            proof = candidate.verify(identities)
            if not proof or any(not row['ok'] for row in proof):
                raise ResourceError('Committed resource files failed verification; retain journals: ' + json.dumps(proof))
            result = dict(status='files-installed', runtime='not_validated', receipt=receipt, verification=proof)
            if entry:
                from resource_results import documents, publish, verify_records
                rows = documents(options, sources, core, prepared, receipt, templates, session=candidate)
                publish(mounts.roots, rows); verify_records(mounts.roots, rows); result['slot_results'] = rows
        return result
