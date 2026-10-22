from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'guest'))
from offload_mounts import etc_overlay_options


class SourceOverlayChecks(unittest.TestCase):
    def test_actual_38_and_310_vendor_profiles_are_accepted(self):
        base = 'Where=/etc\nWhat=overlay\nType=overlay\nOptions=lowerdir=/etc,upperdir=/var/lib/overlays/etc/upper,workdir=/var/lib/overlays/etc/work'
        self.assertTrue(etc_overlay_options(base))
        self.assertTrue(etc_overlay_options(base + ',index=off'))

    def test_unknown_mount_paths_and_flags_remain_rejected(self):
        base = 'Where=/etc\nWhat=overlay\nType=overlay\nOptions=lowerdir=/etc,upperdir=/var/lib/overlays/etc/upper,workdir=/var/lib/overlays/etc/work'
        for text in (base.replace('upperdir=/var', 'upperdir=/unknown'), base + ',redirect_dir=on', base + ',index=on', base + '\nOptions=lowerdir=/tmp'):
            with self.assertRaises(RuntimeError): etc_overlay_options(text)


if __name__ == '__main__': unittest.main()
