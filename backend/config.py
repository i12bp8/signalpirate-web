import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".config" / "signalpirate" / "settings.json"

_current_config = {
    "ai_key": "",
    "ai_model": "anthropic/claude-3.5-haiku",
    "research_mode": False,
}

def load_config() -> dict:
    global _current_config
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r") as f:
                loaded = json.load(f)
                _current_config.update(loaded)
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
    return _current_config

def save_config(new_config: dict) -> dict:
    global _current_config
    _current_config.update(new_config)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(_current_config, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save config: {e}")
    return _current_config

def get_config() -> dict:
    return _current_config
