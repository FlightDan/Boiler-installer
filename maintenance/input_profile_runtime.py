"""Prepare the fixed InputPlumber profile and apply preserved BMI260 biases."""
import json,math
from pathlib import Path
import sys
sys.path.insert(0,'/usr/local/lib/boiler')
from maintenance.adoption_blobs import _directory
from maintenance.engine import _json_write
from maintenance.input_activation import _safe_file

BASE=Path('/usr/share/inputplumber/devices/00-boiler-onexplayer.yaml')
GYRO=Path('/etc/inputplumber/devices.d/00-boiler-onexplayer.yaml')
CALIBRATION=Path('/etc/boiler-input/calibration.json')
RUNTIME=Path('/run/boiler-input-runtime')
OUTPUT=RUNTIME/'rootfs/usr/share/inputplumber/devices/00-boiler-onexplayer.yaml'

def configured_profile(profile,calibration=None):
 if profile.get('version')!=1 or profile.get('kind')!='CompositeDevice' or profile.get('name')!='Boiler ONEXPLAYER USB interface 02':
  raise ValueError('Unexpected fixed InputPlumber profile')
 if calibration is None:return profile
 if set(calibration)!={'gyro_bias_deg_s','accel_bias_m_s2','auto_calibrate'} or type(calibration['auto_calibrate']) is not bool:
  raise ValueError('Invalid preserved BMI260 calibration keys')
 for name in ('gyro_bias_deg_s','accel_bias_m_s2'):
  value=calibration[name]
  if not isinstance(value,list) or len(value)!=3 or any(type(x) not in (int,float) or not math.isfinite(x) for x in value):
   raise ValueError('BMI260 calibration requires three finite values per axis group')
 matches=[row['iio'] for row in profile['source_devices'] if row.get('group')=='imu' and 'iio' in row]
 if len(matches)!=1:raise ValueError('The selected motion profile has no unique IIO source')
 matches[0].update(gyro_bias=calibration['gyro_bias_deg_s'],accel_bias=calibration['accel_bias_m_s2'],gyro_auto_calibrate=calibration['auto_calibrate'])
 return profile

def main():
 selected=GYRO.exists()
 raw=_safe_file(GYRO if selected else BASE)
 if len(raw)>65536:raise ValueError('Input profile exceeds size limit')
 profile=json.loads(raw)
 calibration=None
 if selected:
  raw=_safe_file(CALIBRATION)
  if len(raw)>65536:raise ValueError('Calibration exceeds size limit')
  calibration=json.loads(raw)
 profile=configured_profile(profile,calibration)
 with _directory(OUTPUT.parent,create=True):pass
 _json_write(OUTPUT,profile)

if __name__=='__main__':main()
