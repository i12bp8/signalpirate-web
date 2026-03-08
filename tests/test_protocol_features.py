import json
import tempfile
import unittest
from pathlib import Path

from backend import protocol_features


class ProtocolFeaturesTests(unittest.TestCase):
    def test_recommend_tools_for_switch_family_includes_expected_github_projects(self):
        proto = {
            "name": "Proove / Nexa / KlikAanKlikUit Wireless Switch",
            "category": "Remote Control",
            "modulation": "OOK",
            "encoding": "PWM",
            "description": "Fixed-code wall switch family",
        }

        tools = protocol_features.recommend_tools(proto)
        tool_ids = [tool["id"] for tool in tools[:5]]

        self.assertIn("rtl_433", tool_ids)
        self.assertIn("urh", tool_ids)
        self.assertIn("rc_switch", tool_ids)
        self.assertIn("utils_433", tool_ids)

    def test_protocol_catalog_maps_specialist_tools_for_nfc_wifi_and_lora(self):
        protocols = [
            {"name": "ISO14443 Demo", "category": "Access Control", "modulation": "NFC", "encoding": "ISO14443"},
            {"name": "WiFi Sensor", "category": "IoT", "modulation": "OFDM", "encoding": "WiFi"},
            {"name": "LoRa Node", "category": "IoT", "modulation": "CSS", "encoding": "LoRa"},
        ]

        catalog = protocol_features.build_protocol_catalog(protocols)
        by_name = {item["name"]: item for item in catalog}

        self.assertEqual("proxmark3", by_name["ISO14443 Demo"]["tools"][0]["id"])
        self.assertEqual("gr_ieee80211", by_name["WiFi Sensor"]["tools"][0]["id"])
        self.assertEqual("gr_lora_sdr", by_name["LoRa Node"]["tools"][0]["id"])

    def test_signal_capabilities_marks_nexa_family_as_editable(self):
        signal = {
            "model": "Nexa-Security",
            "iq_file": "/tmp/sample.cu8",
            "data": {
                "id": 15570906,
                "channel": 1,
                "unit": 1,
                "group": 0,
                "state": "OFF",
            },
            "protocol_info": {
                "name": "Proove / Nexa / KlikAanKlikUit Wireless Switch",
                "category": "Remote Control",
                "modulation": "OOK",
                "encoding": "PWM",
            },
        }

        caps = protocol_features.get_signal_capabilities(signal)

        self.assertEqual("editable", caps["tier"])
        self.assertTrue(caps["can_edit"])
        self.assertTrue(caps["can_replay_raw"])
        self.assertEqual("nexa_switch", caps["editor"]["id"])

    def test_build_protocol_variant_writes_cs8_urh_zip_and_variant_metadata(self):
        signal = {
            "_id": 42,
            "model": "Nexa-Security",
            "frequency": 433_920_000,
            "data": {
                "id": 15570906,
                "channel": 1,
                "unit": 1,
                "group": 0,
                "state": "OFF",
            },
            "protocol_info": {
                "name": "Proove / Nexa / KlikAanKlikUit Wireless Switch",
                "category": "Remote Control",
                "modulation": "OOK",
                "encoding": "PWM",
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            result = protocol_features.build_protocol_variant(signal, {"state": "ON"}, tmpdir)
            out_path = Path(result["path"])

            self.assertEqual(".cs8", out_path.suffix)
            self.assertTrue(out_path.exists())
            self.assertTrue(out_path.with_suffix(".urh.zip").exists())
            self.assertTrue(Path(str(out_path) + ".meta.json").exists())
            meta = json.loads(Path(str(out_path) + ".variant.json").read_text())
            self.assertEqual("ON", meta["variant"]["state"])
            self.assertEqual("nexa_switch", meta["variant"]["editor"])

    def test_logical_bit_composition_matches_known_nexa_capture(self):
        bits = protocol_features._compose_nexa_switch_logical_bits(15570906, 1, 1, 0, False)
        raw_bits = protocol_features._manchester_encode(bits)

        self.assertEqual("00111011011001011111011010001111", bits)
        self.assertEqual("0101101010011010011010010110011010101010011010011001010110101010", raw_bits)


if __name__ == "__main__":
    unittest.main()
