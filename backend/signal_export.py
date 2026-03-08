"""
Signal Export/Import — .fob (KAT-compatible), .sub (Flipper Zero), .json formats.

Supports:
  - Export: .fob v2.0, .sub RAW, .json
  - Import: .fob (v1/v2 auto-detect), .sub
"""
import json
import os
import re
import shutil
import subprocess
import time
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger("signalpirate.signal_export")

EXPORT_DIR = os.path.expanduser("~/.config/signalpirate/exports")
IMPORT_DIR = os.path.expanduser("~/.config/signalpirate/imports")
IQ_CAPTURE_DIR = os.path.expanduser("~/.config/signalpirate/iq_captures")
_IQ_EXPORT_USE_COUNT: Dict[str, int] = {}
_IQ_DECODE_CACHE: Dict[str, List[str]] = {}
_UNSIGNED_TO_SIGNED_IQ_TABLE = bytes.maketrans(
    bytes(range(256)),
    bytes((b ^ 0x80) for b in range(256)),
)


def ensure_dirs() -> None:
    os.makedirs(EXPORT_DIR, exist_ok=True)
    os.makedirs(IMPORT_DIR, exist_ok=True)


# ──────────────────────────────────────────────────────
#  .fob Export/Import (KAT-compatible)
# ──────────────────────────────────────────────────────

def export_fob(signal_data: dict, vehicle_info: dict = None,
               decoded: dict = None, filename: str = None) -> str:
    """Export signal as KAT-compatible .fob v2.0 JSON."""
    ensure_dirs()

    model = signal_data.get("model", "Unknown")
    ts = signal_data.get("timestamp", time.time())
    freq = _normalize_frequency(signal_data.get("frequency", signal_data.get("freq", 433920000)))

    if not filename:
        safe_model = _safe_filename(model)
        filename = f"{safe_model}_{int(ts)}.fob"

    filepath = os.path.join(EXPORT_DIR, filename)

    fob = {
        "version": "2.0",
        "format": "kat-fob",
        "signal": {
            "protocol": decoded.get("protocol", model) if decoded else model,
            "frequency": freq,
            "frequency_mhz": f"{freq / 1e6:.2f}MHz",
            "modulation": decoded.get("encoding", "PWM") if decoded else "PWM",
            "rf_modulation": decoded.get("rf_modulation", "AM") if decoded else "AM",
            "encryption": decoded.get("encryption", "Unknown") if decoded else "Unknown",
            "data_bits": decoded.get("data_bits", 0) if decoded else 0,
            "data_hex": decoded.get("data_hex", "") if decoded else "",
            "serial": decoded.get("serial_hex", "") if decoded else "",
            "counter": decoded.get("counter", 0) if decoded else 0,
            "button": decoded.get("button", 0) if decoded else 0,
            "button_name": decoded.get("button_name", "") if decoded else "",
            "crc_valid": decoded.get("crc_valid") if decoded else None,
            "encoder_capable": decoded.get("encoder_capable", False) if decoded else False,
        },
        "vehicle": vehicle_info or {},
        "capture": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)),
            "raw_data_hex": signal_data.get("data", {}).get("data", "") if isinstance(signal_data.get("data"), dict) else str(signal_data.get("data", "")),
            "raw_pair_count": len(signal_data.get("pulses", [])),
            "raw_pairs": signal_data.get("pulses", []),
        },
    }

    with open(filepath, "w") as f:
        json.dump(fob, f, indent=2, default=str)

    logger.info(f"Exported .fob: {filepath}")
    return filepath


def import_fob(filepath: str) -> Optional[dict]:
    """Import a .fob file (v1/v2 auto-detect)."""
    try:
        with open(filepath, "r") as f:
            data = json.load(f)

        version = data.get("version", "1.0")
        fmt = data.get("format", "")

        if fmt == "kat-fob" or version.startswith("2"):
            return _import_fob_v2(data)
        else:
            return _import_fob_v1(data)

    except Exception as e:
        logger.error(f"Failed to import .fob: {e}")
        return None


