"""
Protocol Features - capability classification, GitHub tooling suggestions,
and structured editors for protocols we can actually synthesize.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from backend.payload_generator import PayloadGenerator


TOOL_CATALOG: Dict[str, Dict[str, Any]] = {
    "rtl_433": {
        "id": "rtl_433",
        "name": "rtl_433",
        "repo": "https://github.com/merbanan/rtl_433",
        "tags": ["decode", "sensor", "sub-ghz"],
        "summary": "Large decoder set for weather sensors, switches, TPMS, meters, alarms, and many 433/868/915 MHz protocols.",
    },
    "urh": {
        "id": "urh",
        "name": "Universal Radio Hacker",
        "repo": "https://github.com/jopohl/urh",
        "tags": ["analyze", "edit", "demod"],
        "summary": "Waveform-first SDR workbench for demodulation, labeling, protocol inspection, and manual signal editing.",
    },
    "gnuradio": {
        "id": "gnuradio",
        "name": "GNU Radio",
        "repo": "https://github.com/gnuradio/gnuradio",
        "tags": ["dsp", "advanced", "pipeline"],
        "summary": "Use when you need custom DSP graphs, bespoke decoders, or non-trivial TX/RX pipelines.",
    },
    "sigdigger": {
        "id": "sigdigger",
        "name": "SigDigger",
        "repo": "https://github.com/BatchDrake/SigDigger",
        "tags": ["spectrum", "inspect", "record"],
        "summary": "Fast spectrum browser and recorder for discovering unknown emitters before deeper protocol work.",
    },
    "gqrx": {
        "id": "gqrx",
        "name": "gqrx",
        "repo": "https://github.com/gqrx-sdr/gqrx",
        "tags": ["receiver", "scan", "monitor"],
        "summary": "Practical SDR receiver front-end for tuning, listening, and validating live RF activity.",
    },
    "soapysdr": {
        "id": "soapysdr",
        "name": "SoapySDR",
        "repo": "https://github.com/pothosware/SoapySDR",
        "tags": ["hardware", "driver", "sdr"],
        "summary": "Hardware abstraction layer that helps when a workflow needs broader SDR device support.",
    },
    "flipper_firmware": {
        "id": "flipper_firmware",
        "name": "Flipper Zero Firmware",
        "repo": "https://github.com/flipperdevices/flipperzero-firmware",
        "tags": ["flipper", "sub-ghz", "portable"],
        "summary": "Best match for the many Flipper-branded protocol entries and Sub-GHz interoperability workflows.",
    },
    "qflipper": {
        "id": "qflipper",
        "name": "qFlipper",
        "repo": "https://github.com/flipperdevices/qFlipper",
        "tags": ["flipper", "desktop", "manage"],
        "summary": "Desktop management tool for moving captures, firmware, and assets onto Flipper devices.",
    },
    "rc_switch": {
        "id": "rc_switch",
        "name": "rc-switch",
        "repo": "https://github.com/sui77/rc-switch",
        "tags": ["switch", "ook", "tx"],
        "summary": "Arduino/RPi transmitter and decoder toolkit for classic fixed-code OOK switch families.",
    },
    "utils_433": {
        "id": "utils_433",
        "name": "433Utils",
        "repo": "https://github.com/ninjablocks/433Utils",
        "tags": ["switch", "gpio", "cli"],
        "summary": "CLI-oriented companion tooling for common 433 MHz switch families on Raspberry Pi style setups.",
    },
    "openmqttgateway": {
        "id": "openmqttgateway",
        "name": "OpenMQTTGateway",
        "repo": "https://github.com/1technophile/OpenMQTTGateway",
        "tags": ["mqtt", "gateway", "sensor"],
        "summary": "Bridge RF sensors and switches into MQTT and Home Assistant without writing custom glue.",
    },
    "esphome": {
        "id": "esphome",
        "name": "ESPHome",
        "repo": "https://github.com/esphome/esphome",
        "tags": ["esp", "home-assistant", "integration"],
        "summary": "Useful when decoded RF signals need to become maintainable Home Assistant entities.",
    },
    "proxmark3": {
        "id": "proxmark3",
        "name": "Proxmark3",
        "repo": "https://github.com/RfidResearchGroup/proxmark3",
        "tags": ["nfc", "rfid", "hf"],
        "summary": "Primary open-source toolchain for NFC and RFID families such as ISO14443-class protocols.",
    },
    "gr_ieee80211": {
        "id": "gr_ieee80211",
        "name": "gr-ieee802-11",
        "repo": "https://github.com/bastibl/gr-ieee802-11",
        "tags": ["wifi", "ofdm", "802.11"],
        "summary": "802.11 OFDM transceiver blocks for GNU Radio when Wi-Fi protocol work goes beyond basic inspection.",
    },
    "gr_lora_sdr": {
        "id": "gr_lora_sdr",
        "name": "gr-lora_sdr",
        "repo": "https://github.com/tapparelj/gr-lora_sdr",
        "tags": ["lora", "css", "gnuradio"],
        "summary": "GNU Radio LoRa PHY blocks for CSS/LoRa entries that need more than raw IQ replay.",
    },
}


_EDITABLE_FAMILY_TOKENS = ("nexa", "proove", "klikaanklikuit", "kaku")
_SWITCH_FAMILY_TOKENS = _EDITABLE_FAMILY_TOKENS + (
    "intertechno",
    "pt2262",
    "ev1527",
    "switch",
    "doorbell",
    "remote control",
    "remote",
    "garage",
)
_SENSOR_TOKENS = (
    "sensor",
    "weather",
    "temperature",
    "humidity",
    "rain",
    "meter",
    "thermometer",
    "environment",
    "hvac",
    "soil",
    "leak",
)
_TPMS_TOKENS = ("tpms", "tire pressure", "tire")
_AUTOMOTIVE_TOKENS = (
    "keyfob",
    "automotive",
    "car alarm",
    "keeloq",
    "rolling code",
    "vehicle",
)
_NFC_TOKENS = ("nfc", "rfid", "iso14443", "mifare")
_WIFI_TOKENS = ("wifi", "802.11", "ofdm")
_LORA_TOKENS = ("lora", "lorawan", "css")
_FLIPPER_TOKENS = ("flipper",)


def _as_lower_text(*values: Any) -> str:
    parts: List[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            parts.extend(str(item) for item in value if item is not None)
        elif isinstance(value, dict):
            parts.extend(f"{k} {v}" for k, v in value.items())
        else:
            parts.append(str(value))
    return " ".join(parts).lower()


def _to_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return default
        try:
            if v.lower().startswith("0x"):
                return int(v, 16)
            return int(float(v))
        except Exception:
            return default
    return default


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "protocol"


def _has_any(text: str, tokens: Iterable[str]) -> bool:
    return any(token in text for token in tokens)


def _protocol_text(protocol_info: Optional[dict], signal: Optional[dict] = None) -> str:
    proto = protocol_info or {}
    sig = signal or {}
    return _as_lower_text(
        proto.get("name"),
        proto.get("category"),
        proto.get("description"),
        proto.get("devices"),
        proto.get("security"),
        proto.get("security_level"),
        proto.get("modulation"),
        proto.get("encoding"),
        sig.get("model"),
        sig.get("modulation"),
        sig.get("data"),
    )


def _is_nexa_like_text(text: str) -> bool:
    return _has_any(text, _EDITABLE_FAMILY_TOKENS)


def recommend_tools(protocol_info: Optional[dict], signal: Optional[dict] = None) -> List[Dict[str, Any]]:
    proto = protocol_info or {}
    sig = signal or {}
    text = _protocol_text(proto, sig)
    category = str(proto.get("category", "")).lower()
    modulation = str(proto.get("modulation", sig.get("modulation", ""))).lower()
    encoding = str(proto.get("encoding", "")).lower()
    security = str(proto.get("security", "")).lower()

    scored: Dict[str, Dict[str, Any]] = {}

    def add(tool_id: str, score: int, why: str) -> None:
        tool = TOOL_CATALOG[tool_id]
        item = scored.setdefault(
            tool_id,
            {
                **tool,
                "score": 0,
                "reasons": [],
            },
        )
        item["score"] = max(item["score"], score)
        if why not in item["reasons"]:
            item["reasons"].append(why)

    add("urh", 90, "Best general-purpose waveform and protocol workbench")
    add("sigdigger", 62, "Useful for discovery, recording, and quick RF inspection")
    add("gqrx", 52, "Good live receiver front-end when validating activity on-air")

    if _has_any(text, _FLIPPER_TOKENS):
        add("flipper_firmware", 97, "Protocol entry explicitly references Flipper ecosystems")
        add("qflipper", 84, "Best desktop companion for Flipper-focused workflows")

    if _has_any(text, _NFC_TOKENS) or "iso14443" in encoding:
        add("proxmark3", 99, "NFC/RFID protocols need a specialist HF toolchain")

    if _has_any(text, _WIFI_TOKENS) or "ofdm" in modulation:
        add("gr_ieee80211", 98, "Wi-Fi/OFDM entries benefit from IEEE 802.11 GNU Radio blocks")
        add("gnuradio", 94, "Needed for non-trivial OFDM signal processing")
        add("soapysdr", 60, "Hardware abstraction helps once you move beyond RTL-SDR class gear")

    if _has_any(text, _LORA_TOKENS) or "css" in modulation:
        add("gr_lora_sdr", 98, "LoRa/CSS entries map well to dedicated GNU Radio PHY blocks")
        add("gnuradio", 94, "LoRa PHY work generally needs custom DSP graphs")
        add("soapysdr", 60, "Useful when the LoRa lab setup uses varied SDR hardware")

    if (
        _has_any(text, _TPMS_TOKENS)
        or _has_any(text, _SENSOR_TOKENS)
        or any(token in category for token in ("weather", "environment", "energy", "iot", "hvac"))
        or any(token in modulation for token in ("ook", "ask", "fsk", "gfsk"))
    ):
        add("rtl_433", 96, "Natural fit for sub-GHz sensors, TPMS, meters, and many ASK/OOK/FSK families")

    if _has_any(text, _SENSOR_TOKENS) or any(token in category for token in ("weather", "environment", "energy", "iot", "hvac")):
        add("openmqttgateway", 83, "Strong bridge option for sensor-style captures and automation flows")
        add("esphome", 70, "Useful when decoded measurements should become maintainable HA entities")

    if _has_any(text, _SWITCH_FAMILY_TOKENS) or any(token in category for token in ("home automation", "remote control", "access control")):
        add("rc_switch", 90, "Fixed-code switch and remote families are commonly modeled here")
        add("utils_433", 77, "Good lightweight CLI tooling for 433 MHz switch workflows")
        add("openmqttgateway", 74, "Useful when the end goal is switching/automation rather than raw analysis")

    if _has_any(text, _AUTOMOTIVE_TOKENS) or "automotive" in category:
        add("gnuradio", 78, "Automotive captures often need protocol-specific DSP and framing work")
        add("urh", 95, "Best manual analysis path for automotive keyfobs and odd encodings")
        if _has_any(text, _TPMS_TOKENS):
            add("rtl_433", 98, "TPMS is heavily represented in rtl_433 decoders")

    if security in {"rolling_code", "encrypted"} or _has_any(text, ("rolling code", "encrypted", "keeloq")):
        add("urh", 97, "Best used here for inspection and labeling rather than generic payload editing")
        add("gnuradio", 80, "Needed once protocol-specific framing or crypto-adjacent analysis becomes necessary")

    if "various" in modulation or "unknown" in encoding:
        add("gnuradio", 58, "Fallback when canned decoders stop helping")
        add("soapysdr", 50, "Useful when the workflow outgrows a single SDR vendor stack")

    return sorted(scored.values(), key=lambda item: (-item["score"], item["name"]))


def get_protocol_support_profile(protocol_info: Optional[dict]) -> Dict[str, Any]:
    proto = protocol_info or {}
    text = _protocol_text(proto)
    modulation = str(proto.get("modulation", "")).lower()
    encoding = str(proto.get("encoding", "")).lower()
    security = _as_lower_text(proto.get("security"), proto.get("security_level"))

    if _is_nexa_like_text(text):
        return {
            "tier": "editable",
            "label": "Structured Edit",
            "summary": "SignalPirate has a field-level editor for this fixed-code switch family.",
            "structured_editor": True,
            "editor_id": "nexa_switch",
            "raw_replay_requires_capture": True,
        }

    if (
        _has_any(text, ("rolling code", "keeloq", "encrypted", "hopping"))
        or _has_any(security, ("critical", "broken", "encrypted", "rolling"))
        or ("automotive" in text and not _has_any(text, _TPMS_TOKENS))
        or _has_any(text, ("access control", "alarm"))
    ):
        return {
            "tier": "research",
            "label": "Research / Capture",
            "summary": "Capture and inspect the waveform first. Reliable editing or replay usually needs protocol-specific work.",
            "structured_editor": False,
            "editor_id": None,
            "raw_replay_requires_capture": True,
        }

    if modulation and modulation != "various" or (encoding and encoding != "unknown") or proto.get("category"):
        return {
            "tier": "replay",
            "label": "Replay After Capture",
            "summary": "Capture the waveform first, then replay raw IQ or inspect it with external tooling.",
            "structured_editor": False,
            "editor_id": None,
            "raw_replay_requires_capture": True,
        }

    return {
        "tier": "research",
        "label": "Research Only",
        "summary": "Metadata is present, but SignalPirate does not have a structured encoder path for this entry yet.",
        "structured_editor": False,
        "editor_id": None,
        "raw_replay_requires_capture": True,
    }


def enrich_protocol_record(protocol_info: dict, index: int | None = None) -> Dict[str, Any]:
    proto = dict(protocol_info)
    support = get_protocol_support_profile(proto)
    proto["slug"] = _slugify(str(proto.get("name", f"protocol-{index or 0}")))
    if index is not None:
        proto["index"] = index
    proto["support"] = support
    proto["tools"] = recommend_tools(proto)
    return proto


def build_protocol_catalog(protocols: Iterable[dict]) -> List[Dict[str, Any]]:
    enriched = [enrich_protocol_record(proto, index=i) for i, proto in enumerate(protocols)]
    enriched.sort(key=lambda item: (item.get("category", ""), item.get("name", "")))
    return enriched


def _is_nexa_switch_family(signal: dict, protocol_info: Optional[dict]) -> bool:
    data = signal.get("data") if isinstance(signal.get("data"), dict) else {}
    text = _protocol_text(protocol_info or {}, signal)
    required = {"id", "unit", "state"}
    if not required.issubset(set(data.keys())):
        return False
    return _is_nexa_like_text(text) or all(key in data for key in ("channel", "group"))


def get_protocol_editor_schema(signal: dict) -> Optional[Dict[str, Any]]:
    protocol_info = signal.get("protocol_info") if isinstance(signal.get("protocol_info"), dict) else {}
    data = signal.get("data") if isinstance(signal.get("data"), dict) else {}

    if _is_nexa_switch_family(signal, protocol_info):
        unit = max(1, min(4, _to_int(data.get("unit"), 1)))
        channel = max(1, min(4, _to_int(data.get("channel"), 1)))
        group = bool(_to_int(data.get("group"), 0))
        state = str(data.get("state", "OFF")).upper()
        if state not in {"ON", "OFF"}:
            state = "OFF"
        return {
            "id": "nexa_switch",
            "title": "Nexa / Proove / KaKu Switch",
            "experimental": True,
            "summary": "Structured editor for fixed-code switch captures that expose id/unit/channel/group/state fields.",
            "notes": [
                "Replay Raw is the most faithful path when you only want to retransmit the original burst.",
                "Edited variants are synthesized from decoded fields and timing templates, not cloned sample-for-sample IQ.",
            ],
            "fields": [
                {
                    "name": "id",
                    "label": "House ID",
                    "type": "number",
                    "min": 0,
                    "max": 0x3FFFFFF,
                    "value": _to_int(data.get("id"), 0),
                },
                {
                    "name": "channel",
                    "label": "Channel",
                    "type": "number",
                    "min": 1,
                    "max": 4,
                    "value": channel,
                },
                {
                    "name": "unit",
                    "label": "Unit",
                    "type": "number",
                    "min": 1,
                    "max": 4,
                    "value": unit,
                },
                {
                    "name": "group",
                    "label": "Group",
                    "type": "boolean",
                    "value": group,
                },
                {
                    "name": "state",
                    "label": "State",
                    "type": "select",
                    "value": state,
                    "options": ["ON", "OFF"],
                },
            ],
        }

    return None


def get_signal_capabilities(signal: dict) -> Dict[str, Any]:
    protocol_info = signal.get("protocol_info") if isinstance(signal.get("protocol_info"), dict) else {}
    support = get_protocol_support_profile(protocol_info)
    editor = get_protocol_editor_schema(signal)
    has_iq = bool(signal.get("iq_file"))
    replay_mode = "raw" if has_iq else ("structured" if editor else "none")
    tier = "editable" if editor else ("replay" if has_iq else "view")

    if tier == "editable":
        summary = "Structured editor available; raw replay also available." if has_iq else "Structured editor available."
    elif tier == "replay":
        summary = "Raw IQ replay available."
    else:
        summary = "No safe structured TX model yet. Use tooling and exports for analysis."

    return {
        "tier": tier,
        "label": {
            "editable": "Editable",
            "replay": "Replay Raw",
            "view": "View Only",
        }[tier],
        "summary": summary,
        "can_edit": bool(editor),
        "can_replay_raw": has_iq,
        "replay_mode": replay_mode,
        "editor": editor,
        "tools": recommend_tools(protocol_info, signal),
        "protocol_support": support,
    }


def _compose_nexa_switch_logical_bits(house_id: int, channel: int, unit: int, group: int, state_on: bool) -> str:
    house_id = max(0, min(0x3FFFFFF, int(house_id)))
    channel = max(1, min(4, int(channel)))
    unit = max(1, min(4, int(unit)))
    group = 1 if group else 0
    on_bit = 1 if state_on else 0

    b0 = (house_id >> 18) & 0xFF
    b1 = (house_id >> 10) & 0xFF
    b2 = (house_id >> 2) & 0xFF
    b3 = ((house_id & 0x3) << 6) | (group << 5) | (on_bit << 4) | (((channel - 1) ^ 0x03) << 2) | ((unit - 1) ^ 0x03)
    return f"{b0:08b}{b1:08b}{b2:08b}{b3:08b}"


def _manchester_encode(bits: str) -> str:
    return "".join("10" if bit == "1" else "01" for bit in bits)


def _write_variant_metadata(path: Path, signal: dict, variant_info: dict) -> None:
    meta_path = path.with_suffix(path.suffix + ".variant.json")
    payload = {
        "format": "signalpirate-protocol-variant",
        "version": 1,
        "created_at": int(time.time()),
        "source_model": signal.get("model", "Unknown"),
        "source_signal_id": signal.get("_id"),
        "variant": variant_info,
    }
    meta_path.write_text(json.dumps(payload, indent=2))


def build_protocol_variant(signal: dict, edits: dict, output_dir: Path | str) -> Dict[str, Any]:
    schema = get_protocol_editor_schema(signal)
    if not schema:
        raise ValueError("No structured editor is available for this capture")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if schema["id"] == "nexa_switch":
        return _build_nexa_switch_variant(signal, edits, output_dir)

    raise ValueError(f"Unsupported editor type: {schema['id']}")


def _build_nexa_switch_variant(signal: dict, edits: dict, output_dir: Path) -> Dict[str, Any]:
    data = signal.get("data") if isinstance(signal.get("data"), dict) else {}
    house_id = _to_int(edits.get("id", data.get("id")), 0)
    channel = max(1, min(4, _to_int(edits.get("channel", data.get("channel", 1)), 1)))
    unit = max(1, min(4, _to_int(edits.get("unit", data.get("unit", 1)), 1)))
    group = 1 if bool(edits.get("group", _to_int(data.get("group"), 0))) else 0
    state_raw = str(edits.get("state", data.get("state", "OFF"))).strip().upper()
    state_on = state_raw == "ON"

    logical_bits = _compose_nexa_switch_logical_bits(house_id, channel, unit, group, state_on)
    raw_bits = _manchester_encode(logical_bits)

    pulses = [{"level": False, "duration_us": 5000}]
    pulse_width = 260
    short_gap = 270
    long_gap = 1300
    sync_gap = 2700
    repeats = 10
    for _ in range(repeats):
        for bit in raw_bits:
            pulses.append({"level": True, "duration_us": pulse_width})
            pulses.append({"level": False, "duration_us": short_gap if bit == "0" else long_gap})
        pulses.append({"level": True, "duration_us": pulse_width})
        pulses.append({"level": False, "duration_us": sync_gap})

    sample_rate = 1_024_000
    state_slug = "on" if state_on else "off"
    model_slug = str(signal.get("model", "switch")).replace(" ", "_").replace("/", "-")
    filename = f"edited_{model_slug}_{state_slug}_{int(time.time())}.cs8"
    path = output_dir / filename

    PayloadGenerator(sample_rate=sample_rate).generate_from_pulses(pulses, str(path))

    from backend import signal_export

    frequency = int(signal.get("frequency") or signal.get("freq") or 433_920_000)
    signal_export._write_c8_meta(
        str(path),
        sample_rate=sample_rate,
        frequency=frequency,
        target_model=str(signal.get("model", "Unknown")),
        decoded_models=[str(signal.get("model", "Unknown"))],
        model_match=False,
        protocol_name=str((signal.get("protocol_info") or {}).get("name", signal.get("model", "Unknown"))),
        protocol_security=str((signal.get("protocol_info") or {}).get("security", "")),
        protocol_security_level=str((signal.get("protocol_info") or {}).get("security_level", "")),
    )
    signal_export._write_urh_project(
        str(path),
        sample_rate=sample_rate,
        frequency=frequency,
        modulation="OOK",
    )

    variant_info = {
        "editor": "nexa_switch",
        "house_id": house_id,
        "channel": channel,
        "unit": unit,
        "group": group,
        "state": "ON" if state_on else "OFF",
        "logical_bits": logical_bits,
        "raw_bits": raw_bits,
        "timing": {
            "pulse_us": pulse_width,
            "short_gap_us": short_gap,
            "long_gap_us": long_gap,
            "sync_gap_us": sync_gap,
            "repeats": repeats,
        },
    }
    _write_variant_metadata(path, signal, variant_info)
    return {
        "path": str(path),
        "filename": path.name,
        "variant": variant_info,
    }
