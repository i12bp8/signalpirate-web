import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend import signal_export


class SignalExportTests(unittest.TestCase):
    def test_export_converts_cu8_to_cs8_and_writes_matching_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            export_dir = root / "exports"
            import_dir = root / "imports"
            export_dir.mkdir()
            import_dir.mkdir()

            src_path = root / "unit_433.92M_250k.cu8"
            src_path.write_bytes(bytes([0, 127, 128, 255]))

            signal_data = {
                "time": 1234567890,
                "model": "Test Sensor",
                "freq": 433_920_000,
                "mod": "OOK",
                "iq_file": str(src_path),
                "protocol_info": {
                    "name": "Test Sensor",
                    "security": "none",
                    "security_level": "low",
                },
            }

            with mock.patch.object(signal_export, "EXPORT_DIR", str(export_dir)), mock.patch.object(
                signal_export, "IMPORT_DIR", str(import_dir)
            ), mock.patch.object(signal_export, "_decode_models_from_iq", return_value=["Test Sensor"]):
                out_path = Path(signal_export.export_hackrf_c8(signal_data))

            self.assertEqual(".cs8", out_path.suffix)
            self.assertEqual(bytes([128, 255, 0, 127]), out_path.read_bytes())

            meta_path = Path(str(out_path) + ".meta.json")
            meta = json.loads(meta_path.read_text())
            self.assertEqual("cs8", meta["sample_format"])
            self.assertEqual("cu8", meta["source_sample_format"])
            self.assertEqual(250_000, meta["sample_rate"])

    def test_export_normalizes_explicit_c8_filename_to_cs8(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            export_dir = root / "exports"
            import_dir = root / "imports"
            export_dir.mkdir()
            import_dir.mkdir()

            signal_data = {
                "time": 1234567890,
                "model": "Test Remote",
                "freq": 433_920_000,
                "mod": "OOK",
                "data": "0xA5",
            }

            with mock.patch.object(signal_export, "EXPORT_DIR", str(export_dir)), mock.patch.object(
                signal_export, "IMPORT_DIR", str(import_dir)
            ):
                out_path = Path(signal_export.export_hackrf_c8(signal_data, filename="legacy_name.c8"))

            self.assertEqual("legacy_name.cs8", out_path.name)
            self.assertTrue(out_path.exists())
            self.assertTrue(Path(str(out_path).replace(".cs8", ".urh.zip")).exists())


if __name__ == "__main__":
    unittest.main()