def _import_fob_v2(data: dict) -> dict:
    """Import KAT .fob v2.0 format."""
    sig = data.get("signal", {})
    capture = data.get("capture", {})
    vehicle = data.get("vehicle", {})

    return {
        "model": sig.get("protocol", "Unknown"),
        "frequency": sig.get("frequency", 433920000),
        "modulation": sig.get("modulation", "PWM"),
        "encryption": sig.get("encryption", "Unknown"),
        "serial": sig.get("serial", ""),
        "counter": sig.get("counter", 0),
        "button": sig.get("button", 0),
        "button_name": sig.get("button_name", ""),
        "data_hex": sig.get("data_hex", ""),
        "raw_data": capture.get("raw_data_hex", ""),
        "pulses": capture.get("raw_pairs", []),
        "vehicle": vehicle,
        "timestamp": capture.get("timestamp", ""),
        "encoder_capable": sig.get("encoder_capable", False),
        "source": "fob_v2",
    }


def _import_fob_v1(data: dict) -> dict:
    """Import legacy .fob v1 format."""
    return {
        "model": data.get("protocol", data.get("model", "Unknown")),
        "frequency": data.get("frequency", 433920000),
        "data_hex": data.get("data", ""),
        "serial": data.get("serial", ""),
        "source": "fob_v1",
    }


# ──────────────────────────────────────────────────────
#  .sub Export/Import (Flipper Zero)
# ──────────────────────────────────────────────────────

def export_flipper_sub(signal_data: dict, filename: str = None) -> str:
    """Export signal as Flipper Zero .sub RAW file."""
    ensure_dirs()

    model = signal_data.get("model", "Unknown")
    freq = _normalize_frequency(signal_data.get("frequency", signal_data.get("freq", 433920000)))
    mod = signal_data.get("modulation", signal_data.get("mod", "OOK"))
    ts = signal_data.get("timestamp", time.time())

    if not filename:
        filename = f"{_safe_filename(model)}_{int(ts)}.sub"

    filepath = os.path.join(EXPORT_DIR, filename)

    preset = "FuriHalSubGhzPreset2FSKDev238Async" if "FSK" in mod.upper() else "FuriHalSubGhzPresetOok650Async"

    lines = [
        "Filetype: Flipper SubGhz RAW File",
        "Version: 1",
        f"# Exported by SignalPirate v3.0",
        f"# Model: {model}",
        f"# Time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))}",
        f"Frequency: {freq}",
        f"Preset: {preset}",
        "Protocol: RAW",
    ]

    # Generate RAW_Data from pulses or hex data
    pulses = signal_data.get("data", {}).get("pulses", []) if isinstance(signal_data.get("data"), dict) else []
    if not pulses:
        pulses = signal_data.get("pulses", [])

    if pulses:
        raw_vals = []
        for p in pulses:
            dur = int(p.get("duration_us", 500))
            raw_vals.append(str(dur) if p.get("level", 0) else str(-dur))
        for i in range(0, len(raw_vals), 512):
            lines.append(f"RAW_Data: {' '.join(raw_vals[i:i+512])}")
    else:
        data_hex = _extract_hex(signal_data)
        if data_hex:
            bits = bin(int(data_hex, 16))[2:]
            raw_vals = []
            for bit in bits:
                raw_vals.extend(["500", "-250"] if bit == "1" else ["250", "-500"])
            raw_vals.append("-10000")
            lines.append(f"RAW_Data: {' '.join(raw_vals)}")
        else:
            lines.append("RAW_Data: 500 -500 500 -500 500 -1000")

    with open(filepath, "w") as f:
        f.write("\n".join(lines) + "\n")

    logger.info(f"Exported .sub: {filepath}")
    return filepath


