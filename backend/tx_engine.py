import asyncio
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Tuple

from backend import payload_generator

logger = logging.getLogger("signalpirate.tx_engine")

_research_mode_enabled = False

HACKRF_MIN_FREQ_HZ = 1_000_000
HACKRF_MAX_FREQ_HZ = 6_000_000_000
HACKRF_MIN_SAMPLE_RATE = 2_000_000
HACKRF_MAX_SAMPLE_RATE = 20_000_000
HACKRF_DEFAULT_SAMPLE_RATE = 2_000_000
HACKRF_DEFAULT_TX_VGA = 20
HACKRF_MAX_TX_VGA = 47
HACKRF_MAX_TX_SECONDS = 8.0
HACKRF_OPEN_RETRIES = 4
AUTO_TUNE_MAX_FILE_SECONDS = 1.75
AUTO_TUNE_VGA_STEPS = (12, 20, 28, 36)
AUTO_TUNE_FREQ_OFFSETS_HZ = (0, -2500, 2500)


def enable_research_mode() -> None:
    global _research_mode_enabled
    _research_mode_enabled = True
    logger.warning("TX RESEARCH MODE ENABLED - Transmissions unlocked.")


def disable_research_mode() -> None:
    global _research_mode_enabled
    _research_mode_enabled = False
    logger.info("TX Research Mode disabled.")


def is_research_mode_enabled() -> bool:
    return _research_mode_enabled


def _sanitize_frequency(frequency: int) -> int:
    value = int(frequency)
    if value < HACKRF_MIN_FREQ_HZ or value > HACKRF_MAX_FREQ_HZ:
        raise ValueError(f"Frequency {value} Hz is outside HackRF TX range")
    return value


def _sanitize_sample_rate(sample_rate: int) -> int:
    value = int(sample_rate)
    if value < HACKRF_MIN_SAMPLE_RATE or value > HACKRF_MAX_SAMPLE_RATE:
        raise ValueError(
            f"Sample rate {value} is outside safe HackRF TX range "
            f"({HACKRF_MIN_SAMPLE_RATE}-{HACKRF_MAX_SAMPLE_RATE})"
        )
    return value


def _sanitize_tx_vga(value: int | None = None) -> int:
    raw = HACKRF_DEFAULT_TX_VGA if value is None else int(value)
    return max(0, min(HACKRF_MAX_TX_VGA, raw))


def _tx_vga_from_env() -> int:
    env = os.environ.get("SIGNALPIRATE_HACKRF_TX_VGA")
    if not env:
        return HACKRF_DEFAULT_TX_VGA
    try:
        return _sanitize_tx_vga(int(env))
    except Exception:
        logger.warning("Invalid SIGNALPIRATE_HACKRF_TX_VGA=%r; using default", env)
        return HACKRF_DEFAULT_TX_VGA


def _validate_tx_file(path: Path, sample_rate: int) -> Tuple[int, float]:
    file_size = path.stat().st_size
    if file_size <= 0:
        raise ValueError("IQ file is empty")
    if file_size % 2 != 0:
        raise ValueError("IQ file length is invalid for complex int8 samples")

    duration_s = file_size / float(sample_rate * 2)
    if duration_s > HACKRF_MAX_TX_SECONDS:
        raise ValueError(
            f"TX file is {duration_s:.2f}s long; refusing to transmit files over "
            f"{HACKRF_MAX_TX_SECONDS:.1f}s in one shot"
        )
    return file_size, duration_s


def _build_hackrf_tx_command(
    path: Path,
    frequency: int,
    sample_rate: int,
    tx_vga: int,
) -> list[str]:
    return [
        "hackrf_transfer",
        "-t", str(path),
        "-f", str(frequency),
        "-s", str(sample_rate),
        "-a", "0",  # Keep PA amp off by default to reduce abuse risk and spurs.
        "-x", str(tx_vga),
    ]


def _normalize_model_name(name: Any) -> str:
    return str(name or "").strip().lower()


def _classify_hackrf_error(stderr_text: str) -> str:
    text = (stderr_text or "").lower()
    if "hackrf_open() failed" in text or "hackrf not found" in text or "hackrf_init() failed" in text:
        return (
            "HackRF is not available to the service. Check USB connection, power, "
            "plugdev/systemd permissions, and that no other process owns the device."
        )
    if "resource busy" in text or "busy" in text:
        return "HackRF is busy. Another process still has the device open."
    return stderr_text.strip() or "HackRF transmission failed."


