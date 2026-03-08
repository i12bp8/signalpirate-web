"""
rtl_433 Engine — Manages rtl_433 subprocess with UDP syslog for instant signal delivery.

Uses `-F syslog:127.0.0.1:<port>` instead of stdout pipes to bypass Linux block-buffering.
"""
import asyncio
import asyncio.subprocess
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Optional, Any, Callable, Dict, List
from asyncio.subprocess import Process

logger = logging.getLogger("signalpirate.rtl433_engine")

SYSLOG_RE = re.compile(r"<\d+>\d+\s+\S+\s+\S+\s+\S+\s+-\s+-\s+-\s+(.*)")
UDP_PORT = 14433
IQ_CAPTURE_DIR = Path.home() / ".config" / "signalpirate" / "iq_captures"


class Signal:
    """Represents a decoded RF signal."""

    def __init__(self, raw_data: dict, protocol_info: Optional[dict] = None, vuln_info: Optional[dict] = None):
        self.timestamp: float = time.time()
        self.raw: dict = raw_data
        self.model: str = raw_data.get("model", "Unknown")
        self.protocol: Optional[int] = raw_data.get("protocol", None)
        # Normalize frequency: rtl_433 sends freq as MHz float (433.92085)
        raw_freq = raw_data.get("freq", 433.92)
        if isinstance(raw_freq, (int, float)) and raw_freq < 10000:
            self.frequency: float = raw_freq * 1_000_000
        else:
            self.frequency = float(raw_freq)
        self.rssi: Optional[float] = raw_data.get("rssi", raw_data.get("snr", None))
        self.modulation: str = raw_data.get("mod", "OOK") or "OOK"
        self.data: dict = raw_data
        self.iq_file: Optional[str] = raw_data.get("iq_file")
        self.protocol_info: Optional[dict] = protocol_info
        self.vuln_info: Optional[dict] = vuln_info

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "model": self.model,
            "protocol_id": self.protocol,
            "frequency": self.frequency,
            "rssi": self.rssi,
            "modulation": self.modulation,
            "iq_file": self.iq_file,
            "data": self.data,
            "protocol_info": self.protocol_info,
            "vuln_info": self.vuln_info,
        }


class _SyslogProtocol(asyncio.DatagramProtocol):
    """Receives UDP syslog packets from rtl_433 and forwards JSON payloads."""

    def __init__(self, engine: "RTL433Engine"):
        self.engine = engine
        super().__init__()

    def datagram_received(self, data: bytes, addr: tuple) -> None:  # type: ignore[override]
        try:
            msg = data.decode("utf-8", errors="replace")
            match = SYSLOG_RE.match(msg)
            if match:
                payload = match.group(1).strip()
            else:
                payload = msg.strip()

            if not payload or not payload.startswith("{"):
                return

            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                return

            # Skip rtl_433 internal status messages
            if "src" in parsed and "model" not in parsed:
                return

            self.engine._handle_signal(parsed)

        except Exception as e:
            logger.error(f"Syslog parse error: {e}")