def import_flipper_sub(filepath: str) -> Optional[dict]:
    """Import a Flipper Zero .sub file."""
    try:
        with open(filepath, "r") as f:
            content = f.read()

        result: Dict[str, Any] = {"source": "flipper_sub", "model": "Flipper Import"}
        pulses: List[dict] = []

        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("Frequency:"):
                result["frequency"] = int(line.split(":")[1].strip())
            elif line.startswith("Preset:"):
                preset = line.split(":")[1].strip()
                result["modulation"] = "FSK" if "FSK" in preset else "OOK"
            elif line.startswith("RAW_Data:"):
                raw = line.split(":")[1].strip().split()
                for val in raw:
                    try:
                        v = int(val)
                        pulses.append({
                            "level": v > 0,
                            "duration_us": abs(v),
                        })
                    except ValueError:
                        continue

        result["pulses"] = pulses
        result["timestamp"] = time.time()
        return result

    except Exception as e:
        logger.error(f"Failed to import .sub: {e}")
        return None


# ──────────────────────────────────────────────────────
#  .cs8 Export (HackRF Native IQ)
# ──────────────────────────────────────────────────────

def export_hackrf_c8(signal_data: dict, filename: str = None) -> str:
    """Export signal directly to signed complex 8-bit IQ (.cs8) for HackRF/URH."""
    ensure_dirs()
    from backend.payload_generator import PayloadGenerator
    
    gen = PayloadGenerator()
    model = signal_data.get("model", "Unknown")
    ts_raw = signal_data.get("time", signal_data.get("timestamp", time.time()))
    try:
        ts = float(ts_raw)
    except Exception:
        ts = time.time()
    
    if not filename:
        filename = f"{_safe_filename(model)}_{int(ts)}.cs8"
    else:
        filename = _normalize_iq_export_filename(filename)
        
    filepath = os.path.join(EXPORT_DIR, filename)
    freq_hz = _normalize_frequency(signal_data.get("frequency", signal_data.get("freq", 433_920_000)))
    protocol_info = signal_data.get("protocol_info") if isinstance(signal_data.get("protocol_info"), dict) else {}
    protocol_name = str(protocol_info.get("name", "")).strip()
    protocol_security = str(protocol_info.get("security", "")).strip()
    protocol_security_level = str(protocol_info.get("security_level", "")).strip()

    # Best fidelity path: convert rtl_433 autosaved IQ capture when available.
    iq_file = signal_data.get("iq_file")
    
    # If the frontend sent an absolute path from an old session under a different user 
    # (e.g., /home/sam/...) but we are now running as a systemd service (/opt/...),
    # extract just the filename and look for it in the current IQ_CAPTURE_DIR.
    if isinstance(iq_file, str) and "/" in iq_file:
        fallback_path = os.path.join(IQ_CAPTURE_DIR, os.path.basename(iq_file))
        if os.path.isfile(fallback_path):
            iq_file = fallback_path
            
    if not (isinstance(iq_file, str) and os.path.isfile(iq_file)):
        iq_file = _find_matching_iq_capture(freq_hz=freq_hz, signal_ts=ts)
        if iq_file:
            logger.info("Matched IQ capture for export: %s", iq_file)
            
        # If we STILL don't have an IQ file, try to find a nearby one that decodes
        if not iq_file:
            iq_file = _choose_iq_for_export(
                model=model,
                signal_ts=ts,
                freq_hz=freq_hz,
                preferred_iq="",
            )
    if isinstance(iq_file, str) and os.path.isfile(iq_file):
        decoded_models = _decode_models_from_iq(iq_file)
        model_lc = str(model or "").strip().lower()
        model_match = any(str(m).strip().lower() == model_lc for m in decoded_models) if model_lc else False
        # Reject clearly wrong IQ mapping (explicitly decodes to other models).
        # Keep empty-decoder IQ files, since some valid captures still replay well.
        if model_lc and decoded_models and not model_match:
            logger.warning(
                "IQ candidate rejected for %s: %s decodes as %s",
                model,
                iq_file,
                ",".join(decoded_models),
            )
        else:
            _IQ_EXPORT_USE_COUNT[iq_file] = _IQ_EXPORT_USE_COUNT.get(iq_file, 0) + 1
            sample_rate = _guess_iq_sample_rate_hz(iq_file)
            out_rate = _convert_iq_to_c8(iq_file, filepath, sample_rate=sample_rate)
            # Replay IQ at its capture center frequency to preserve original offsets.
            iq_center = _parse_iq_center_freq_hz(iq_file)
            tx_freq = iq_center if iq_center is not None else freq_hz
            _write_c8_meta(
                filepath,
                sample_rate=out_rate,
                frequency=tx_freq,
                source_iq=iq_file,
                target_model=str(model or ""),
                decoded_models=decoded_models,
                model_match=model_match,
                protocol_name=protocol_name,
                protocol_security=protocol_security,
                protocol_security_level=protocol_security_level,
            )
            _write_urh_project(
                filepath, 
                sample_rate=out_rate, 
                frequency=tx_freq,
                modulation=signal_data.get("modulation", signal_data.get("mod", "OOK"))
            )
            logger.info(f"Exported .cs8 from captured IQ: {filepath}")
            return filepath
    
    pulses = signal_data.get("data", {}).get("pulses", []) if isinstance(signal_data.get("data"), dict) else []
    if not pulses and "pulses" in signal_data:
        pulses = signal_data["pulses"]
        
    if pulses:
        gen.generate_from_pulses(pulses, filepath)
        _write_c8_meta(
            filepath,
            sample_rate=gen.sample_rate,
            frequency=freq_hz,
            target_model=str(model or ""),
            decoded_models=[],
            model_match=False,
            protocol_name=protocol_name,
            protocol_security=protocol_security,
            protocol_security_level=protocol_security_level,
        )
        _write_urh_project(
            filepath, 
            sample_rate=gen.sample_rate, 
            frequency=freq_hz,
            modulation=signal_data.get("modulation", signal_data.get("mod", "OOK"))
        )
    else:
        # Fallback to reconstructing from hex
        data_hex = _extract_hex(signal_data)
        if data_hex:
            try:
                bits = bin(int(data_hex, 16))[2:]
                gen.generate_ook_iq(bits, 2000, filepath)
                _write_c8_meta(
                    filepath,
                    sample_rate=gen.sample_rate,
                    frequency=freq_hz,
                    target_model=str(model or ""),
                    decoded_models=[],
                    model_match=False,
                    protocol_name=protocol_name,
                    protocol_security=protocol_security,
                    protocol_security_level=protocol_security_level,
                )
                _write_urh_project(
                    filepath, 
                    sample_rate=gen.sample_rate, 
                    frequency=freq_hz,
                    modulation="OOK"
                )
            except Exception:
                gen.generate_ook_iq("10" * 50, 2000, filepath)
                _write_c8_meta(
                    filepath,
                    sample_rate=gen.sample_rate,
                    frequency=freq_hz,
                    target_model=str(model or ""),
                    decoded_models=[],
                    model_match=False,
                    protocol_name=protocol_name,
                    protocol_security=protocol_security,
                    protocol_security_level=protocol_security_level,
                )
                _write_urh_project(
                    filepath, 
                    sample_rate=gen.sample_rate, 
                    frequency=freq_hz,
                    modulation="OOK"
                )
        else:
            gen.generate_ook_iq("10" * 50, 2000, filepath)
            _write_c8_meta(
                filepath,
                sample_rate=gen.sample_rate,
                frequency=freq_hz,
                target_model=str(model or ""),
                decoded_models=[],
                model_match=False,
                protocol_name=protocol_name,
                protocol_security=protocol_security,
                protocol_security_level=protocol_security_level,
            )
            _write_urh_project(
                filepath, 
                sample_rate=gen.sample_rate, 
                frequency=freq_hz,
                modulation="OOK"
            )
            
    logger.info(f"Exported .cs8: {filepath}")
    return filepath


