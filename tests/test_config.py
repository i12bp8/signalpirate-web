import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend import config


class ConfigTests(unittest.TestCase):
    def test_load_config_applies_defaults_and_persists_research_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / 'settings.json'
            cfg_path.write_text(json.dumps({"research_mode": True, "ai_model": "test/model"}))

            with mock.patch.object(config, 'CONFIG_PATH', cfg_path), mock.patch.object(config, '_current_config', dict(config.DEFAULT_CONFIG)):
                loaded = config.load_config()

            self.assertTrue(loaded['research_mode'])
            self.assertEqual('test/model', loaded['ai_model'])
            self.assertTrue(loaded['rtl_autolevel'])
            self.assertEqual('', loaded['sniper_mode_model'])

    def test_save_config_round_trips_new_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / 'settings.json'

            with mock.patch.object(config, 'CONFIG_PATH', cfg_path), mock.patch.object(config, '_current_config', dict(config.DEFAULT_CONFIG)):
                saved = config.save_config({
                    'research_mode': True,
                    'unique_scans_only': True,
                    'sniper_mode_model': 'Auriol-V2',
                })
                reloaded = config.load_config()

            self.assertTrue(saved['research_mode'])
            self.assertTrue(reloaded['research_mode'])
            self.assertTrue(reloaded['unique_scans_only'])
            self.assertEqual('Auriol-V2', reloaded['sniper_mode_model'])


if __name__ == '__main__':
    unittest.main()
