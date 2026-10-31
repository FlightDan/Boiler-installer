import unittest
from protocol.resource_selection import installer_dependencies

class InstallerDependencyTests(unittest.TestCase):
 def test_decky_resource_name_maps_to_the_actual_installer_option(self):
  self.assertEqual(installer_dependencies({'external_component_dependencies':['decky']}),{'core:decky'})
 def test_canonical_core_id_remains_unchanged(self):
  self.assertEqual(installer_dependencies({'external_component_dependencies':['core:decky']}),{'core:decky'})
 def test_unknown_required_component_is_not_silently_dropped(self):
  self.assertEqual(installer_dependencies({'external_component_dependencies':['unavailable']}),{'unavailable'})

if __name__=='__main__':unittest.main()