def probe_hackrf_access() -> Dict[str, Any]:
    if shutil.which("hackrf_info") is None:
        return {"ok": False, "error": "hackrf_info not found in PATH"}
    try:
        proc = subprocess.run(
            ["hackrf_info"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}

    if proc.returncode == 0:
        output = (proc.stdout or proc.stderr).strip()
        return {"ok": True, "details": output}

    err = (proc.stderr or proc.stdout or "").strip()
    return {"ok": False, "error": _classify_hackrf_error(err), "raw_error": err}


def describe_tx_file(filepath: str, sample_rate: int = HACKRF_DEFAULT_SAMPLE_RATE) -> Dict[str, Any]:
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {filepath}")

    safe_rate = _sanitize_sample_rate(sample_rate)
    file_size, duration_s = _validate_tx_file(path, safe_rate)
    return {
        "path": str(path),
        "bytes": file_size,
        "duration_s": round(duration_s, 4),
        "sample_rate": safe_rate,
    }


def build_auto_tune_candidates(base_frequency: int) -> list[Dict[str, Any]]:
    freq = _sanitize_frequency(base_frequency)
    candidates: list[Dict[str, Any]] = []
    for offset_hz in AUTO_TUNE_FREQ_OFFSETS_HZ:
        for tx_vga in AUTO_TUNE_VGA_STEPS:
            safe_vga = _sanitize_tx_vga(tx_vga)
            candidates.append(
                {
                    "frequency": freq + offset_hz,
                    "frequency_offset_hz": offset_hz,
                    "tx_vga": safe_vga,
                    "label": f"{(freq + offset_hz) / 1e6:.6f} MHz @ VGA {safe_vga}",
                }
            )
    return candidates


def score_probe_observation(observed_signals: list[Dict[str, Any]], target_model: str = "") -> Dict[str, Any]:
    target_norm = _normalize_model_name(target_model)
    total_hits = len(observed_signals)
    target_hits = 0
    model_counts: Dict[str, int] = {}

    for row in observed_signals:
        model = str(row.get("model", "") or "").strip()
        if not model:
            continue
        model_counts[model] = model_counts.get(model, 0) + 1
        if target_norm and _normalize_model_name(model) == target_norm:
            target_hits += 1

    non_target_hits = max(0, total_hits - target_hits)
    score = (target_hits * 100) - (non_target_hits * 4) + min(total_hits, 10)
    if target_norm and target_hits == 0:
        score -= 10

    dominant_model = ""
    dominant_count = 0
    if model_counts:
        dominant_model, dominant_count = max(model_counts.items(), key=lambda item: item[1])

    return {
        "score": score,
        "total_hits": total_hits,
        "target_hits": target_hits,
        "non_target_hits": non_target_hits,
        "dominant_model": dominant_model,
        "dominant_count": dominant_count,
        "models_seen": model_counts,
    }


async def transmit_c8_file(
    filepath: str,
    frequency: int = 433_920_000,
    sample_rate: int = HACKRF_DEFAULT_SAMPLE_RATE,
    tx_vga: int | None = None,
) -> Dict[str, Any]:
    """Transmit a .cs8 IQ file using hackrf_transfer with conservative defaults."""
    if not is_research_mode_enabled():
        return {"success": False, "error": "TX blocked: Research Mode disabled."}

    if shutil.which("hackrf_transfer") is None:
        return {"success": False, "error": "hackrf_transfer not found in PATH"}

    path = Path(filepath)
    if not path.exists():
        return {"success": False, "error": f"File not found: {filepath}"}

    try:
        safe_freq = _sanitize_frequency(frequency)
        safe_rate = _sanitize_sample_rate(sample_rate)
        tx_vga = _tx_vga_from_env() if tx_vga is None else _sanitize_tx_vga(tx_vga)
        file_size, duration_s = _validate_tx_file(path, safe_rate)
    except Exception as e:
        return {"success": False, "error": str(e)}

    access = probe_hackrf_access()
    if not access.get("ok"):
        return {"success": False, "error": access.get("error", "HackRF unavailable"), "raw_error": access.get("raw_error", "")}

    cmd = _build_hackrf_tx_command(path, safe_freq, safe_rate, tx_vga)
    logger.info(
        "Transmitting via HackRF: file=%s freq=%s rate=%s tx_vga=%s duration=%.3fs bytes=%s",
        path,
        safe_freq,
        safe_rate,
        tx_vga,
        duration_s,
        file_size,
    )
    last_err = ""
    for attempt in range(1, HACKRF_OPEN_RETRIES + 1):
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                return {
                    "success": True,
                    "details": "HackRF transmission completed.",
                    "frequency": safe_freq,
                    "sample_rate": safe_rate,
                    "tx_vga": tx_vga,
                    "duration_s": round(duration_s, 3),
                    "amp_enabled": False,
                }

            err_msg = stderr.decode().strip() or stdout.decode().strip()
            last_err = err_msg
            if attempt < HACKRF_OPEN_RETRIES and ("hackrf_open() failed" in err_msg.lower() or "busy" in err_msg.lower()):
                await asyncio.sleep(0.35 * attempt)
                continue
            return {"success": False, "error": _classify_hackrf_error(err_msg), "raw_error": err_msg}
        except Exception as e:
            logger.error("TX error: %s", e)
            last_err = str(e)
            if attempt < HACKRF_OPEN_RETRIES:
                await asyncio.sleep(0.35 * attempt)
                continue
            return {"success": False, "error": str(e)}

    return {"success": False, "error": _classify_hackrf_error(last_err), "raw_error": last_err}


async def transmit_sub_file(filepath: str, tx_vga: int | None = None) -> Dict[str, Any]:
    """Convert a Flipper Zero .sub file to .cs8 and transmit it."""
    if not is_research_mode_enabled():
        return {"success": False, "error": "TX blocked: Research Mode disabled."}

    path = Path(filepath)
    if not path.exists():
        return {"success": False, "error": f"File not found: {filepath}"}

    try:
        c8_path = path.with_suffix(".cs8")
        with open(path, "r") as f:
            sub_data = f.read()

        freq = 433_920_000
        for line in sub_data.splitlines():
            if line.startswith("Frequency:"):
                try:
                    freq = int(line.split(":")[1].strip())
                except Exception:
                    pass

        payload_generator.sub_to_c8(str(path), str(c8_path))
        if not c8_path.exists():
            return {"success": False, "error": "Failed to convert .sub to .cs8"}

        return await transmit_c8_file(
            str(c8_path),
            frequency=freq,
            sample_rate=HACKRF_DEFAULT_SAMPLE_RATE,
            tx_vga=tx_vga,
        )
    except Exception as e:
        logger.error("Sub conversion/TX error: %s", e)
        return {"success": False, "error": str(e)}
