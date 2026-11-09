"""Runtime-profile constraints shared by the BF63112 and FTE3600 backends.

The two sensor backends live in one libfprint and one fprintd service, so the
profile files below have to satisfy both at once. These checks read the real
shipped files, so a future edit that only suits one sensor fails here.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'common/fingerprint/runtime-profile'


def spidev_bufsiz():
    for line in (PROFILE / 'spidev.conf').read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('options spidev'):
            for token in line.split():
                if token.startswith('bufsiz='):
                    return int(token.split('=', 1)[1], 0)
    raise AssertionError('spidev.conf declares no bufsiz option')


class SpidevProfileTests(unittest.TestCase):
    # BF63112 requires one address byte plus its largest FIFO payload to be read
    # in a single message (bf63112.c: BF63112_SPI_MESSAGE_SIZE, 96*112 image).
    BF63112_MINIMUM = 1 + 1 + 96 * 112
    # FTE3600 clamps its 65535-byte transfer capacity down to the spidev bufsiz
    # (linux_board.c: config.transfer_capacity = min(requested, spi_bufsiz)).
    FTE3600_MINIMUM = 65535

    def test_bufsiz_covers_the_larger_of_the_two_backends(self):
        bufsiz = spidev_bufsiz()
        self.assertGreaterEqual(bufsiz, self.BF63112_MINIMUM,
                                'spidev buffer is too small for an atomic BF63112 FIFO read')
        self.assertGreaterEqual(bufsiz, self.FTE3600_MINIMUM,
                                'spidev buffer silently truncates FTE3600 image transfers')

    def test_bufsiz_is_not_the_bf_only_legacy_value(self):
        # 16384 satisfies BF63112 but truncates FTE3600; it must not come back.
        self.assertGreaterEqual(self.BF63112_MINIMUM, 1)
        self.assertLess(16384, self.FTE3600_MINIMUM,
                        'the legacy 16384 profile is insufficient for FTE3600')
        self.assertGreater(spidev_bufsiz(), 16384)


class FprintdServiceTests(unittest.TestCase):
    def test_service_allows_every_device_the_backends_open(self):
        text = (PROFILE / 'boiler-fprintd.service').read_text(encoding='utf-8')
        allowances = {line.split('=', 1)[1].strip()
                      for line in text.splitlines()
                      if line.startswith('DeviceAllow=')}
        # FTE3600 polls its ACPI glue UIO node; BF63112 opens spi and requests a
        # reset GPIO. All three must be permitted or a backend fails closed.
        self.assertIn('char-spi rw', allowances)
        self.assertIn('char-gpiochip rw', allowances)
        self.assertIn('char-uio rw', allowances)

    def test_service_keeps_one_dbus_owner_and_no_borrowed_library_path(self):
        text = (PROFILE / 'boiler-fprintd.service').read_text(encoding='utf-8')
        self.assertIn('BusName=net.reactivated.Fprint', text)
        self.assertEqual(text.count('BusName='), 1,
                         'two backends must not race for separate D-Bus names')
        self.assertIn('UnsetEnvironment=LD_LIBRARY_PATH', text)


if __name__ == '__main__':
    unittest.main()
