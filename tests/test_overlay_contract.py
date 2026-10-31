import copy, unittest
from protocol.overlay_contract import IDENTITY,KIND,FILES,validate_contract

class ContractTests(unittest.TestCase):
    def resource(self):
        return dict(id=IDENTITY,install=dict(kind=KIND,files=[dict(source=src,target=target,mode=mode,scope='root-slots') for key,src,target,mode,size,digest in FILES]),payload=dict(files=[dict(path=src,size=size,sha256=digest) for key,src,target,mode,size,digest in FILES]),verify={'kind':'sha256-v1'},restore={'kind':'transaction-backup-v1'},dependencies=[],external_component_dependencies=[],hardware_requires={})
    def test_closed_targets(self):
        r=self.resource();validate_contract(r)
        for mutate in (lambda x:x['install']['files'][0].update(target='etc/shadow'),lambda x:x['install']['files'][0].update(mode=511),lambda x:x['payload']['files'][0].update(sha256='0'*64),lambda x:x.update(hardware_requires={'path':['/etc/shadow']})):
            value=copy.deepcopy(r);mutate(value)
            with self.assertRaises(ValueError):validate_contract(value)
if __name__=='__main__':unittest.main()