class RTL433Engine:
    """Manages the rtl_433 background process and receives decoded signals via UDP."""

    def __init__(self, protocol_db: Any = None, vuln_db: Any = None):
        self.protocol_db = protocol_db
        self.vuln_db = vuln_db
        self.process: Optional[Process] = None
        self._udp_transport: Optional[asyncio.DatagramTransport] = None
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._running: bool = False
        self.signals: deque[Signal] = deque(maxlen=1000)
        self._last_sig_key: str = ""
        self._last_sig_time: float = 0.0
        self.frequency: int = 433_920_000
        self.device_type: str = "rtlsdr"
        self.hackrf_gain: int = 80
        self.iq_capture_dir: Path = IQ_CAPTURE_DIR
        self.iq_capture_dir.mkdir(parents=True, exist_ok=True)
        self._seen_iq_files: set[str] = set()
        self._iq_match_lock = asyncio.Lock()
        self.stats: Dict[str, Any] = {
            "signals_decoded": 0,
            "start_time": None,
            "errors": 0,
            "protocols_seen": set(),
        }

    def add_listener(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    async def start(self, frequency: int = 433_920_000, device_type: str = "rtlsdr", rtl_autolevel: bool = True, rtl_squelch: bool = True, rtl_gain: int = 38, hackrf_gain: int = 80) -> bool:
        """Start rtl_433 subprocess with UDP syslog output."""
        if self._running:
            return False

        if not shutil.which("rtl_433"):
            logger.error("rtl_433 not found in PATH")
            return False

        if frequency:
            self.frequency = frequency
        self.device_type = device_type
        self.hackrf_gain = hackrf_gain

        # Bind UDP listener
        try:
            loop = asyncio.get_running_loop()
            transport, _ = await loop.create_datagram_endpoint(
                lambda: _SyslogProtocol(self),
                local_addr=("127.0.0.1", UDP_PORT),
            )
            sock = transport.get_extra_info("socket")
            if sock is not None:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._udp_transport = transport
        except OSError as e:
            logger.error(f"Failed to bind UDP port {UDP_PORT}: {e}")
            return False

        if device_type == "hackrf":
            return await self._start_hackrf(hackrf_gain)
        else:
            return await self._start_rtlsdr(rtl_autolevel, rtl_squelch, rtl_gain)

    async def _start_rtlsdr(self, rtl_autolevel: bool, rtl_squelch: bool, rtl_gain: int) -> bool:
        # Wider capture profile: 2M native matches HackRF TX constraints
        sample_rate = 2_000_000
        cmd = [
            "rtl_433",
            "-f", str(self.frequency),
            "-s", str(sample_rate),
        ]
        if rtl_autolevel:
            cmd.extend(["-Y", "autolevel"])
        elif rtl_gain > 0:
            cmd.extend(["-g", str(rtl_gain)])
            
        if rtl_squelch:
            cmd.extend(["-Y", "squelch"])
            
        # Use magnitude estimation for cleaner ASK/OOK slicing
        cmd.extend(["-Y", "magest"])
            
        cmd.extend([
            "-S", "known",
            "-F", f"syslog:127.0.0.1:{UDP_PORT}",
            "-M", "level", "-M", "protocol", "-M", "time:unix",
            "-d", "0",
        ])
        logger.info(f"Starting rtl_433: {' '.join(cmd)}")
        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                cwd=str(self.iq_capture_dir),
            )
            self._running = True
            self.stats["start_time"] = time.time()
            asyncio.create_task(self._read_stderr())
            return True
        except Exception as e:
            logger.error(f"Failed to start rtl_433: {e}")
            self._cleanup_udp()
            return False

    async def _start_hackrf(self, hackrf_gain: int) -> bool:
        # Stability-first HackRF profile for 433 MHz RX.
        gain_str = self._hackrf_gain_string(hackrf_gain)
        sample_rate = 2_000_000
        cmd = [
            "rtl_433",
            "-d", "driver=hackrf",
            "-f", str(self.frequency),
            "-s", str(sample_rate),
            "-g", gain_str,
            "-Y", "autolevel",
            "-Y", "magest",
            "-S", "known",
            "-F", f"syslog:127.0.0.1:{UDP_PORT}",
            "-M", "level", "-M", "protocol", "-M", "time:unix",
        ]
        logger.info(f"Starting rtl_433 (HackRF): {' '.join(cmd)}")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                cwd=str(self.iq_capture_dir),
            )
        except Exception as e:
            logger.error(f"Failed to spawn rtl_433 for HackRF: {e}")
            self.stats["errors"] += 1
            self._cleanup_udp()
            return False

        await asyncio.sleep(1.0)
        if proc.returncode is None:
            self.process = proc
            self._running = True
            self.stats["start_time"] = time.time()
            asyncio.create_task(self._read_stderr())
            logger.info(
                "HackRF profile active: rate=%s gain=%s",
                sample_rate,
                gain_str,
            )
            return True

        err_msg = ""
        if proc.stderr is not None:
            try:
                stderr_data = await proc.stderr.read()
                err_msg = stderr_data.decode("utf-8", errors="replace").strip()
            except Exception:
                pass
        if err_msg:
            logger.error("HackRF profile failed: %s", err_msg.splitlines()[-1])
        else:
            logger.error("HackRF profile failed (rtl_433 exited)")
        self.stats["errors"] += 1
        self._cleanup_udp()
        return False

    @staticmethod
    def _hackrf_gain_string(hackrf_gain: int) -> str:
        # Map UI scalar (0-100) to conservative 433 MHz gain values.
        # HackRF valid ranges are LNA 0-40 step 8, VGA 0-62 step 2, AMP 0/1.
        scalar = max(0, min(100, int(hackrf_gain)))
        lna_target = 24.0 + (scalar / 100.0) * 16.0
        vga_target = 16.0 + (scalar / 100.0) * 20.0
        lna = int((lna_target + 4.0) // 8.0) * 8
        vga = int((vga_target + 1.0) // 2.0) * 2
        lna = max(0, min(40, lna))
        vga = max(0, min(62, vga))
        amp = 0
        return f"LNA={lna},VGA={vga},AMP={amp}"

    async def stop(self) -> None:
        self._running = False
        self._cleanup_udp()
        for proc_attr in ("process",):
            proc: Optional[Process] = getattr(self, proc_attr, None)
            if proc is not None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except Exception:
                    try:
                        proc.kill()
                        await proc.wait()
                    except Exception:
                        pass
                setattr(self, proc_attr, None)

    def _cleanup_udp(self) -> None:
        transport = self._udp_transport
        if transport is not None:
            transport.close()
            self._udp_transport = None

    def _handle_signal(self, data: dict) -> None:
        """Process a decoded signal — enrich, dedup, and notify listeners."""
        if "time" not in data:
            data["time"] = time.strftime("%Y-%m-%d %H:%M:%S")

        # Dedup: skip if same model+id within 500ms
        now = time.time()
        sig_key = f"{data.get('model', '')}:{data.get('id', '')}:{data.get('channel', '')}"
        if sig_key == self._last_sig_key and (now - self._last_sig_time) < 0.5:
            return
        self._last_sig_key = sig_key
        self._last_sig_time = now

        # rtl_433 may write autosaved IQ shortly after emitting JSON decode.
        # Defer finalization briefly so we can attach the correct iq_file.
        asyncio.create_task(self._finalize_signal_with_iq(data))

    async def _finalize_signal_with_iq(self, data: dict) -> None:
        # Give rtl_433 autosave enough time to flush the IQ file before binding.
        await asyncio.sleep(0.9)
        iq_file = await self._claim_iq_for_signal(data)
        if iq_file:
            data["iq_file"] = iq_file

        sig = Signal(data)

        if self.protocol_db:
            sig.protocol_info = self.protocol_db.match_signal(data)
        if self.vuln_db:
            sig.vuln_info = self.vuln_db.match_signal(data)

        self.signals.appendleft(sig)
        self.stats["signals_decoded"] += 1
        if sig.model != "Unknown":
            self.stats["protocols_seen"].add(sig.model)

        sig_dict = sig.to_dict()
        for listener in self._listeners:
            try:
                result = listener(sig_dict)
                if asyncio.iscoroutine(result):
                    asyncio.ensure_future(result)
            except Exception as e:
                logger.error(f"Listener error: {e}")

    async def _claim_iq_for_signal(self, data: dict) -> Optional[str]:
        """
        Claim the best unassigned autosaved IQ file for this decoded signal.
        """
        async with self._iq_match_lock:
            signal_ts = self._parse_signal_unix_time(data)
            signal_freq = self._parse_signal_freq_hz(data)
            best_path: Optional[str] = None
            best_score = float("inf")
            now = time.time()

            try:
                for ent in self.iq_capture_dir.iterdir():
                    if not ent.is_file():
                        continue
                    path = str(ent)
                    if path in self._seen_iq_files:
                        continue
                    st = ent.stat()
                    if st.st_size < 4096:
                        continue
                    # Ignore stale files.
                    if (now - st.st_mtime) > 120.0:
                        continue

                    dt = float(st.st_mtime - signal_ts)
                    # Most autosaves land just after decode; allow wider lag under load.
                    if dt < -2.0 or dt > 20.0:
                        continue

                    # Prefer files closest in time to this signal.
                    time_score = abs(dt)
                    if 0.0 <= dt <= 3.0:
                        time_score *= 0.5

                    # Prefer files centered near this signal's frequency.
                    freq_score = 0.0
                    iq_center = self._parse_iq_center_freq_hz(path)
                    if iq_center is not None:
                        delta_hz = abs(iq_center - signal_freq)
                        if delta_hz > 800_000:
                            continue
                        freq_score = delta_hz / 50_000.0
                    else:
                        freq_score = 5.0

                    score = time_score + freq_score
                    if score < best_score:
                        best_score = score
                        best_path = path
            except Exception:
                return None

            if best_path is None:
                return None

            self._seen_iq_files.add(best_path)
            return best_path

    @staticmethod
    def _parse_signal_unix_time(data: dict) -> float:
        raw = data.get("time")
        try:
            return float(raw)
        except Exception:
            return time.time()

    @staticmethod
    def _parse_signal_freq_hz(data: dict) -> int:
        raw = data.get("freq", 433.92)
        try:
            val = float(raw)
            if val < 10_000:
                return int(val * 1_000_000)
            return int(val)
        except Exception:
            return 433_920_000

    @staticmethod
    def _parse_iq_center_freq_hz(path: str) -> Optional[int]:
        name = Path(path).name
        m = re.search(r"_(\d+(?:\.\d+)?)M_", name)
        if not m:
            return None
        try:
            return int(float(m.group(1)) * 1_000_000)
        except Exception:
            return None

    def _find_recent_iq_capture(self) -> Optional[str]:
        """Find newest unassigned rtl_433 autosaved IQ file."""
        now = time.time()
        newest_path: Optional[Path] = None
        newest_mtime = 0.0
        try:
            for ent in self.iq_capture_dir.iterdir():
                if not ent.is_file():
                    continue
                path = str(ent)
                if path in self._seen_iq_files:
                    continue
                # Ignore stale files and very small artifacts.
                # autosave snippets can complete a few seconds after decode.
                st = ent.stat()
                if (now - st.st_mtime) > 12.0:
                    continue
                if st.st_size < 4096:
                    continue
                if st.st_mtime > newest_mtime:
                    newest_mtime = st.st_mtime
                    newest_path = ent
        except Exception:
            return None

        if newest_path is None:
            return None

        chosen = str(newest_path)
        self._seen_iq_files.add(chosen)
        return chosen

    async def _read_stderr(self) -> None:
        proc = self.process
        if proc is None or proc.stderr is None:
            return
        try:
            while self._running:
                line = await proc.stderr.readline()
                if not line:
                    break
                msg = line.decode("utf-8", errors="replace").strip()
                if msg:
                    if "error" in msg.lower() or "fail" in msg.lower():
                        logger.warning(f"rtl_433: {msg}")
                    else:
                        logger.debug(f"rtl_433: {msg}")
            if self._running and proc.returncode is not None:
                exit_code = proc.returncode
                logger.error(f"rtl_433 exited with code {exit_code}")
                self.stats["errors"] += 1
                self._running = False
                self.process = None
                self._cleanup_udp()
        except (asyncio.CancelledError, Exception):
            pass

    async def set_frequency(self, frequency: int) -> bool:
        """Change frequency by restarting the engine."""
        was_running = self._running
        if was_running:
            await self.stop()
        self.frequency = frequency
        if was_running:
            return await self.start(frequency=frequency, device_type=self.device_type, hackrf_gain=self.hackrf_gain)
        return True

    def get_recent_signals(self, count: int = 50) -> List[Dict[str, Any]]:
        return [sig.to_dict() for sig in list(self.signals)[:count]]

    def get_stats(self) -> Dict[str, Any]:
        uptime = 0.0
        start = self.stats["start_time"]
        if start is not None:
            uptime = time.time() - float(start)
        return {
            "running": self._running,
            "signals_decoded": self.stats["signals_decoded"],
            "uptime_seconds": uptime,
            "unique_protocols": len(self.stats["protocols_seen"]),
            "protocols_seen": list(self.stats["protocols_seen"]),
            "frequency": self.frequency,
            "device_type": self.device_type,
            "signal_buffer_size": len(self.signals),
            "errors": self.stats["errors"],
        }
