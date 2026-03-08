import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".config" / "signalpirate" / "settings.json"

DEFAULT_CONFIG = {
    "ai_key": "",
    "ai_model": "anthropic/claude-3.5-haiku",
    "research_mode": False,
    "rtl_autolevel": True,
    "rtl_squelch": True,
    "rtl_gain": 38,
    "unique_scans_only": False,
    "sniper_mode_model": "",
    "tx_profiles": {},
}

_current_config = dict(DEFAULT_CONFIG)


def _merge_defaults(loaded: dict | None = None) -> dict:
    merged = dict(DEFAULT_CONFIG)
    if loaded:
        merged.update(loaded)
    return merged


def load_config() -> dict:
    global _current_config
    loaded = {}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r") as f:
                loaded = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
    _current_config = _merge_defaults(loaded)
    return dict(_current_config)


def save_config(new_config: dict) -> dict:
    global _current_config
    _current_config = _merge_defaults(_current_config)
    _current_config.update(new_config or {})
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(_current_config, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save config: {e}")
    return dict(_current_config)


def get_config() -> dict:
    return dict(_current_config)
