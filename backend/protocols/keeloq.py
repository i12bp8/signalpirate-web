"""
KeeLoq Cipher — Encrypt/decrypt with normal, secure, FAAC, and magic key derivation.

Ported from KAT (Rust) and ProtoPirate (C) reference implementations.
NLF constant: 0x3A5C742E
"""
import struct
from typing import Optional

# KeeLoq Non-Linear Function lookup table
NLF = 0x3A5C742E


def keeloq_encrypt(data: int, key: int) -> int:
    """Standard KeeLoq 528-round encryption. 32-bit data, 64-bit key."""
    x = data & 0xFFFFFFFF
    k = key & 0xFFFFFFFFFFFFFFFF

    for i in range(528):
        # NLF inputs: bits 31, 26, 20, 9, 1
        nlf_idx = (
            ((x >> 1) & 1)
            | (((x >> 9) & 1) << 1)
            | (((x >> 20) & 1) << 2)
            | (((x >> 26) & 1) << 3)
            | (((x >> 31) & 1) << 4)
        )
        nlf_bit = (NLF >> nlf_idx) & 1

        # Feedback = NLF XOR bit0 XOR bit16 XOR key_bit
        fb = nlf_bit ^ (x & 1) ^ ((x >> 16) & 1) ^ (k & 1)

        # Shift right, insert feedback at bit 31
        x = ((x >> 1) | (fb << 31)) & 0xFFFFFFFF

        # Rotate key right
        k = ((k >> 1) | ((k & 1) << 63)) & 0xFFFFFFFFFFFFFFFF

    return x


def keeloq_decrypt(data: int, key: int) -> int:
    """Standard KeeLoq 528-round decryption. 32-bit data, 64-bit key."""
    x = data & 0xFFFFFFFF
    k = key & 0xFFFFFFFFFFFFFFFF

    # Pre-rotate key to decryption position
    for _ in range(528):
        k = (((k << 1) & 0xFFFFFFFFFFFFFFFF) | (k >> 63))

    for i in range(528):
        # Reverse key rotation
        k = ((k >> 1) | ((k & 1) << 63)) & 0xFFFFFFFFFFFFFFFF

        # NLF inputs: bits 30, 25, 19, 8, 0
        nlf_idx = (
            (x & 1)
            | (((x >> 8) & 1) << 1)
            | (((x >> 19) & 1) << 2)
            | (((x >> 25) & 1) << 3)
            | (((x >> 30) & 1) << 4)
        )
        nlf_bit = (NLF >> nlf_idx) & 1

        # Feedback = NLF XOR bit15 XOR bit31 XOR key_bit
        fb = nlf_bit ^ ((x >> 15) & 1) ^ ((x >> 31) & 1) ^ (k & 1)

        # Shift left, insert feedback at bit 0
        x = (((x << 1) & 0xFFFFFFFF) | fb)

    return x


class KeeLoq:
    """High-level KeeLoq operations for automotive keyfob analysis."""

    @staticmethod
    def derive_normal_key(serial: int, manufacturer_key: int) -> int:
        """Normal learning: derive device key from serial + manufacturer key."""
        # Encrypt serial with manufacturer key, result is device key lower 32 bits
        low = keeloq_encrypt(serial & 0xFFFFFFFF, manufacturer_key)
        high = keeloq_encrypt((serial + 0x60000000) & 0xFFFFFFFF, manufacturer_key)
        return (high << 32) | low

    @staticmethod
    def derive_secure_key(serial: int, seed: int, manufacturer_key: int) -> int:
        """Secure learning: uses seed from device pairing."""
        low = keeloq_encrypt(seed & 0xFFFFFFFF, manufacturer_key)
        high = keeloq_encrypt((seed ^ serial) & 0xFFFFFFFF, manufacturer_key)
        return (high << 32) | low

    @staticmethod
    def derive_faac_key(serial: int, seed: int, manufacturer_key: int) -> int:
        """FAAC SLH learning key derivation."""
        man_lo = manufacturer_key & 0xFFFFFFFF
        man_hi = (manufacturer_key >> 32) & 0xFFFFFFFF
        s = seed & 0x0000FFFF
        x = s | ((serial & 0xFF) << 16) | (man_lo & 0xFF000000)
        low = keeloq_encrypt(x, manufacturer_key)
        x = s | (((serial >> 8) & 0xFF) << 16) | (man_hi & 0xFF000000)
        high = keeloq_encrypt(x, manufacturer_key)
        return (high << 32) | low

    @staticmethod
    def derive_magic_xor_key(serial: int, manufacturer_key: int) -> int:
        """Magic serial XOR learning key derivation (used by some alarm systems)."""
        magic = serial ^ (manufacturer_key & 0xFFFFFFFF)
        low = keeloq_encrypt(magic, manufacturer_key)
        high = keeloq_encrypt((magic + 1) & 0xFFFFFFFF, manufacturer_key)
        return (high << 32) | low

    @staticmethod
    def decode_hopping(encrypted_32: int, device_key: int) -> dict:
        """Decrypt a KeeLoq hopping code and extract fields."""
        plain = keeloq_decrypt(encrypted_32, device_key)
        return {
            "button": (plain >> 28) & 0xF,
            "discrimination": (plain >> 16) & 0x3FF,
            "counter": plain & 0xFFFF,
            "overflow": (plain >> 26) & 0x3,
        }

    @staticmethod
    def encode_hopping(counter: int, serial: int, button: int, device_key: int) -> int:
        """Encode a KeeLoq hopping code for transmission."""
        disc = serial & 0x3FF
        plain = ((button & 0xF) << 28) | ((disc & 0x3FF) << 16) | (counter & 0xFFFF)
        return keeloq_encrypt(plain, device_key)

    @staticmethod
    def generate_transmission(counter: int, serial: int, button: int, device_key: int) -> str:
        """Generate a full 66-bit KeeLoq transmission as binary string."""
        # 32-bit encrypted hopping code
        encrypted = KeeLoq.encode_hopping(counter, serial, button, device_key)
        enc_bin = format(encrypted, "032b")

        # 34-bit fixed portion: serial (28 bits) + button (4 bits) + vlow + rpt
        fixed = ((serial & 0x0FFFFFFF) << 6) | ((button & 0xF) << 2)
        fixed_bin = format(fixed, "034b")

        return enc_bin + fixed_bin
