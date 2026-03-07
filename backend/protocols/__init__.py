"""
Protocol Decoders Package — KAT-style vehicle protocol analysis.

Provides KeeLoq/AUT64 crypto, manufacturer keystore, and 14+ protocol decoders
for automotive keyfob signals (Kia, Ford, Fiat, Subaru, Suzuki, VAG, PSA, etc.).
"""
from backend.protocols.keeloq import KeeLoq, keeloq_encrypt, keeloq_decrypt
from backend.protocols.keystore import Keystore, EMBEDDED_KEYS
from backend.protocols.decoders import decode_signal, PROTOCOL_REGISTRY, DecodedSignal

__all__ = [
    "KeeLoq", "keeloq_encrypt", "keeloq_decrypt",
    "Keystore", "EMBEDDED_KEYS",
    "decode_signal", "PROTOCOL_REGISTRY", "DecodedSignal",
]
