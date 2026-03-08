import tempfile
import unittest
from pathlib import Path

from backend import tx_engine


class TxEngineTests(unittest.TestCase):
    def test_build_hackrf_tx_command_uses_safe_amp_off_defaults(self):
        cmd = tx_engine._build_hackrf_tx_command(
            Path("/tmp/test.cs8"),
            frequency=433_920_000,
            sample_rate=2_000_000,
            tx_vga=20,
        )
        self.assertEqual(
            [
                "hackrf_transfer",
                "-t", "/tmp/test.cs8",
                "-f", "433920000",
                "-s", "2000000",
                "-a", "0",
                "-x", "20",
            ],
            cmd,
        )

    def test_validate_tx_file_rejects_oversized_duration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "too_long.cs8"
            sample_rate = 2_000_000
            too_long_bytes = int((tx_engine.HACKRF_MAX_TX_SECONDS + 1.0) * sample_rate * 2)
            path.write_bytes(b"\x00" * too_long_bytes)

            with self.assertRaisesRegex(ValueError, "refusing to transmit files over"):
                tx_engine._validate_tx_file(path, sample_rate)

    def test_sanitize_frequency_rejects_out_of_range_values(self):
        with self.assertRaises(ValueError):
            tx_engine._sanitize_frequency(100_000)

        self.assertEqual(433_920_000, tx_engine._sanitize_frequency(433_920_000))


if __name__ == "__main__":
    unittest.main()
