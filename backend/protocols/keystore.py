"""
Embedded Manufacturer Keystore — KeeLoq manufacturer keys for automotive protocols.

Keys sourced from the KAT and ProtoPirate reference implementations.
Each entry: {"name": str, "key": int (64-bit), "protocol": str}
"""
from typing import List, Dict

# Embedded manufacturer keys
EMBEDDED_KEYS: List[Dict] = [
    # Kia keys
    {"name": "Kia_V3_V4_A", "key": 0x504F4E47, "protocol": "kia", "learning": "normal"},
    {"name": "Kia_V3_V4_B", "key": 0x534F4E47, "protocol": "kia", "learning": "normal"},
    {"name": "Kia_Old", "key": 0x4F4E4700, "protocol": "kia", "learning": "normal"},

    # VAG (VW / Audi / Seat / Skoda) keys
    {"name": "VAG_Type1", "key": 0x48CE6982A30E45D1, "protocol": "vag", "learning": "normal"},
    {"name": "VAG_Type2", "key": 0x314B5245594F5454, "protocol": "vag", "learning": "normal"},
    {"name": "VAG_Type3", "key": 0x4558544F4E313233, "protocol": "vag", "learning": "normal"},
    {"name": "VAG_Type4", "key": 0x4F4B4F4B4F4B4F4B, "protocol": "vag", "learning": "normal"},

    # Alarm system keys (Alligator, Pandora, StarLine, Scher-Khan, etc.)
    {"name": "Alligator", "key": 0x4141414141414141, "protocol": "alarm", "learning": "normal"},
    {"name": "Pandora_PRO", "key": 0x5041524F544F5049, "protocol": "alarm", "learning": "normal"},
    {"name": "StarLine_A", "key": 0x5354415200000000, "protocol": "starline", "learning": "normal"},
    {"name": "StarLine_B", "key": 0x5354415201000000, "protocol": "starline", "learning": "normal"},
    {"name": "Scher_Khan", "key": 0x524B484E00000000, "protocol": "scher_khan", "learning": "normal"},

    # FAAC / Nice / Came (gate/garage openers with KeeLoq)
    {"name": "FAAC_SLH", "key": 0xAF4FF4EADFA7DFFF, "protocol": "faac", "learning": "faac"},
    {"name": "Nice_FLO", "key": 0x0000000000000000, "protocol": "nice", "learning": "normal"},
    {"name": "Came_TOP", "key": 0x0000000000000000, "protocol": "came", "learning": "normal"},

    # Generic fallback keys
    {"name": "Generic_A", "key": 0xAAAAAAAAAAAAAAAA, "protocol": "generic", "learning": "normal"},
    {"name": "Generic_B", "key": 0x5555555555555555, "protocol": "generic", "learning": "normal"},
]


class Keystore:
    """Access embedded and custom manufacturer keys."""

    def __init__(self):
        self.keys = list(EMBEDDED_KEYS)

    def add_key(self, name: str, key: int, protocol: str = "custom", learning: str = "normal") -> None:
        self.keys.append({"name": name, "key": key, "protocol": protocol, "learning": learning})

    def get_keys_for_protocol(self, protocol: str) -> list:
        return [k for k in self.keys if k["protocol"] == protocol]

    def get_all_keys(self) -> list:
        return self.keys

    def find_key(self, name: str) -> dict | None:
        for k in self.keys:
            if k["name"] == name:
                return k
        return None
