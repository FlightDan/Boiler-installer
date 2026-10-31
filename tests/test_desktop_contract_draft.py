import hashlib,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from protocol import desktop_hdr_contract as contract

class ContractTests(unittest.TestCase):
    def test_pending_native_never_accepted(self):
        with self.assertRaisesRegex(ValueError,'differs from reviewed fixed contract'):contract.validate_contract({})

    def test_only_exact_frozen_inventory_and_profile(self):
        payload=[]
        for key in contract.PROFILES:
            data=contract.profile_bytes(key);payload.append(dict(path='payload/desktop/profile-'+key+'.json',size=len(data),sha256=hashlib.sha256(data).hexdigest()))
        files=[dict(source='payload/desktop/panel-binding.json',target=contract.PREFIX+'share/boiler-desktop-hdr/panel-binding.json',scope='root-slots',mode=420)]
        metadata=dict(hardware_requires={},compatibility={},external_component_dependencies=[])
        resource=dict(id=contract.IDENTITY,dependencies=[],profiles=contract.PROFILES,desktop_bundle_sha256='a'*64,**metadata,
            install=dict(kind=contract.KIND,files=files),payload={'files':payload},verify={'kind':'sha256-v1'},restore={'kind':'transaction-backup-v1'})
        with patch.object(contract,'REVIEWED_BUNDLE_SHA256','a'*64),patch.object(contract,'REVIEWED_PAYLOAD',payload),patch.object(contract,'REVIEWED_INSTALL_FILES',files),patch.object(contract,'REVIEWED_METADATA',metadata):
            selected=contract.render(resource,{'profile':'x2-mini'})
            self.assertEqual(selected['hdr_selected_profile'],'x2-mini')
            self.assertEqual(selected['install']['kind'],'files-v1')
            with self.assertRaises(ValueError):contract.render(resource,{'profile':'x2-mini','command':'x'})
            altered=dict(resource,desktop_bundle_sha256='b'*64)
            with self.assertRaises(ValueError):contract.validate_contract(altered)

if __name__=='__main__':unittest.main()
