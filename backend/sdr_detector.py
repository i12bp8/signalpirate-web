"""
SDR Device Detector — Auto-detects RTL-SDR and HackRF hardware.
"""
import asyncio
import subprocess
import logging
import shutil

logger = logging.getLogger("signalpirate.sdr_detector")


class SDRDevice:
    """Represents a detected SDR device."""
    def __init__(self, device_type, name, serial=None, capabilities=None):
        self.device_type = device_type  # "rtlsdr" or "hackrf"
        self.name = name
        self.serial = serial
        self.capabilities = capabilities or []

    def to_dict(self):
        return {
            "type": self.device_type,
            "name": self.name,
            "serial": self.serial,
            "capabilities": self.capabilities,
            "can_tx": "tx" in self.capabilities,
            "can_rx": "rx" in self.capabilities,
        }


class SDRDetector:
    """Detects and manages SDR hardware."""

    def __init__(self):
        self.devices = []
        self.primary_device = None
        self._running = False

    async def detect_devices(self):
        """Scan for connected SDR devices."""
        self.devices = []

        # Check for HackRF first (has TX capability)
        hackrf = await self._detect_hackrf()
        if hackrf:
            self.devices.append(hackrf)

        # Check for RTL-SDR
        rtlsdr = await self._detect_rtlsdr()
        if rtlsdr:
            self.devices.append(rtlsdr)

        # Set primary RX device.
        # Prefer RTL-SDR for receive stability/sensitivity and keep HackRF for TX.
        if self.devices:
            self.primary_device = next(
                (d for d in self.devices if d.device_type == "rtlsdr"),
                self.devices[0],
            )
            logger.info(f"Primary SDR device: {self.primary_device.name}")
        else:
            self.primary_device = None
            logger.warning("No SDR devices detected")

        return self.devices

    async def _detect_hackrf(self):
        """Detect HackRF One via hackrf_info."""
        if not shutil.which("hackrf_info"):
            logger.debug("hackrf_info not found in PATH")
            return None

        try:
            proc = await asyncio.create_subprocess_exec(
                "hackrf_info",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
            output = stdout.decode("utf-8", errors="replace")

            if proc.returncode == 0 and "Serial number" in output:
                serial = None
                for line in output.splitlines():
                    if "Serial number" in line:
                        serial = line.split(":")[-1].strip()
                        break

                logger.info(f"HackRF One detected (serial: {serial})")
                return SDRDevice(
                    device_type="hackrf",
                    name="HackRF One",
                    serial=serial,
                    capabilities=["rx", "tx"],
                )
        except asyncio.TimeoutError:
            logger.debug("hackrf_info timed out")
        except Exception as e:
            logger.debug(f"HackRF detection error: {e}")

        return None

    async def _detect_rtlsdr(self):
        """Detect RTL-SDR dongle via rtl_test."""
        if not shutil.which("rtl_test"):
            logger.debug("rtl_test not found in PATH")
            return None

        try:
            proc = await asyncio.create_subprocess_exec(
                "rtl_test", "-t",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # rtl_test runs indefinitely, give it a brief window
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=3)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                # If it was running, the device exists
                logger.info("RTL-SDR detected (rtl_test was running)")
                return SDRDevice(
                    device_type="rtlsdr",
                    name="RTL-SDR",
                    serial=None,
                    capabilities=["rx"],
                )

            output = (stdout.decode("utf-8", errors="replace") +
                      stderr.decode("utf-8", errors="replace"))

            if "Found" in output and "device" in output.lower():
                logger.info("RTL-SDR detected")
                return SDRDevice(
                    device_type="rtlsdr",
                    name="RTL-SDR",
                    serial=None,
                    capabilities=["rx"],
                )
        except Exception as e:
            logger.debug(f"RTL-SDR detection error: {e}")

        return None

    async def check_rtl433(self):
        """Check if rtl_433 is available."""
        return shutil.which("rtl_433") is not None

    def get_status(self):
        """Return current device status as dict."""
        return {
            "devices": [d.to_dict() for d in self.devices],
            "primary": self.primary_device.to_dict() if self.primary_device else None,
            "has_rx": any("rx" in d.capabilities for d in self.devices),
            "has_tx": any("tx" in d.capabilities for d in self.devices),
            "rtl_433_available": shutil.which("rtl_433") is not None,
            "device_count": len(self.devices),
        }
