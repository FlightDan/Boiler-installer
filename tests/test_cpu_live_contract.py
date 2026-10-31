import copy
import importlib.util
import hashlib
import os
import sys
import unittest
from pathlib import Path

SDK = Path(__file__).resolve().parents[1]
if not (SDK / 'protocol' / 'cpu_contract.py').is_file():
    SDK = Path(__file__).resolve().parents[2] / 'source-authority-terra' / 'sdk'
sys.path.insert(0, str(SDK))
from protocol import cpu_contract as old
spec = importlib.util.spec_from_file_location('candidate_cpu_live', Path(__file__).resolve().parents[1] / 'protocol' / 'cpu_live_contract.py')
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


def resource():
    rows, inventory = [], []
    for index, (target, digest) in enumerate(old.TARGETS.items()):
        name = 'files/' + str(index)
        inventory.append({'path': name, 'sha256': digest})
        rows.append({'source': name, 'target': target, 'mode': 0o600 if target == old.CONFIG else 0o644, 'scope': 'root-slots'})
    return {'id': old.IDENTITY, 'install': {'kind': live.KIND, 'config_policy': copy.deepcopy(old.POLICY), 'files': rows},
        'payload': {'files': inventory}, 'verify': {'kind': 'cpu-live-state-v1'},
        'restore': {'kind': 'cpu-live-transaction-v1'},
        'activation': {'kind': 'fixed-public-cpu-provider-v1', 'phase': 'first-boot', 'autoload': True},
        'hardware_requires': copy.deepcopy(live.HARDWARE),
        'compatibility': dict(architectures=['x86_64'], os_ids=['steamos'], os_versions=['3.10'], kernels=[old.KERNEL]),
        'dependencies': ['resource:common-intel-rapl'], 'external_component_dependencies': ['decky']}


class ContractTests(unittest.TestCase):
    def test_valid_and_separate_from_staging(self):
        r = resource()
        self.assertIs(live.validate_contract(r), r)
        with self.assertRaises(ValueError): old.validate_contract(r)
        r['install']['kind'] = old.KIND
        with self.assertRaises(ValueError): live.validate_contract(r)

    def test_actual_public_payloads_match_pins(self):
        configured=os.environ.get('BOILER_CPU_TEST_POOL')
        if not configured:self.skipTest('An explicit public CPU pool fixture is required')
        pool=Path(configured)
        names = ('000-cpu_control.py', '001-power_control.py', '002-main.py', '003-index.js',
                 '004-oxp3-cpu.service', '005-plugin.json', '006-package.json', '007-config.json')
        for name, digest in zip(names, old.TARGETS.values()):
            self.assertEqual(hashlib.sha256((pool / name).read_bytes()).hexdigest(), digest)
    def test_reject_tampering(self):
        def digest(r): r['payload']['files'][0]['sha256'] = '0' * 64
        def mode(r): r['install']['files'][0]['mode'] = 0o600
        def target(r): r['install']['files'][0]['target'] = 'arbitrary'
        def deps(r): r['dependencies'] = []
        def activation(r): r['activation']['autoload'] = 1
        def facts(r): r['hardware_requires'].update(old.HARDWARE)
        def bool_fact(r): r['hardware_requires']['intel_pstate'] = [1]
        def duplicate(r): r['payload']['files'].append(copy.deepcopy(r['payload']['files'][0]))
        def hooks(r): r['hooks'] = ['script']
        for change in (digest, mode, target, deps, activation, facts, bool_fact, duplicate, hooks):
            with self.subTest(change=change.__name__):
                r = resource(); change(r)
                with self.assertRaises(ValueError): live.validate_contract(r)

if __name__ == '__main__': unittest.main()


