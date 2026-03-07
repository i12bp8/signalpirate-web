"""
Vehicle Protocol Decoders — 14+ decoders for automotive keyfob signals.

Each decoder analyzes rtl_433 JSON output and extracts protocol-specific fields
(serial, counter, button, encryption type, CRC). Ported from KAT's Rust decoders.

Supports: Kia V0-V6, Ford V0, Fiat V0, Subaru, Suzuki, VAG, PSA, Scher-Khan, StarLine
Plus KeeLoq generic fallback for unknown signals.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from backend.protocols.keeloq import KeeLoq, keeloq_decrypt
from backend.protocols.keystore import Keystore, EMBEDDED_KEYS

logger = logging.getLogger("signalpirate.protocols.decoders")

# Button code mapping
BUTTON_NAMES = {
    0x1: "Lock",
    0x2: "Unlock",
    0x3: "Lock+Unlock",
    0x4: "Trunk",
    0x5: "Lock+Trunk",
    0x8: "Panic",
    0x9: "Lock+Panic",
    0xA: "Unlock+Panic",
}


@dataclass
class DecodedSignal:
    """Result of a protocol decode attempt."""
    protocol: str = ""              # e.g. "Kia V3/V4", "VAG Type 2"
    manufacturer: str = ""          # e.g. "Kia", "Volkswagen"
    serial: int = 0                 # Device serial number
    serial_hex: str = ""            # Hex representation
    counter: int = 0                # Rolling code counter
    button: int = 0                 # Button code
    button_name: str = ""           # Human-readable button name
    encryption: str = ""            # e.g. "KeeLoq", "AUT64", "None"
    encoding: str = ""              # e.g. "PWM", "Manchester"
    rf_modulation: str = "AM"       # AM, FM, or AM/FM
    data_bits: int = 0              # Total bit count
    data_hex: str = ""              # Full hex data
    crc_valid: Optional[bool] = None
    key_name: str = ""              # Which keystore key decoded it
    key_index: int = -1             # Index in keystore
    encoder_capable: bool = False   # Can we re-encode for TX?
    decoded_fields: Dict[str, Any] = field(default_factory=dict)
    raw_data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "protocol": self.protocol,
            "manufacturer": self.manufacturer,
            "serial": self.serial,
            "serial_hex": self.serial_hex,
            "counter": self.counter,
            "button": self.button,
            "button_name": self.button_name,
            "encryption": self.encryption,
            "encoding": self.encoding,
            "rf_modulation": self.rf_modulation,
            "data_bits": self.data_bits,
            "data_hex": self.data_hex,
            "crc_valid": self.crc_valid,
            "key_name": self.key_name,
            "encoder_capable": self.encoder_capable,
        }


# ──────────────────────────────────────────────────────
#  Protocol Decoders
# ──────────────────────────────────────────────────────

def _decode_kia(data: dict) -> Optional[DecodedSignal]:
    """Decode Kia keyfob (V0-V6, KeeLoq-based)."""
    model = data.get("model", "")
    if "Kia" not in model and "kia" not in model.lower():
        return None

    sig = DecodedSignal(
        manufacturer="Kia",
        encryption="KeeLoq",
        encoding="PWM",
        rf_modulation="AM/FM",
        encoder_capable=True,
        raw_data=data,
    )

    # Determine version from model string
    if "Kia" in model:
        sig.protocol = f"Kia ({model})"
    else:
        sig.protocol = "Kia (Unknown Version)"

    # Extract serial and data fields
    if "id" in data:
        sig.serial = int(data["id"]) if isinstance(data["id"], int) else int(str(data["id"]), 0)
        sig.serial_hex = f"0x{sig.serial:06X}"

    if "button_code" in data:
        sig.button = data["button_code"]
    elif "cmd" in data:
        sig.button = data["cmd"]
    sig.button_name = BUTTON_NAMES.get(sig.button, f"Unknown({sig.button})")

    if "data" in data and isinstance(data["data"], str):
        sig.data_hex = data["data"]
        try:
            sig.data_bits = len(data["data"].replace("0x", "")) * 4
        except Exception:
            pass

    # Try KeeLoq decode with keystore
    _try_keeloq_decode(sig, data)

    return sig


def _decode_ford(data: dict) -> Optional[DecodedSignal]:
    """Decode Ford keyfob."""
    model = data.get("model", "")
    if "Ford" not in model and "ford" not in model.lower():
        return None

    sig = DecodedSignal(
        protocol="Ford V0",
        manufacturer="Ford",
        encryption="KeeLoq",
        encoding="PWM",
        rf_modulation="AM",
        encoder_capable=True,
        raw_data=data,
    )

    if "id" in data:
        sig.serial = int(data["id"]) if isinstance(data["id"], int) else int(str(data["id"]), 0)
        sig.serial_hex = f"0x{sig.serial:06X}"

    sig.button = data.get("button_code", data.get("cmd", 0))
    sig.button_name = BUTTON_NAMES.get(sig.button, f"Unknown({sig.button})")

    _try_keeloq_decode(sig, data)
    return sig


def _decode_fiat(data: dict) -> Optional[DecodedSignal]:
    """Decode Fiat keyfob."""
    model = data.get("model", "")
    if "Fiat" not in model and "fiat" not in model.lower():
        return None

    return DecodedSignal(
        protocol="Fiat V0",
        manufacturer="Fiat",
        encryption="KeeLoq",
        encoding="PWM",
        rf_modulation="AM",
        serial=data.get("id", 0),
        serial_hex=f"0x{data.get('id', 0):06X}",
        button=data.get("button_code", 0),
        button_name=BUTTON_NAMES.get(data.get("button_code", 0), "Unknown"),
        raw_data=data,
    )


def _decode_subaru(data: dict) -> Optional[DecodedSignal]:
    """Decode Subaru keyfob (rolling code)."""
    model = data.get("model", "")
    if "Subaru" not in model and "subaru" not in model.lower():
        return None

    sig = DecodedSignal(
        protocol="Subaru",
        manufacturer="Subaru",
        encryption="Rolling Code",
        encoding="Manchester",
        rf_modulation="AM",
        raw_data=data,
    )

    if "id" in data:
        sig.serial = int(data["id"]) if isinstance(data["id"], int) else int(str(data["id"]), 0)
        sig.serial_hex = f"0x{sig.serial:06X}"

    sig.button = data.get("button_code", data.get("cmd", 0))
    sig.button_name = BUTTON_NAMES.get(sig.button, f"Unknown({sig.button})")

    return sig


def _decode_suzuki(data: dict) -> Optional[DecodedSignal]:
    """Decode Suzuki keyfob."""
    model = data.get("model", "")
    if "Suzuki" not in model and "suzuki" not in model.lower():
        return None

    return DecodedSignal(
        protocol="Suzuki",
        manufacturer="Suzuki",
        encryption="KeeLoq",
        encoding="PWM",
        rf_modulation="AM",
        serial=data.get("id", 0),
        serial_hex=f"0x{data.get('id', 0):06X}",
        button=data.get("button_code", 0),
        button_name=BUTTON_NAMES.get(data.get("button_code", 0), "Unknown"),
        raw_data=data,
    )


def _decode_vag(data: dict) -> Optional[DecodedSignal]:
    """Decode VAG (VW/Audi/Seat/Skoda) keyfob."""
    model = data.get("model", "")
    keywords = ["VW", "Volkswagen", "Audi", "Seat", "Skoda", "VAG"]
    if not any(kw.lower() in model.lower() for kw in keywords):
        return None

    sig = DecodedSignal(
        protocol="VAG",
        manufacturer="Volkswagen Group",
        encryption="AUT64/KeeLoq",
        encoding="PWM",
        rf_modulation="AM/FM",
        encoder_capable=True,
        raw_data=data,
    )

    if "id" in data:
        sig.serial = int(data["id"]) if isinstance(data["id"], int) else int(str(data["id"]), 0)
        sig.serial_hex = f"0x{sig.serial:06X}"

    sig.button = data.get("button_code", data.get("cmd", 0))
    sig.button_name = BUTTON_NAMES.get(sig.button, f"Unknown({sig.button})")

    return sig


def _decode_psa(data: dict) -> Optional[DecodedSignal]:
    """Decode PSA (Peugeot/Citroën) keyfob."""
    model = data.get("model", "")
    keywords = ["Peugeot", "Citroen", "PSA"]
    if not any(kw.lower() in model.lower() for kw in keywords):
        return None

    return DecodedSignal(
        protocol="PSA",
        manufacturer="PSA Group",
        encryption="KeeLoq",
        encoding="PWM",
        rf_modulation="AM",
        serial=data.get("id", 0),
        serial_hex=f"0x{data.get('id', 0):06X}",
        raw_data=data,
    )


def _decode_scher_khan(data: dict) -> Optional[DecodedSignal]:
    """Decode Scher-Khan alarm system."""
    model = data.get("model", "")
    if "scher" not in model.lower() and "khan" not in model.lower():
        return None

    return DecodedSignal(
        protocol="Scher-Khan",
        manufacturer="Scher-Khan",
        encryption="KeeLoq",
        encoding="PWM",
        rf_modulation="AM",
        serial=data.get("id", 0),
        serial_hex=f"0x{data.get('id', 0):06X}",
        raw_data=data,
    )


def _decode_starline(data: dict) -> Optional[DecodedSignal]:
    """Decode StarLine alarm system."""
    model = data.get("model", "")
    if "star" not in model.lower() and "line" not in model.lower():
        return None

    return DecodedSignal(
        protocol="Star Line",
        manufacturer="Star Line",
        encryption="KeeLoq",
        encoding="Manchester",
        rf_modulation="AM/FM",
        serial=data.get("id", 0),
        serial_hex=f"0x{data.get('id', 0):06X}",
        raw_data=data,
    )


def _decode_toyota(data: dict) -> Optional[DecodedSignal]:
    """Decode Toyota keyfob / TPMS."""
    model = data.get("model", "")
    if "Toyota" not in model and "toyota" not in model.lower():
        return None

    return DecodedSignal(
        protocol="Toyota",
        manufacturer="Toyota",
        encryption="Rolling Code",
        encoding="Manchester",
        rf_modulation="AM",
        serial=data.get("id", 0),
        serial_hex=f"0x{data.get('id', 0):06X}",
        raw_data=data,
    )


def _decode_honda(data: dict) -> Optional[DecodedSignal]:
    """Decode Honda keyfob (CVE-2022-27254 vulnerable)."""
    model = data.get("model", "")
    if "Honda" not in model and "honda" not in model.lower():
        return None

    return DecodedSignal(
        protocol="Honda",
        manufacturer="Honda",
        encryption="Rolling Code",
        encoding="Manchester",
        rf_modulation="AM",
        serial=data.get("id", 0),
        serial_hex=f"0x{data.get('id', 0):06X}",
        raw_data=data,
    )


def _decode_nissan(data: dict) -> Optional[DecodedSignal]:
    """Decode Nissan keyfob."""
    model = data.get("model", "")
    if "Nissan" not in model and "nissan" not in model.lower():
        return None

    return DecodedSignal(
        protocol="Nissan",
        manufacturer="Nissan",
        encryption="Rolling Code",
        encoding="PWM",
        rf_modulation="AM",
        serial=data.get("id", 0),
        serial_hex=f"0x{data.get('id', 0):06X}",
        raw_data=data,
    )


def _decode_generic_keyfob(data: dict) -> Optional[DecodedSignal]:
    """Decode generic keyfob / remote with KeeLoq fallback."""
    model = data.get("model", "Unknown")

    # Only try for models that look like keyfobs/remotes
    keyfob_keywords = [
        "keyfob", "remote", "key", "fob", "car", "alarm",
        "gate", "garage", "doorbell", "security", "came",
        "nice", "faac", "bft", "ditec",
    ]
    if not any(kw in model.lower() for kw in keyfob_keywords):
        return None

    sig = DecodedSignal(
        protocol=f"Generic ({model})",
        manufacturer="Unknown",
        encoding="PWM",
        rf_modulation="AM",
        raw_data=data,
    )

    if "id" in data:
        try:
            sig.serial = int(data["id"]) if isinstance(data["id"], int) else int(str(data["id"]), 0)
            sig.serial_hex = f"0x{sig.serial:06X}"
        except (ValueError, TypeError):
            pass

    sig.button = data.get("button_code", data.get("cmd", 0))
    sig.button_name = BUTTON_NAMES.get(sig.button, f"Unknown({sig.button})")

    _try_keeloq_decode(sig, data)

    return sig


# ──────────────────────────────────────────────────────
#  KeeLoq Generic Fallback
# ──────────────────────────────────────────────────────

def _try_keeloq_decode(sig: DecodedSignal, data: dict) -> bool:
    """Try to decode the signal's encrypted portion with every key in the keystore."""
    data_hex = data.get("data", "")
    if isinstance(data_hex, dict):
        data_hex = data_hex.get("data", "")
    if not isinstance(data_hex, str) or len(data_hex) < 8:
        return False

    try:
        # Try to extract the 32-bit encrypted hopping portion
        hex_clean = data_hex.replace("0x", "").replace(" ", "")
        if len(hex_clean) < 8:
            return False
        encrypted = int(hex_clean[:8], 16)
    except (ValueError, TypeError):
        return False

    keystore = Keystore()
    for i, key_entry in enumerate(keystore.get_all_keys()):
        try:
            dev_key = KeeLoq.derive_normal_key(sig.serial, key_entry["key"])
            decoded = KeeLoq.decode_hopping(encrypted, dev_key)

            # Validate: discrimination should match lower 10 bits of serial
            expected_disc = sig.serial & 0x3FF
            if decoded["discrimination"] == expected_disc:
                sig.encryption = "KeeLoq"
                sig.counter = decoded["counter"]
                sig.key_name = key_entry["name"]
                sig.key_index = i
                sig.encoder_capable = True
                sig.decoded_fields = decoded
                if decoded["button"]:
                    sig.button = decoded["button"]
                    sig.button_name = BUTTON_NAMES.get(sig.button, f"Unknown({sig.button})")
                logger.info(f"KeeLoq decoded with key {key_entry['name']}: counter={decoded['counter']}")
                return True
        except Exception:
            continue

    return False


# ──────────────────────────────────────────────────────
#  Protocol Registry
# ──────────────────────────────────────────────────────

# Ordered list of decoders (specific first, generic last)
PROTOCOL_REGISTRY = [
    ("Kia", _decode_kia),
    ("Ford", _decode_ford),
    ("Fiat", _decode_fiat),
    ("Subaru", _decode_subaru),
    ("Suzuki", _decode_suzuki),
    ("VAG", _decode_vag),
    ("PSA", _decode_psa),
    ("Scher-Khan", _decode_scher_khan),
    ("Star Line", _decode_starline),
    ("Toyota", _decode_toyota),
    ("Honda", _decode_honda),
    ("Nissan", _decode_nissan),
    ("Generic Keyfob", _decode_generic_keyfob),
]


def decode_signal(data: dict) -> Optional[DecodedSignal]:
    """
    Try all protocol decoders on a signal. Returns the first successful match.
    Falls back to KeeLoq generic if no specific protocol matches.
    """
    for name, decoder in PROTOCOL_REGISTRY:
        try:
            result = decoder(data)
            if result is not None:
                return result
        except Exception as e:
            logger.error(f"Decoder {name} error: {e}")

    return None
