import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]/'adaptations/brands/onexplayer/oxp3/current/upstream-20261029'


def module(name):
    spec = importlib.util.spec_from_file_location(name, SOURCE/(name+'.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


dock = module('dock_input')
speaker = module('speaker_route')


class ConnectorTests(unittest.TestCase):
    TEMPLATE = (b'kind: CompositeDevice\nname: ONEXPLAYER 3\nsource_devices:\n'
                b'  - group: gamepad\n    evdev:\n      name: pad\n'
                b'  - group: imu\n    iio:\n      name: bmi260\n'
                b'options:\n  auto_manage: true\n# calibration retained\n')

    def test_adds_only_connector_keyboard_and_is_idempotent(self):
        result = dock.add_connector_keyboard(self.TEMPLATE)
        self.assertEqual(result.replace(dock.BLOCK.encode(), b''), self.TEMPLATE)
        self.assertEqual(dock.add_connector_keyboard(result), result)
        self.assertNotIn(b'product_id: 0xfe02', result)

    def test_crlf_preserved(self):
        original = self.TEMPLATE.replace(b'\n', b'\r\n')
        result = dock.add_connector_keyboard(original)
        self.assertEqual(result.replace(dock.BLOCK.replace('\n', '\r\n').encode(), b''), original)

    def test_custom_connector_and_duplicate_source_refused(self):
        with self.assertRaises(ValueError):
            dock.add_connector_keyboard(self.TEMPLATE.replace(b'name: pad', b'name: HID 1a86:fe02'))
        with self.assertRaises(ValueError):
            dock.add_connector_keyboard(self.TEMPLATE + b'source_devices:\n')


class FakeAudio:
    def __init__(self, active='HiFi (Speaker)', failure='', jack='off', changed=None):
        self.card = {'name': 'alsa_card.pci-sof', 'active_profile': active,
                     'properties': {'alsa.card': '0'},
                     'profiles': {'HiFi (Speaker)': {'available': 'yes'},
                                  'HiFi (Headphones)': {'available': 'yes'}}}
        self.failure, self.jack, self.changed = failure, jack, changed
        self.calls, self.reads = [], 0

    def __call__(self, *args):
        self.calls.append(args)
        if args[0].endswith('systemctl'):
            return '127\n'
        if args[0].endswith('journalctl'):
            return self.failure
        if args[0].endswith('amixer'):
            return '  : values='+self.jack+'\n'
        if 'list' in args:
            self.reads += 1
            card = dict(self.card)
            if self.reads > 1 and self.changed:
                card['active_profile'] = self.changed
            return json.dumps([card])
        return ''

    def writes(self):
        return [call for call in self.calls if 'set-card-profile' in call]


class SpeakerTests(unittest.TestCase):
    def check(self, audio, codec=True):
        return speaker.check(12345, command=audio, codec=lambda _: codec, pause=lambda _: None)

    def test_correct_route_without_failure_is_untouched(self):
        audio = FakeAudio()
        self.assertEqual(self.check(audio), [])
        self.assertEqual(audio.writes(), [])

    def test_confirmed_speaker_ucm_failure_retries_current_profile(self):
        audio = FakeAudio(failure='Failed to enable UCM device Speaker: Input/output error')
        self.assertEqual(self.check(audio), [('alsa_card.pci-sof', 'HiFi (Speaker)')])
        self.assertEqual([row[-1] for row in audio.writes()], ['off', 'HiFi (Speaker)'])
        journal = next(row for row in audio.calls if row[0].endswith('journalctl'))
        self.assertIn('_PID=127', journal)
        self.assertIn('@12345', journal)

    def test_failed_reenable_restores_original_and_reports_failure(self):
        audio = FakeAudio(failure='Failed to enable UCM device Speaker: Input/output error')
        count = [0]
        def command(*args):
            result = audio(*args)
            if 'set-card-profile' in args:
                count[0] += 1
                if count[0] == 2:
                    return None
            return result
        with self.assertRaisesRegex(RuntimeError, 'Could not select audio profile'):
            speaker.check(12345, command=command, codec=lambda _: True, pause=lambda _: None)
        self.assertEqual([row[-1] for row in audio.writes()],
                         ['off', 'HiFi (Speaker)', 'HiFi (Speaker)'])

    def test_failed_off_is_not_reported_as_success(self):
        audio = FakeAudio(failure='Failed to enable UCM device Speaker')
        def command(*args):
            result = audio(*args)
            return None if 'set-card-profile' in args else result
        with self.assertRaises(RuntimeError):
            speaker.check(12345, command=command, codec=lambda _: True, pause=lambda _: None)
        self.assertEqual([row[-1] for row in audio.writes()], ['off'])

    def test_microphone_failure_is_not_a_speaker_retry(self):
        audio = FakeAudio(failure='Failed to enable UCM device Mic')
        self.assertEqual(self.check(audio), [])
        self.assertEqual(audio.writes(), [])

    def test_empty_jack_repairs_wrong_headphones_route(self):
        audio = FakeAudio(active='HiFi (Headphones)')
        self.check(audio)
        self.assertEqual([row[-1] for row in audio.writes()], ['HiFi (Speaker)'])

    def test_plugged_headphones_unrelated_codec_and_changed_route_preserved(self):
        for audio, codec in ((FakeAudio(active='HiFi (Headphones)', jack='on'), True),
                             (FakeAudio(active='HiFi (Headphones)'), False),
                             (FakeAudio(active='HiFi (Headphones)', changed='off'), True)):
            self.assertEqual(self.check(audio, codec), [])
            self.assertEqual(audio.writes(), [])

    def test_ambiguous_speaker_profiles_are_preserved(self):
        audio = FakeAudio(active='HiFi (Headphones)')
        audio.card['profiles']['HiFi (Speaker2)'] = {'available': 'yes'}
        self.assertEqual(self.check(audio), [])

    def test_codec_and_dmi_require_exact_local_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codec = root/'proc/asound/card0/codec#0'
            codec.parent.mkdir(parents=True)
            codec.write_text('Codec: Realtek ALC245\n')
            self.assertTrue(speaker.local_codec('0', root))
            self.assertFalse(speaker.local_codec('../0', root))
            self.assertFalse(speaker.supported(root))
            dmi = root/'sys/class/dmi/id'
            dmi.mkdir(parents=True)
            (dmi/'sys_vendor').write_text('ONE-NETBOOK')
            (dmi/'product_name').write_text('ONEXPLAYER 3')
            self.assertTrue(speaker.supported(root))

    def test_explicit_install_targets_are_new_and_complete(self):
        install = json.loads((SOURCE/'install.json').read_bytes())
        targets = [row['target'] for row in install['files']]
        self.assertEqual(len(targets), len(set(targets)))
        self.assertEqual(len(targets), 6)
        self.assertTrue(all('oxp3-speaker-' in target for target in targets))
        for row in install['files']:
            self.assertTrue((SOURCE/row['source']).is_file())
            self.assertEqual(row['mode'], 0o644)

    def test_resume_uses_sleep_stop_hook_without_target_ordering_cycle(self):
        unit = (SOURCE/'oxp3-speaker-resume.service').read_text()
        self.assertIn('Before=sleep.target', unit)
        self.assertIn('DefaultDependencies=no', unit)
        self.assertIn('StopWhenUnneeded=yes', unit)
        self.assertIn('RemainAfterExit=yes', unit)
        self.assertIn('ExecStart=/usr/bin/true', unit)
        self.assertIn('ExecStop=-/usr/bin/systemctl --user -M deck@ start --no-block oxp3-speaker-route.service', unit)
        self.assertNotIn('After=', unit)
        install = json.loads((SOURCE/'install.json').read_bytes())
        triggers = [row['target'] for row in install['files'] if 'system/' in row['target'] and '.target.d/' in row['target']]
        self.assertEqual(triggers, ['etc/systemd/system/sleep.target.d/20-oxp3-speaker-resume.conf'])

    def test_session_target_does_not_wait_for_audio_polling(self):
        unit = (SOURCE/'oxp3-speaker-route.service').read_text()
        self.assertIn('Type=exec', unit)
        self.assertNotIn('Type=oneshot', unit)
        self.assertIn('ExecStart=/usr/bin/python3 -I -B', unit)


if __name__ == '__main__':
    unittest.main()
