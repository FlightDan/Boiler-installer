"""Retry only transient Windows directory publication failures."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protocol import local, manifest


def denial(code=5):
    error = OSError('simulated Windows failure')
    error.winerror = code
    return error


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'staged'
        self.source.mkdir()
        self.destination = self.root/'published'

    def test_immediate_and_transient_success(self):
        for failures in (0, 1):
            operation = mock.Mock(side_effect=[denial()] * failures + [None])
            with mock.patch.object(manifest, '_windows_publication', return_value=True), \
                 mock.patch.object(manifest.time, 'sleep') as sleep:
                manifest._publish_directory(self.source, self.destination, operation)
            self.assertEqual(operation.call_count, failures + 1)
            self.assertEqual(sleep.call_count, failures)

    def test_persistent_failure_returns_first_error(self):
        first = denial()
        operation = mock.Mock(side_effect=[first] + [denial() for _ in range(5)])
        with mock.patch.object(manifest, '_windows_publication', return_value=True), \
             mock.patch.object(manifest.time, 'sleep') as sleep:
            with self.assertRaises(OSError) as caught:
                manifest._publish_directory(self.source, self.destination, operation)
        self.assertIs(caught.exception, first)
        self.assertEqual(operation.call_count, 6)
        self.assertEqual(sleep.call_count, 5)

    def test_other_errors_and_non_windows_are_immediate(self):
        for windows, code in ((True, 32), (False, 5)):
            operation = mock.Mock(side_effect=denial(code))
            with mock.patch.object(manifest, '_windows_publication', return_value=windows), \
                 mock.patch.object(manifest.time, 'sleep') as sleep:
                with self.assertRaises(OSError):
                    manifest._publish_directory(self.source, self.destination, operation)
            self.assertEqual(operation.call_count, 1)
            sleep.assert_not_called()

    def test_destination_appears_during_delay_and_is_preserved(self):
        operation = mock.Mock(side_effect=denial())
        def appear(_):
            self.destination.write_text('keep')
        with mock.patch.object(manifest, '_windows_publication', return_value=True), \
             mock.patch.object(manifest.time, 'sleep', side_effect=appear):
            with self.assertRaises(manifest.ProtocolError):
                manifest._publish_directory(self.source, self.destination, operation)
        self.assertEqual(self.destination.read_text(), 'keep')
        self.assertEqual(operation.call_count, 1)

    def test_extract_retries_replace_and_cleans_up_on_failure(self):
        archive = self.root/'source.zip'
        with zipfile.ZipFile(archive, 'w') as bundle:
            bundle.writestr('file.txt', 'payload')
        real_replace = os.replace
        for persistent in (False, True):
            attempts = []
            def replace(source, dest):
                attempts.append(1)
                if persistent or len(attempts) == 1:
                    raise denial()
                return real_replace(source, dest)
            operation = mock.Mock(side_effect=replace)
            with mock.patch.object(manifest, '_windows_publication', return_value=True), \
                 mock.patch.object(manifest.time, 'sleep'), mock.patch.object(manifest.os, 'replace', operation):
                if persistent:
                    with self.assertRaises(OSError): manifest.safe_extract_archive(archive, self.destination)
                else:
                    manifest.safe_extract_archive(archive, self.destination)
            self.assertFalse(list(self.root.glob('.boiler-extract-*')))
            if not persistent:
                self.assertEqual((self.destination/'file.txt').read_text(), 'payload')
                (self.destination/'file.txt').unlink()
                self.destination.rmdir()

    def test_local_import_rechecks_guard_and_cleans_staging(self):
        script = self.root/'fixture.sh'
        script.write_bytes(b'#!/bin/bash\n# boiler-script: 1\nexit 0\n')
        real_rename, real_guard = os.rename, local._safe_path
        attempts, checks = [], []
        def rename(source, dest):
            attempts.append(1)
            if len(attempts) == 1: raise denial()
            return real_rename(source, dest)
        def guard(path):
            checks.append(path)
            return real_guard(path)
        with mock.patch.object(manifest, '_windows_publication', return_value=True), \
             mock.patch.object(manifest.time, 'sleep'), mock.patch.object(local.os, 'rename', side_effect=rename), \
             mock.patch.object(local, '_safe_path', side_effect=guard):
            local.import_local(script, self.destination, developer=True)
        self.assertEqual(len(attempts), 2)
        self.assertGreaterEqual(sum(str(path).casefold().endswith(str(self.destination).casefold()) for path in checks), 3)
        self.assertTrue((self.destination/'manifest.json').is_file())
        self.assertFalse(list(self.root.glob('.local-device-*')))

    def test_local_new_unsafe_path_stops_retry_and_cleans_staging(self):
        script = self.root/'fixture.sh'
        script.write_bytes(b'#!/bin/bash\n# boiler-script: 1\nexit 0\n')
        real_guard = local._safe_path
        checks = []
        def guard(path):
            if str(path).casefold().endswith(str(self.destination).casefold()):
                checks.append(path)
                if len(checks) == 3: raise manifest.ProtocolError('new unsafe path')
            return real_guard(path)
        with mock.patch.object(manifest, '_windows_publication', return_value=True), \
             mock.patch.object(manifest.time, 'sleep'), mock.patch.object(local.os, 'rename', side_effect=denial()) as rename, \
             mock.patch.object(local, '_safe_path', side_effect=guard):
            with self.assertRaisesRegex(manifest.ProtocolError, 'new unsafe path'):
                local.import_local(script, self.destination, developer=True)
        self.assertEqual(rename.call_count, 1)
        self.assertFalse(self.destination.exists())
        self.assertFalse(list(self.root.glob('.local-device-*')))

if __name__ == '__main__': unittest.main()