def _convert_iq_to_c8(src_path: str, dst_path: str, sample_rate: int = 2_000_000) -> int:
    """
    Convert/copy IQ data to signed complex 8-bit (.cs8).
    rtl_433 autosaves are typically .cu8 (unsigned offset-binary), while
    HackRF TX and URH expect signed int8 complex samples for .cs8 inputs.
    """
    out_rate = max(1, int(sample_rate))

    with open(src_path, "rb") as src:
        data = src.read()
    
    if len(data) % 2:
        data = data[:-1]
        
    if not data:
        with open(dst_path, "wb") as dst:
            dst.write(b"")
        return out_rate

    payload = data
    src_format = _detect_iq_sample_format(src_path)
    if src_format == "cu8":
        payload = payload.translate(_UNSIGNED_TO_SIGNED_IQ_TABLE)
    elif src_format == "u8":
        logger.warning("Copying unsupported real-valued .u8 source as-is for export: %s", src_path)

    # Trim leading/trailing low-energy IQ to preserve packet timing and reduce
    # replaying unrelated background chunks from autosave files.
    trimmed = _trim_iq_edges(payload, out_rate)
    if trimmed and len(trimmed) < len(payload):
        payload = trimmed

    with open(dst_path, "wb") as dst:
        dst.write(payload)
    return out_rate


