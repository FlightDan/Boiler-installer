import base64
from contextlib import redirect_stderr
import hashlib
from io import BytesIO, StringIO
import json
from pathlib import Path
import runpy
import shutil
import socket
import struct
import sys
import tempfile
import unittest
import zlib
from urllib.error import URLError
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'components'))
from payload_lock import record
from wireless import plan_required, resolve

NS = runpy.run_path(str(ROOT / 'scripts/download-firmware.py'))


def blob_pack(data):
    size = len(data)
    first = 0x30 | (size & 15); size >>= 4
    header = bytearray([first | (0x80 if size else 0)])
    while size:
        byte = size & 127; size >>= 7
        header.append(byte | (0x80 if size else 0))
    pack = b'PACK' + struct.pack('>II', 2, 1) + bytes(header) + zlib.compress(data)
    return b'0008NAK\n' + pack + hashlib.sha1(pack).digest()


class OnDemandFirmwareChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.base = Path(self.tmp.name)
        self.root = self.base / 'root'; self.root.mkdir()
        module = self.root / 'usr/lib/modules/test/kernel/drivers/net/wireless/iwlwifi.ko'
        module.parent.mkdir(parents=True); module.write_bytes(b'kernel')
        self.locks = self.base / 'locks'; self.locks.mkdir()
        self.core = self.base / 'core'; self.core.mkdir()
        self.calls = []; self.blobs = {}
        def item(source, data, target):
            self.blobs[source] = data
            blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
            return record(source, data, target=target, git_blob=blob, url=NS['BASE'] + source)
        self.files = [item('intel/iwlwifi/' + name, data, 'usr/lib/firmware/' + name)
                      for name, data in [('needed.ucode', b'needed'), ('unrelated.ucode', b'unrelated'), ('alias.ucode', b'needed')]]
        self.notices = [item('LICENSES/test', b'license', 'licenses/test')]
        self.lock = dict(schema=1, kind='firmware-only', vendor='intel', version='pinned', files=self.files,
                         licenses=self.notices, provenance=dict(url=NS['BASE'], revision=NS['REVISION'], license='test'))
        (self.locks / 'intel.json').write_text(json.dumps(self.lock))
        prefix = 'wireless/intel/'
        self.bundle = dict(self.lock,
                           files=[dict(entry, source=prefix + 'firmware/' + entry['target'].removeprefix('usr/lib/firmware/')) for entry in self.files],
                           licenses=[dict(entry, source=prefix + entry['target']) for entry in self.notices])
        self.catalog = dict(schema=1, components={'wifi:intel': self.bundle})
        self.catalog_path = self.base / 'catalog.json'; self.catalog_path.write_text(json.dumps(self.catalog))
        self.options = dict(schema=1, wireless=['intel'], repairs=[], plugins=[])

    def tearDown(self):
        from protocol.manifest import _filesystem_path
        owned=Path(self.tmp.name).resolve()
        self.assertEqual(owned,self.base.resolve())
        if owned.exists():
            shutil.rmtree(_filesystem_path(owned))
        self.tmp.cleanup()

    def plan(self, requirements=('needed.ucode',), options=None):
        options = self.options if options is None else options
        vendors = options.get('wireless', [])
        records, plans = plan_required(self.root, self.catalog['components'], vendors, lambda _: requirements)
        result = dict(schema=1, catalog_sha256=hashlib.sha256(self.catalog_path.read_bytes()).hexdigest(), selection=options,
                      slots={slot: dict(records=records, plans=plans) for slot in ('A', 'B')})
        path = self.base / 'plan.json'; path.write_text(json.dumps(result))
        return result, path

    def fetch(self, item):
        self.calls.append(item['source']); return self.blobs[item['source']]

    def test_deep_retained_model_core_is_materialized_atomically_with_normal_display_path(self):
        from protocol.manifest import _filesystem_path
        source='maintenance-runtime/cache/model-bundles/packages/oxp3/payload/legacy/integration/' + 'nested-source-'*4 + '/input.patch'
        data=b'locked retained model source\n'
        original=_filesystem_path(self.core/source)
        original.parent.mkdir(parents=True,exist_ok=True); original.write_bytes(data)
        self.catalog['components']['core:maintenance']=dict(available=True,files=[record(source,data)])
        self.catalog_path.write_text(json.dumps(self.catalog))
        plan=dict(schema=1,catalog_sha256=hashlib.sha256(self.catalog_path.read_bytes()).hexdigest(),
                  selection=dict(schema=1,repairs=[],plugins=['core:maintenance']),slots={})
        plan_path=self.base/'deep-plan.json';plan_path.write_text(json.dumps(plan))
        destination=self.base/('deep-private-payload-'+'x'*65)/'payload'
        self.assertGreater(len(str(destination/'integration'/source)),260)
        output=NS['download'](self.catalog_path,plan_path,destination,self.core,self.base/'cache',self.locks,self.fetch)
        self.assertEqual(destination,output)
        self.assertFalse(str(output).startswith('\\\\?\\'))
        self.assertEqual(data,_filesystem_path(output/'integration'/source).read_bytes())
        self.assertEqual([],self.calls)
        with self.assertRaises(FileExistsError):
            NS['download'](self.catalog_path,plan_path,destination,self.core,self.base/'cache',self.locks,self.fetch)

    def download(self, path, name='output', fetch=None):
        return NS['download'](self.catalog_path, path, self.base / name, self.core, self.base / 'cache', self.locks, fetch or self.fetch)

    def test_no_selection_opens_no_firmware_and_downloads_nothing(self):
        _, path = self.plan(options=dict(schema=1, wireless=[], repairs=[], plugins=[]))
        output = self.download(path)
        self.assertEqual([], self.calls)
        self.assertFalse((output / 'integration/wireless').exists())
        self.assertEqual(self.catalog_path.read_bytes(), (output / 'integration/manifest.json').read_bytes())

    def test_host_empty_slots_plan_needs_no_vm_or_download(self):
        plan, path = self.plan(options=dict(schema=1, wireless=[], repairs=[], plugins=[]))
        plan['slots'] = {}
        path.write_text(json.dumps(plan))
        output = self.download(path)
        self.assertEqual([], self.calls)
        self.assertTrue((output / 'integration/manifest.json').is_file())

    def test_empty_selection_does_not_open_network(self):
        _, path = self.plan(options=dict(schema=1, wireless=[], repairs=[], plugins=[]))
        with patch('urllib.request.build_opener', side_effect=AssertionError('Unexpected network access')):
            NS['download'](self.catalog_path, path, self.base / 'output', self.core, self.base / 'cache', self.locks)

    def test_only_one_missing_file_and_its_license_are_downloaded(self):
        _, path = self.plan()
        output = self.download(path)
        self.assertEqual({'intel/iwlwifi/needed.ucode', 'LICENSES/test'}, set(self.calls))
        self.assertEqual(b'needed', (output / 'integration' / self.bundle['files'][0]['source']).read_bytes())
        self.assertFalse((output / 'integration' / self.bundle['files'][1]['source']).exists())
        self.assertEqual(self.catalog_path.read_bytes(), (output / 'integration/manifest.json').read_bytes())

    def test_existing_image_file_needs_no_payload_or_license_bytes(self):
        native = self.root / 'usr/lib/firmware/needed.ucode.zst'; native.parent.mkdir(parents=True); native.write_bytes(b'native')
        with patch('wireless.checked', side_effect=AssertionError('Must not read any payload bytes')):
            writes, plans = resolve(self.root, self.base / 'absent-payload', self.catalog['components'], ['intel'], lambda _: ['needed.ucode'])
            self.assertEqual({}, writes)
            self.assertTrue(plans['wifi:intel']['preserved'])
            _, path = self.plan()
        self.download(path)
        self.assertEqual([], self.calls)

    def test_only_selected_core_files_and_notices_are_copied(self):
        data = b'decky'; notice = b'notice'
        entries = [record('core/decky', data, target='usr/local/bin/decky')]
        notices = [record('core/NOTICE', notice, target='licenses/NOTICE')]
        self.catalog['components'].update({'core:decky': dict(available=True, files=entries, notices=notices),
                                          'core:maintenance': dict(available=True, files=[record('missing', b'absent', target='usr/bin/maintenance')])})
        for entry, content in [(entries[0], data), (notices[0], notice)]:
            path = self.core / entry['source']; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
        self.catalog_path.write_text(json.dumps(self.catalog))
        _, path = self.plan(options=dict(schema=1, wireless=[], repairs=[], plugins=['core:decky']))
        output = self.download(path)
        self.assertEqual(data, (output / 'integration/core/decky').read_bytes())
        self.assertEqual(notice, (output / 'integration/core/NOTICE').read_bytes())
        self.assertEqual([], self.calls)

    def test_cancelled_download_removes_unpublished_temporary_payload(self):
        _, path = self.plan()
        def cancel(_): raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): self.download(path, fetch=cancel)
        self.assertFalse((self.base / 'output').exists())
        self.assertEqual([], list(self.base.glob('.output-*')))

    def test_three_dns_failures_report_the_missing_source_without_publishing_payload(self):
        _, path = self.plan()
        source = 'intel/iwlwifi/needed.ucode'
        script = ROOT / 'scripts/download-firmware.py'
        arguments = [str(script), '--catalog', str(self.catalog_path), '--plan', str(path),
                     '--output', str(self.base / 'output'), '--core-payload', str(self.core),
                     '--cache', str(self.base / 'cache'), '--locks', str(self.locks)]
        stderr = StringIO()
        with patch('urllib.request.build_opener') as opener, patch('sys.argv', arguments), redirect_stderr(stderr):
            request = opener.return_value.open
            request.side_effect = [URLError(socket.gaierror(11001, 'getaddrinfo failed')) for _ in range(3)]
            with self.assertRaisesRegex(OSError, r'needed\.ucode.*tuna\.tsinghua\.edu\.cn.*gitlab\.com.*kernel\.googlesource\.com'):
                runpy.run_path(str(script), run_name='__main__')
        self.assertEqual('BOILER_FIRMWARE_SOURCES_UNAVAILABLE=1\n', stderr.getvalue())
        self.assertEqual(NS['TUNA'], request.call_args_list[0].args[0].full_url)
        self.assertEqual([NS['MIRROR'] + source, NS['BASE'] + source + '?format=TEXT'],
                         [call.args[0] for call in request.call_args_list[1:]])
        self.assertFalse((self.base / 'output').exists())
        self.assertEqual([], list(self.base.glob('.output-*')))

    def test_dns_failure_on_mirror_can_use_fixed_origin(self):
        item = self.files[0]
        with patch('urllib.request.build_opener') as opener:
            request = opener.return_value.open
            response = request.return_value.__enter__.return_value
            response.read.return_value = base64.b64encode(b'needed')
            request.side_effect = [URLError('TUNA unavailable'), URLError('GitLab unavailable'), request.return_value]
            data = NS['fetch_bytes'](item)
        self.assertEqual(b'needed', NS['verified'](data, item))

    def test_tuna_blob_is_requested_and_verified(self):
        item = self.files[0]
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = BytesIO(blob_pack(b'needed'))
            data = NS['fetch_bytes'](item)
            request = opener.return_value.open.call_args.args[0]
        self.assertEqual(b'needed', NS['verified'](data, item))
        self.assertEqual(NS['TUNA'], request.full_url)
        self.assertEqual(NS['_pkt'](('want ' + item['git_blob'] + ' no-progress\n').encode()) + b'0000' + NS['_pkt'](b'done\n'), request.data)

    def test_unsupported_tuna_pack_falls_back_to_gitlab(self):
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = [BytesIO(b'unsupported'), BytesIO(b'needed')]
            data = NS['fetch_bytes'](self.files[0])
        self.assertEqual(b'needed', NS['verified'](data, self.files[0]))

    def test_redirect_rejection_does_not_fall_back(self):
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = ValueError('Firmware origin redirects are forbidden')
            with self.assertRaisesRegex(ValueError, 'redirects are forbidden'):
                NS['fetch_bytes'](self.files[0])
            self.assertEqual(1, opener.return_value.open.call_count)

    def test_bad_tuna_pack_or_blob_never_publishes_or_falls_back(self):
        _, path = self.plan()
        valid = blob_pack(b'needed')
        corrupt_checksum = valid[:-1] + bytes([valid[-1] ^ 1])
        cases = [corrupt_checksum, valid + b'extra', blob_pack(b'too long'),
                 blob_pack(b'wrong!'), b'0008NAK\nPACK' + b'\0' * 5,
                 b'0008NAK\nPACK' + b'x' * 10000]
        for response in cases:
            with self.subTest(response=response[:20]), patch('urllib.request.build_opener') as opener:
                opener.return_value.open.return_value = BytesIO(response)
                with self.assertRaises(ValueError):
                    NS['download'](self.catalog_path, path, self.base / 'output', self.core, self.base / 'cache', self.locks)
                self.assertEqual(1, opener.return_value.open.call_count)
                self.assertFalse((self.base / 'output').exists())
                self.assertEqual([], list(self.base.glob('.output-*')))

    def test_plan_does_not_read_unrelated_bytes_and_resolve_checks_only_needed(self):
        with patch('wireless.checked', side_effect=AssertionError('Metadata planning cannot open blobs')):
            records, _ = plan_required(self.root, self.catalog['components'], ['intel'], lambda _: ['needed.ucode'])
        self.assertEqual(['usr/lib/firmware/needed.ucode'], list(records))
        source = self.core / self.bundle['files'][0]['source']; source.parent.mkdir(parents=True); source.write_bytes(b'needed')
        writes, _ = resolve(self.root, self.core, self.catalog['components'], ['intel'], lambda _: ['needed.ucode'])
        self.assertEqual({'usr/lib/firmware/needed.ucode': b'needed'}, writes)

    def test_same_sha_alias_and_second_run_reuse_verified_cache(self):
        _, path = self.plan(('needed.ucode', 'alias.ucode'))
        self.download(path)
        self.assertEqual(2, len(self.calls))  # One content blob and one notice.
        self.calls.clear(); self.download(path, name='second')
        self.assertEqual([], self.calls)

    def test_wrong_hash_never_publishes_partial_output(self):
        _, path = self.plan()
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            self.download(path, fetch=lambda _: b'incorrect')
        self.assertFalse((self.base / 'output').exists())
        self.assertEqual([], list(self.base.glob('.output-*')))

    def test_tampered_plan_binding_record_unknown_target_and_selection_are_rejected(self):
        original, path = self.plan()
        for mutation in ('binding', 'record', 'target', 'selection'):
            value = json.loads(json.dumps(original))
            if mutation == 'binding': value['catalog_sha256'] = '0' * 64
            elif mutation == 'record': value['slots']['A']['records']['usr/lib/firmware/needed.ucode']['size'] += 1
            elif mutation == 'target': value['slots']['A']['plans']['wifi:intel']['files'] = ['usr/lib/firmware/unknown.ucode']
            else: value['selection']['wireless'] = []
            path.write_text(json.dumps(value))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): self.download(path)
        self.assertEqual([], self.calls)

    def test_catalog_url_tampering_is_rejected_even_with_updated_plan_hash(self):
        self.catalog['components']['wifi:intel']['files'][0]['url'] = 'https://untrusted.invalid/firmware'
        self.catalog_path.write_text(json.dumps(self.catalog)); _, path = self.plan()
        with self.assertRaisesRegex(ValueError, 'canonical lock'): self.download(path)
        self.assertEqual([], self.calls)

    def test_canonical_lock_url_cannot_broaden_allowed_origin(self):
        self.lock['files'][0]['url'] = self.bundle['files'][0]['url'] = 'https://untrusted.invalid/firmware'
        (self.locks / 'intel.json').write_text(json.dumps(self.lock))
        self.catalog_path.write_text(json.dumps(self.catalog)); _, path = self.plan()
        with self.assertRaisesRegex(ValueError, 'fixed upstream'): self.download(path)
        self.assertEqual([], self.calls)


if __name__ == '__main__': unittest.main()
