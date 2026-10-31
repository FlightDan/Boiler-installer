import sys,tempfile,unittest,copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from maintenance import desktop_hdr_firstboot as first,desktop_hdr_resources as desktop,resource_runtime as runtime,core_retention
ID=desktop.IDENTITY
class EntryTests(unittest.TestCase):
 def test_firstboot_allows_authenticated_owned_root_but_rejects_legacy_home(self):
  with tempfile.TemporaryDirectory() as tmp:
   base=Path(tmp);roots={k:base/k for k in ('A','B')};[p.mkdir() for p in roots.values()];home=base/'home';home.mkdir();mounts=SimpleNamespace(home=home,roots=roots)
   for root in roots.values():(root/'opt/oxp3-desktop-gamma22').mkdir(parents=True)
   receipt={'id':'group','transactions':[{'component':ID}]};pool=SimpleNamespace(resource=lambda _: {'hdr_selected_profile':'x2-mini'});session=SimpleNamespace(pool=pool,slots={k:{'root':p} for k,p in roots.items()},_bound_receipt=lambda _:receipt,verify=lambda _:[{'ok':True}]);intent={'selection':{'components':[ID]}}
   with patch.object(first.lifecycle,'_session',return_value=(session,[])),patch.object(first.lifecycle,'_receipt',return_value=receipt),patch.object(desktop,'preflight'),patch.object(desktop,'check_restore'),patch('maintenance.resource_reinstall.engines',side_effect=RuntimeError('verified-owned')):
    with self.assertRaisesRegex(RuntimeError,'verified-owned'):first.prepare(intent,mounts,pool,[],{})
    legacy=home/'deck/.local/lib/oxp3-desktop-hdr-runtime/launch.sh';legacy.parent.mkdir(parents=True);legacy.touch()
    with self.assertRaisesRegex(runtime.ResourceError,'legacy'):first.prepare(intent,mounts,pool,[],{})
 def test_untrusted_receipt_rejected_and_offline_preflight_precedes_write(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);mounts=SimpleNamespace(home=p,roots={'A':p/'A','B':p/'B'});pool=SimpleNamespace(resource=lambda _:{'hdr_selected_profile':'x2-mini'});session=SimpleNamespace(pool=pool,slots={},_bound_receipt=lambda _:{'id':'other','transactions':[]})
   with patch.object(first.lifecycle,'_session',return_value=(session,[])),patch.object(first.lifecycle,'_receipt',return_value={'id':'expected'}),patch.object(desktop,'preflight'):
    with self.assertRaisesRegex(runtime.ResourceError,'receipt differs'):first.prepare({'selection':{'components':[ID]}},mounts,pool,[],{})
  session=object.__new__(runtime.ResourceSession);session.pool=object();session.slots={'A':{'root':Path('/none/A')},'B':{'root':Path('/none/B')}}
  with patch.object(runtime,'resolve',return_value=[{'id':ID}]),patch.object(desktop,'preflight',side_effect=runtime.ResourceError('preflight-rejected')) as check,patch.object(runtime.ResourceSession,'_desktop_opt_proof') as proof:
   with self.assertRaisesRegex(runtime.ResourceError,'preflight-rejected'):session.prepare([{'resource':ID}],[ID])
   check.assert_called_once();proof.assert_not_called()
 def test_restore_accepts_only_exact_core_retained_manifest(self):
  manifest={'id':ID,'files':[{'source':'x','target':'opt/x','mode':420}],'requires':[],'conflicts':[],'hooks':[]}
  receipt={'id':'group','transactions':[{'component':ID,'scope':'A','transaction':'tx'}]};pool=SimpleNamespace(resource=lambda _:{'dependencies':[]});session=SimpleNamespace(pool=pool,shared_opt=False,engines={'A':SimpleNamespace(_journal=lambda _:Path('journal'))},_desktop_opt_proof=lambda:None,_bound_receipt=lambda _:receipt)
  valid=core_retention.decorate(manifest,shared=False)
  with patch.object(runtime,'component',return_value=copy.deepcopy(manifest)),patch('protocol.manifest.load_json',return_value={'component':valid}):desktop.check_restore(session,receipt)
  invalid=copy.deepcopy(valid);invalid['requires'].append('core:other')
  with patch.object(runtime,'component',return_value=copy.deepcopy(manifest)),patch('protocol.manifest.load_json',return_value={'component':invalid}):
   with self.assertRaisesRegex(runtime.ResourceError,'authorized retained'):desktop.check_restore(session,receipt)
if __name__=='__main__':unittest.main()