def _trim_iq_edges(iq_signed_bytes: bytes, sample_rate: int) -> bytes:
    """
    Remove leading/trailing low-energy regions from signed int8 IQ data.
    Keeps internal packet timing untouched; only trims edges.
    """
    if len(iq_signed_bytes) < 4096:
        return iq_signed_bytes
    if len(iq_signed_bytes) % 2:
        return iq_signed_bytes[:-1]

    pairs = len(iq_signed_bytes) // 2
    step = max(1, pairs // 4096)
    mags_sample: List[int] = []
    for i in range(0, len(iq_signed_bytes), 2 * step):
        ib = iq_signed_bytes[i]
        qb = iq_signed_bytes[i + 1]
        if ib >= 128:
            ib -= 256
        if qb >= 128:
            qb -= 256
        mags_sample.append(abs(ib) + abs(qb))
    if not mags_sample:
        return iq_signed_bytes

    mags_sample.sort()
    noise_floor = mags_sample[int(len(mags_sample) * 0.20)]
    threshold = max(18, int(noise_floor * 2.5))

    first_idx = -1
    last_idx = -1
    for p in range(pairs):
        i = p * 2
        ib = iq_signed_bytes[i]
        qb = iq_signed_bytes[i + 1]
        if ib >= 128:
            ib -= 256
        if qb >= 128:
            qb -= 256
        mag = abs(ib) + abs(qb)
        if mag >= threshold:
            if first_idx < 0:
                first_idx = p
            last_idx = p

    if first_idx < 0 or last_idx < 0:
        return iq_signed_bytes

    # Keep a small guard around edges so packet preamble isn't clipped.
    guard = max(32, int(max(1, sample_rate) * 0.004))
    start = max(0, first_idx - guard)
    end = min(pairs, last_idx + guard)
    if end <= start:
        return iq_signed_bytes

    kept_pairs = end - start
    if kept_pairs >= int(pairs * 0.97):
        return iq_signed_bytes
    if kept_pairs < max(16, int(max(1, sample_rate) * 0.001)):
        return iq_signed_bytes
    return iq_signed_bytes[start * 2:end * 2]


def _guess_iq_sample_rate_hz(path: str) -> int:
    """
    Guess sample rate from rtl_433 autosave filename patterns like *_250k.cu8 or *_1M.cu8.
    Defaults to 250 kS/s (rtl_433 default).
    """
    name = os.path.basename(path)
    m = re.search(r"_(\d+)([kKmM])\.(?:c?u8|c8|cs8)$", name)
    if not m:
        return 250_000
    value = int(m.group(1))
    unit = m.group(2).lower()
    return value * 1_000_000 if unit == "m" else value * 1_000


def _detect_iq_sample_format(path: str) -> str:
    """Infer the sample byte format from the filename extension."""
    name = os.path.basename(path).lower()
    if name.endswith((".complex16u", ".cu8")):
        return "cu8"
    if name.endswith((".complex16s", ".cs8", ".c8")):
        return "cs8"
    if name.endswith(".u8"):
        return "u8"
    return "unknown"


def _normalize_iq_export_filename(filename: str) -> str:
    """Normalize exported IQ filenames to URH-compatible .cs8."""
    root, _ext = os.path.splitext(filename)
    return f"{root}.cs8"


def _parse_iq_center_freq_hz(path: str) -> Optional[int]:
    """
    Parse center frequency from rtl_433 autosave names like g001_433.92M_250k.cu8.
    Returns Hz or None when unknown.
    """
    name = os.path.basename(path)
    m = re.search(r"_(\d+(?:\.\d+)?)M_", name)
    if not m:
        return None
    try:
        return int(float(m.group(1)) * 1_000_000)
    except Exception:
        return None


def _find_matching_iq_capture(freq_hz: int, signal_ts: float) -> Optional[str]:
    """
    Find the best rtl_433 IQ autosave match for a signal by timestamp and center frequency.
    This is a fallback when per-signal iq_file linkage is missing.
    """
    if not os.path.isdir(IQ_CAPTURE_DIR):
        return None

    best_path: Optional[str] = None
    best_score = float("inf")
    now = time.time()

    try:
        for fname in os.listdir(IQ_CAPTURE_DIR):
            if not fname.endswith((".cu8", ".u8", ".c8", ".cs8")):
                continue
            path = os.path.join(IQ_CAPTURE_DIR, fname)
            if not os.path.isfile(path):
                continue
            st = os.stat(path)
            if st.st_size < 4096:
                continue

            # Ignore very old captures for safety.
            age = now - st.st_mtime
            if age > 900:
                continue

            dt = float(st.st_mtime - signal_ts)
            t_diff = abs(dt)
            if t_diff > 180:
                continue

            iq_freq = _parse_iq_center_freq_hz(path)
            if iq_freq is None:
                f_penalty = 30.0
            else:
                f_delta = abs(iq_freq - int(freq_hz))
                if f_delta > 750_000:
                    continue
                f_penalty = f_delta / 25_000.0

            # Prefer files created right after the decoded signal time.
            # Penalize files that are clearly older than the signal.
            if dt < -3.0:
                time_score = t_diff + 12.0
            else:
                time_score = t_diff
            if 0.0 <= dt <= 6.0:
                time_score *= 0.5

            # Avoid repeatedly mapping many exports to one IQ file.
            use_penalty = 6.0 * float(_IQ_EXPORT_USE_COUNT.get(path, 0))

            score = time_score + f_penalty + use_penalty
            if score < best_score:
                best_score = score
                best_path = path
    except Exception:
        return None

    return best_path


def _choose_iq_for_export(model: str, signal_ts: float, freq_hz: int, preferred_iq: str) -> str:
    """
    Choose the best IQ file for export.
    Priority:
      1) Candidate that decodes to the selected model.
      2) Candidate with any decodable model nearest in time.
      3) Preferred IQ / time-frequency match fallback.
    """
    candidates: List[str] = []
    if preferred_iq and os.path.isfile(preferred_iq):
        candidates.append(preferred_iq)

    # Add nearby candidates around capture time/frequency.
    if os.path.isdir(IQ_CAPTURE_DIR):
        now = time.time()
        nearby: List[tuple[float, str]] = []
        try:
            for fname in os.listdir(IQ_CAPTURE_DIR):
                if not fname.endswith((".cu8", ".u8", ".c8", ".cs8")):
                    continue
                path = os.path.join(IQ_CAPTURE_DIR, fname)
                if not os.path.isfile(path):
                    continue
                st = os.stat(path)
                if st.st_size < 4096:
                    continue
                if (now - st.st_mtime) > 1800:
                    continue
                t_diff = abs(st.st_mtime - signal_ts)
                if t_diff > 30:
                    continue
                iq_center = _parse_iq_center_freq_hz(path)
                if iq_center is not None and abs(iq_center - int(freq_hz)) > 1_000_000:
                    continue
                nearby.append((t_diff, path))
        except Exception:
            nearby = []
        nearby.sort(key=lambda x: x[0])
        for _, path in nearby[:8]:
            if path not in candidates:
                candidates.append(path)

    if not candidates:
        return preferred_iq

    target = (model or "").strip().lower()
    best_unknown = ""
    best_unknown_score = float("inf")
    best_mismatch = preferred_iq
    best_mismatch_score = float("inf")

    for path in candidates:
        models = _decode_models_from_iq(path)
        score = abs(os.path.getmtime(path) - signal_ts)
        if target and any(m.lower() == target for m in models):
            if path != preferred_iq:
                logger.info("IQ model-match upgrade: %s -> %s for %s", preferred_iq or "(none)", path, model)
            return path
        if not models and score < best_unknown_score:
            best_unknown_score = score
            best_unknown = path
        if models and score < best_mismatch_score:
            best_mismatch_score = score
            best_mismatch = path

    # If we couldn't prove a match, prefer undecodable (unknown) captures over
    # clearly wrong-model captures.
    if best_unknown:
        return best_unknown
    return best_mismatch or preferred_iq


def _decode_models_from_iq(path: str) -> List[str]:
    """
    Decode one IQ file with rtl_433 and return unique model names.
    Results are cached to keep export latency reasonable.
    """
    if path in _IQ_DECODE_CACHE:
        return _IQ_DECODE_CACHE[path]

    if not shutil.which("rtl_433"):
        _IQ_DECODE_CACHE[path] = []
        return []

    freq = _parse_iq_center_freq_hz(path) or 433_920_000
    sr = _guess_iq_sample_rate_hz(path)
    cmd = [
        "rtl_433",
        "-r", path,
        "-f", str(freq),
        "-s", str(sr),
        "-F", "json",
    ]
    models: List[str] = []
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=6,
            check=False,
        )
        seen = set()
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line or "\"model\"" not in line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            m = obj.get("model")
            if isinstance(m, str) and m and m not in seen:
                seen.add(m)
                models.append(m)
    except Exception:
        models = []

    _IQ_DECODE_CACHE[path] = models
    return models


