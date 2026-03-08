import asyncio
import logging
import os
import shutil
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


async def transmit_c8_file(
    filepath: str,
    frequency: int = 433_920_000,
    sample_rate: int = HACKRF_DEFAULT_SAMPLE_RATE,
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
        tx_vga = _tx_vga_from_env()
        file_size, duration_s = _validate_tx_file(path, safe_rate)
    except Exception as e:
        return {"success": False, "error": str(e)}

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
        return {"success": False, "error": f"HackRF TX failed: {err_msg}"}
    except Exception as e:
        logger.error("TX error: %s", e)
        return {"success": False, "error": str(e)}


async def transmit_sub_file(filepath: str) -> Dict[str, Any]:
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
        )
    except Exception as e:
        logger.error("Sub conversion/TX error: %s", e)
        return {"success": False, "error": str(e)}
