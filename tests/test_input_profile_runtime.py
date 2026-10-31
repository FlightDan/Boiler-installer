import copy
import unittest
from maintenance.input_profile_runtime import configured_profile

def profile():return dict(version=1,kind='CompositeDevice',name='Boiler ONEXPLAYER USB interface 02',source_devices=[dict(group='imu',iio=dict(name='bmi260'))])
def calibration():return dict(gyro_bias_deg_s=[1.1,2.2,3.3],accel_bias_m_s2=[0,0.1,-0.2],auto_calibrate=True)
class ProfileTests(unittest.TestCase):
 def test_preserved_bias_and_auto_calibration_are_applied_to_real_source_keys(self):
  original=calibration();result=configured_profile(profile(),original);iio=result['source_devices'][0]['iio']
  self.assertEqual(iio['gyro_bias'],original['gyro_bias_deg_s']);self.assertEqual(iio['accel_bias'],original['accel_bias_m_s2']);self.assertTrue(iio['gyro_auto_calibrate']);self.assertEqual(original,calibration())
 def test_no_gyro_selection_does_not_add_a_motion_source(self):
  source=profile();source['source_devices']=[];self.assertEqual(configured_profile(source)['source_devices'],[])
 def test_invalid_calibration_types_axes_and_nonfinite_values_refuse(self):
  for key,value in [('gyro_bias_deg_s',[True,0,0]),('gyro_bias_deg_s',[1,2]),('gyro_bias_deg_s',[float('nan'),0,0]),('accel_bias_m_s2',[0,float('inf'),0]),('auto_calibrate','false')]:
   candidate=calibration();candidate[key]=value
   with self.subTest(key=key,value=value),self.assertRaises(ValueError):configured_profile(profile(),candidate)
 def test_missing_and_unknown_calibration_fields_refuse(self):
  for candidate in ({},dict(calibration(),unknown=1)):
   with self.assertRaises(ValueError):configured_profile(profile(),candidate)
 def test_missing_or_ambiguous_imu_does_not_silently_ignore_calibration(self):
  for sources in ([],[dict(group='imu',iio={}),dict(group='imu',iio={})]):
   candidate=profile();candidate['source_devices']=sources
   with self.assertRaises(ValueError):configured_profile(candidate,calibration())
if __name__=='__main__':unittest.main()
