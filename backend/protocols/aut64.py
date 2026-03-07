"""
AUT64 Block Cipher — 12-round cipher used by VAG (VW/Audi/Seat/Skoda).

Ported from KAT's aut64.rs reference implementation.
Used for VAG type 1/3/4 keyfob decoding.
"""

# AUT64 S-boxes (4-bit)
SBOX = [
    [0x3, 0x5, 0xF, 0x8, 0x2, 0x9, 0x6, 0xC, 0x1, 0xA, 0x4, 0xD, 0x0, 0xE, 0x7, 0xB],
    [0xE, 0x2, 0x4, 0xF, 0xD, 0x8, 0xB, 0x3, 0x6, 0x9, 0x0, 0x1, 0xA, 0xC, 0x5, 0x7],
    [0x1, 0xD, 0xA, 0x0, 0x8, 0x6, 0x3, 0xE, 0xB, 0x4, 0x7, 0x2, 0xF, 0x5, 0x9, 0xC],
    [0x7, 0xC, 0x5, 0xB, 0x4, 0x0, 0xF, 0xA, 0x2, 0x6, 0x9, 0xE, 0xD, 0x1, 0x3, 0x8],
]


def _rotate_left(val: int, shift: int, bits: int = 32) -> int:
    """Rotate val left by shift bits within a bits-wide field."""
    shift %= bits
    return ((val << shift) | (val >> (bits - shift))) & ((1 << bits) - 1)


def aut64_encrypt(data: int, key: int) -> int:
    """AUT64 12-round encryption. 32-bit data, 64-bit key."""
    left = (data >> 16) & 0xFFFF
    right = data & 0xFFFF

    for r in range(12):
        # Extract round key (8 bits per round, cycling key)
        rk = (key >> ((r * 8) % 64)) & 0xFF

        # Apply S-boxes to right half mixed with round key
        mixed = (right ^ rk) & 0xFFFF
        s_out = 0
        for i in range(4):
            nibble = (mixed >> (i * 4)) & 0xF
            s_out |= SBOX[i][nibble] << (i * 4)

        new_right = left ^ s_out
        left = right
        right = new_right

    return ((left & 0xFFFF) << 16) | (right & 0xFFFF)


def aut64_decrypt(data: int, key: int) -> int:
    """AUT64 12-round decryption. 32-bit data, 64-bit key."""
    left = (data >> 16) & 0xFFFF
    right = data & 0xFFFF

    for r in range(11, -1, -1):
        rk = (key >> ((r * 8) % 64)) & 0xFF

        mixed = (left ^ rk) & 0xFFFF
        s_out = 0
        for i in range(4):
            nibble = (mixed >> (i * 4)) & 0xF
            s_out |= SBOX[i][nibble] << (i * 4)

        new_left = right ^ s_out
        right = left
        left = new_left

    return ((left & 0xFFFF) << 16) | (right & 0xFFFF)
