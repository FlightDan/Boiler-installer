import copy, unittest
from types import SimpleNamespace
from maintenance import cpu_live_resources as live, cpu_resources as old
from protocol.cpu_contract import TARGETS, CONFIG, IDENTITY, POLICY


def component():
    return {'id':IDENTITY, 'cpu_live_contract':copy.deepcopy(live.MARKER),
            'cpu_mutable_config':copy.deepcopy(POLICY),
            'files':[{'target':target,'sha256':sha,'mode':0o600 if target==CONFIG else 0o644}
                     for target,sha in TARGETS.items()]}

class LivePermitTests(unittest.TestCase):
    def test_fixed_and_separate_from_staging(self):
        value=component();self.assertIs(live.fixed_component(value),value)
        with self.assertRaises(ValueError):old.fixed_component(value)
        with self.assertRaises(ValueError):live._CpuLiveFilePermit(object(),None,None,None,None,None,None)
        self.assertFalse(old.permitted(SimpleNamespace(root=None),None,value,recovery=False))
    def test_tampered_component(self):
        for mutation in ('marker','target','mode','digest','hooks'):
            value=component()
            if mutation=='marker':value['cpu_live_contract']['public_commit']='bad'
            if mutation=='target':value['files'][0]['target']='var/lib/other'
            if mutation=='mode':value['files'][0]['mode']=0o600
            if mutation=='digest':value['files'][0]['sha256']='0'*64
            if mutation=='hooks':value['hooks']=['bad']
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):live.fixed_component(value)

if __name__ == '__main__':unittest.main()
