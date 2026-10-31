import copy,hashlib,os,unittest
from pathlib import Path
from protocol import ec_profile_contract as ec

def resource():
    payload=[{'path':'module','sha256':ec.MODULE_SHA},
             {'path':'loader','sha256':ec.TARGETS['var/lib/oxp3-fan/load-fan-module.py']},
             {'path':'unit','sha256':ec.TARGETS['etc/systemd/system/oxp3-fan-module.service']}]
    names=('module','module','loader','unit')
    files=[{'source':name,'target':target,'mode':0o644,'scope':'root-slots'} for name,target in zip(names,ec.TARGETS)]
    return {'id':ec.IDENTITY,'ec_profile_contract':copy.deepcopy(ec.MARKER),'install':{'kind':'files-v1','files':files},
       'verify':{'kind':'sha256-v1'},'restore':{'kind':'transaction-backup-v1'},'payload':{'files':payload},
       'hardware_requires':{'kernel':[ec.KERNEL],'acpi_ec_interface':[True]},
       'compatibility':{'architectures':['x86_64'],'os_ids':['steamos'],'os_versions':['3.10'],'kernels':[ec.KERNEL]},
       'dependencies':[],'external_component_dependencies':[],
       'activation':{'kind':'fixed-ec-profile-provider-v1','phase':'first-boot','autoload':True}}
class ECContractTests(unittest.TestCase):
    def test_fixed_and_compiled_bytes(self):
        value=resource();self.assertIs(ec.validate_contract(value),value)
        path=os.environ.get('BOILER_EC_TEST_KO')
        if not path:self.skipTest('An explicit EC module fixture is required')
        raw=Path(path).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),ec.MODULE_SHA)
        for token in (b'parmtype=board_profile:charp',b'oxp3-g1i-v1',('vermagic='+ec.KERNEL).encode()):self.assertIn(token,raw)
    def test_tampering_rejected(self):
        for key in ('profile','module','target','mode','acpi','activation'):
            r=resource()
            if key=='profile':r['ec_profile_contract']['profile']='other'
            if key=='module':r['payload']['files'][0]['sha256']='0'*64
            if key=='target':r['install']['files'][0]['target']='tmp/other'
            if key=='mode':r['install']['files'][0]['mode']=0o600
            if key=='acpi':r['hardware_requires']['acpi_ec_interface']=[1]
            if key=='activation':r['activation']['autoload']=1
            with self.subTest(key=key),self.assertRaises(ValueError):ec.validate_contract(r)

if __name__=='__main__':unittest.main()