def _write_c8_meta(
    path: str,
    sample_rate: int,
    frequency: int,
    source_iq: str = "",
    target_model: str = "",
    decoded_models: Optional[List[str]] = None,
    model_match: bool = False,
    protocol_name: str = "",
    protocol_security: str = "",
    protocol_security_level: str = "",
) -> None:
    meta = {
        "format": "signalpirate-c8-meta",
        "version": 1,
        "sample_format": "cs8",
        "sample_rate": int(sample_rate),
        "frequency": int(frequency),
        "source_iq": source_iq,
        "source_sample_format": _detect_iq_sample_format(source_iq) if source_iq else "",
        "target_model": target_model,
        "decoded_models": decoded_models or [],
        "model_match": bool(model_match),
        "protocol_name": protocol_name,
        "protocol_security": protocol_security,
        "protocol_security_level": protocol_security_level,
        "created_at": int(time.time()),
    }
    meta_path = path + ".meta.json"
    try:
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed writing c8 metadata for {path}: {e}")


def _write_urh_project(
    c8_filepath: str,
    sample_rate: int,
    frequency: int,
    modulation: Optional[str] = "OOK"
) -> None:
    """
    Generate a URH (Universal Radio Hacker) project archive next to the exported .cs8
    so that URH opens with the correct sample rate, frequency, and demodulation set.
    """
    import zipfile
    
    c8_filename = os.path.basename(c8_filepath)
    project_dir = os.path.dirname(c8_filepath)
    project_name = os.path.splitext(c8_filename)[0]
    zip_path = os.path.join(project_dir, f"{project_name}.urh.zip")
    
    urh_mod_idx = 1 if "FSK" in str(modulation).upper() else 0
    center_freq_mhz = frequency / 1_000_000.0
    
    xml_content = f"""<?xml version="1.0" ?>
<UniversalRadioHackerProject description="" collapse_project_tabs="0" modulation_was_edited="0" broadcast_address_hex="ffff">
  <signal name="{project_name}" filename="{c8_filename}" samples_per_symbol="100" center="0" center_spacing="0.1" tolerance="5" noise_threshold="0.02" noise_minimum="-0.02" noise_maximum="0.02" modulation_type="{urh_mod_idx}" sample_rate="{sample_rate}" pause_threshold="8" message_length_divisor="1" bits_per_symbol="1" costas_loop_bandwidth="0.1">
    <messages/>
  </signal>
  <open_file name="{c8_filename}" position="0" />
  <group name="New Group" id="0" />
  <protocol>
    <decodings>
      <decoding name="Non Return To Zero (NRZ)">
        <step type="Non Return To Zero (NRZ)" />
      </decoding>
    </decodings>
    <participants />
    <messages />
    <message_types>
      <message_type name="default" id="0">
        <ruleset />
      </message_type>
    </message_types>
  </protocol>
</UniversalRadioHackerProject>
"""
    try:
        # Give the internal contents a parent folder so it extracts cleanly for URH
        project_folder_name = os.path.basename(zip_path).replace(".urh.zip", "")
        
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Place the XML config and the data file INSIDE the project folder
            zf.writestr(f"{project_folder_name}/URHProject.xml", xml_content)
            zf.write(c8_filepath, f"{project_folder_name}/{c8_filename}")
        logger.info(f"Generated URH project archive: {zip_path}")
    except Exception as e:
        logger.warning(f"Failed writing URH project archive for {c8_filepath}: {e}")


