import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any

from backend import payload_generator

logger = logging.getLogger("signalpirate.tx_engine")

_research_mode_enabled = False

def enable_research_mode() -> None:
    global _research_mode_enabled
    _research_mode_enabled = True
    logger.warning("TX RESEARCH MODE ENABLED - Transmissions unlocked.")

def is_research_mode_enabled() -> bool:
    return _research_mode_enabled

async def transmit_c8_file(filepath: str, frequency: int = 433920000, sample_rate: int = 2000000) -> Dict[str, Any]:
    """Transmit a .c8 IQ file using hackrf_transfer."""
    if not is_research_mode_enabled():
         return {"success": False, "error": "TX blocked: Research Mode disabled."}
         
    path = Path(filepath)
    if not path.exists():
        return {"success": False, "error": f"File not found: {filepath}"}

    # hackrf_transfer settings for TX:
    # -a 0 (AMP off to reduce noise)
    # -x 47 (TX VGA gain high)
    cmd = [
        "hackrf_transfer",
        "-t", str(path),
        "-f", str(frequency),
        "-s", str(sample_rate),
        "-a", "0",
        "-x", "47",
    ]
    
    logger.info(f"Transmitting via HackRF: {' '.join(cmd)}")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode == 0:
             return {"success": True, "details": "HackRF transmission completed."}
        else:
             err_msg = stderr.decode().strip() or stdout.decode().strip()
             return {"success": False, "error": f"HackRF TX failed: {err_msg}"}
    except Exception as e:
        logger.error(f"TX error: {e}")
        return {"success": False, "error": str(e)}

async def transmit_sub_file(filepath: str) -> Dict[str, Any]:
    """Convert a Flipper Zero .sub file to .c8 and transmit it."""
    if not is_research_mode_enabled():
         return {"success": False, "error": "TX blocked: Research Mode disabled."}
         
    path = Path(filepath)
    if not path.exists():
        return {"success": False, "error": f"File not found: {filepath}"}

    try:
        # Convert .sub to .c8 payload
        c8_path = path.with_suffix(".c8")
        # We need to simulate the conversion process or use payload_generator
        # For this version, let's call payload_generator if it has a sub to c8 method
        with open(path, "r") as f:
            sub_data = f.read()
            
        freq = 433920000
        for line in sub_data.splitlines():
            if line.startswith("Frequency:"):
                try:
                    freq = int(line.split(":")[1].strip())
                except:
                    pass
                    
        # Just use the generator to make the c8 file
        payload_generator.sub_to_c8(str(path), str(c8_path))
        
        if not c8_path.exists():
             return {"success": False, "error": "Failed to convert .sub to .c8"}
             
        # Transmit the converted c8 file
        return await transmit_c8_file(str(c8_path), frequency=freq)
        
    except Exception as e:
         logger.error(f"Sub conversion/TX error: {e}")
         return {"success": False, "error": str(e)}
