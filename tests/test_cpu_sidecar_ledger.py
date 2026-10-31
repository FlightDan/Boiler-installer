import copy, unittest
from maintenance import cpu_sidecar_ledger as ledger
GEN='a'*32; TX='e'*32; BOOT='12345678-1234-1234-1234-123456789abc'; INV='b'*32

def values(mhz,epp):
    result={}
    for index in range(14):
        base='/sys/devices/system/cpu/cpufreq/policy'+str(index)+'/'
        result[base+'scaling_max_freq']=str(mhz)
        result[base+'energy_performance_preference']=epp
    return result

class LedgerTests(unittest.TestCase):
    def test_wal_before_completion_and_restart_first_before(self):
        before=values(4000000,'balance_power');desired=values(3000000,'performance')
        state=ledger.prepare(None,generation=GEN,boot=BOOT,invocation=INV,transaction=TX,config_sha256='c'*64,expected_config_sha256='c'*64,
                             before=before,desired=desired,expected_after=desired)
        self.assertEqual(state['attempts'][-1]['phase'],'prepared')
        state=ledger.complete(state,generation=GEN,boot=BOOT,invocation=INV,transaction=TX,after=desired,config_sha256='c'*64)
        state=ledger.stopped(state,generation=GEN,boot=BOOT,invocation=INV,transaction=TX,after=before)
        next_state=ledger.prepare(state,generation=GEN,boot=BOOT,invocation='d'*32,transaction=TX,config_sha256='c'*64,expected_config_sha256='c'*64,
                                  before=desired,desired=desired,expected_after=desired)
        self.assertEqual(next_state['boots'][BOOT],before)
    def test_interrupted_write_preserves_third_values(self):
        before=values(4000000,'balance_power');desired=values(3000000,'performance')
        state=ledger.prepare(None,generation=GEN,boot=BOOT,invocation=INV,transaction=TX,config_sha256='c'*64,expected_config_sha256='c'*64,
                             before=before,desired=desired,expected_after=desired)
        current=copy.deepcopy(before);paths=list(current)
        current[paths[0]]=desired[paths[0]];current[paths[2]]='3500000'
        plan=ledger.reconcile(state,generation=GEN,boot=BOOT,invocation=INV,transaction=TX,current=current)
        self.assertIn(paths[0],plan['safe_to_restore']);self.assertIn(paths[2],plan['external_preserved'])
        self.assertNotIn(paths[2],plan['safe_to_restore'])
        with self.assertRaises(ledger.LedgerError):
            ledger.prepare(state,generation=GEN,boot=BOOT,invocation='d'*32,transaction=TX,config_sha256='c'*64,expected_config_sha256='c'*64,
                           before=current,desired=desired,expected_after=desired)
    def test_disabled_public_restore_preserves_external_changes(self):
        before=values(4000000,'balance_power');owned=values(3000000,'performance')
        public={'before':before,'owned':{p:[v] for p,v in owned.items()}}
        current=copy.deepcopy(owned);names=list(current);current[names[0]]='3500000'
        expected=ledger.expected_after(current,{},public)
        self.assertEqual(expected[names[0]],'3500000')
        self.assertEqual(expected[names[2]],before[names[2]])
        public['owned'][names[2]]=['not-a-number']
        with self.assertRaises(ledger.LedgerError):ledger.expected_after(current,{},public)
    def test_disabled_requires_expected_exact_readback(self):
        before=values(4000000,'balance_power')
        state=ledger.prepare(None,generation=GEN,boot=BOOT,invocation=INV,transaction=TX,config_sha256='c'*64,expected_config_sha256='c'*64,
                             before=before,desired={},expected_after=before)
        with self.assertRaises(ledger.LedgerError):
            ledger.complete(state,generation=GEN,boot=BOOT,invocation=INV,transaction=TX,after=values(3000000,'balance_power'),config_sha256='c'*64)

if __name__=='__main__':unittest.main()