# ──────────────────────────────────────────────────────
#  .json Export
# ──────────────────────────────────────────────────────

def export_json(signal_data: dict, protocol_info: dict = None,
                vuln_info: list = None, filename: str = None) -> str:
    """Export signal as detailed JSON."""
    ensure_dirs()

    model = signal_data.get("model", "Unknown")
    ts = signal_data.get("timestamp", time.time())

    if not filename:
        filename = f"{_safe_filename(model)}_{int(ts)}.json"

    filepath = os.path.join(EXPORT_DIR, filename)

    export_data = {
        "version": "3.0",
        "format": "signalpirate",
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "signal": signal_data,
    }
    if protocol_info:
        export_data["protocol"] = protocol_info
    if vuln_info:
        export_data["vulnerabilities"] = vuln_info if isinstance(vuln_info, list) else [vuln_info]

    with open(filepath, "w") as f:
        json.dump(export_data, f, indent=2, default=str)

    logger.info(f"Exported .json: {filepath}")
    return filepath


# ──────────────────────────────────────────────────────
#  Utilities
# ──────────────────────────────────────────────────────

def _normalize_frequency(freq) -> int:
    """Normalize frequency to Hz."""
    if isinstance(freq, float) and freq < 1e6:
        return int(freq * 1e6)
    return int(freq)


def _safe_filename(name: str) -> str:
    """Make a string safe for use as a filename."""
    return name.replace(" ", "_").replace("/", "-").replace("\\", "-")[:30]


def _extract_hex(signal_data: dict) -> str:
    """Try to extract hex data string from signal."""
    data = signal_data.get("data", "")
    if isinstance(data, dict):
        data = data.get("data", "")
    if isinstance(data, str) and data.startswith("0x"):
        return data.replace("0x", "")
    return ""


def list_exports() -> List[dict]:
    """List all exported files."""
    ensure_dirs()
    files = []
    for fname in sorted(os.listdir(EXPORT_DIR)):
        fpath = os.path.join(EXPORT_DIR, fname)
        if os.path.isfile(fpath):
            ext = os.path.splitext(fname)[1]
            files.append({
                "filename": fname,
                "path": fpath,
                "format": ext.lstrip("."),
                "size": os.path.getsize(fpath),
                "modified": os.path.getmtime(fpath),
            })
    return files


def list_imports() -> List[str]:
    """List importable files from the import directory."""
    ensure_dirs()
    result = []
    for fname in sorted(os.listdir(IMPORT_DIR)):
        if fname.endswith((".fob", ".sub")):
            result.append(os.path.join(IMPORT_DIR, fname))
    return result
