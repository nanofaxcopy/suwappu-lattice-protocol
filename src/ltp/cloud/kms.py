"""
KMS (Key Management Service) abstraction for ETP.

Production: AWS KMS, Azure Key Vault, GCP Cloud KMS.
Development/Test: InMemoryKMSBackend.

Manages cloud key lifecycle: create, sign, rotate, destroy, list.
Distinct from HSMBackend (src/ltp/hsm.py) which models hardware-isolated
cryptographic operations. KMS models cloud-managed lifecycle and policy.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from typing import Optional

from ..primitives import MLDSA, MLKEM

__all__ = ["KMSBackend", "InMemoryKMSBackend"]


class KMSBackend(ABC):
    """Abstract Key Management Service interface."""

    @abstractmethod
    def create_key(self, key_id: str, algorithm: str = "ML-DSA-65") -> bytes:
        """Create a new key. Returns public key material."""
        ...

    @abstractmethod
    def get_public_key(self, key_id: str) -> bytes:
        """Retrieve the public key material for a key."""
        ...

    @abstractmethod
    def sign(self, key_id: str, message: bytes) -> bytes:
        """Sign a message using the key identified by key_id."""
        ...

    @abstractmethod
    def destroy_key(self, key_id: str) -> bool:
        """Schedule key destruction. Returns True if key existed."""
        ...

    @abstractmethod
    def get_key_metadata(self, key_id: str) -> dict:
        """Return metadata: creation time, state, algorithm, etc."""
        ...

    @abstractmethod
    def list_keys(self, prefix: str = "") -> list[str]:
        """List key IDs, optionally filtered by prefix."""
        ...

    @abstractmethod
    def rotate_key(self, key_id: str) -> str:
        """Create a new version of the key. Returns new version ID."""
        ...


class InMemoryKMSBackend(KMSBackend):
    """In-memory KMS implementation for testing and development.

    Thread-safe via threading.Lock. Uses MLDSA for signing keys
    and MLKEM for encryption keys internally.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keys: dict[str, dict] = {}

    def create_key(self, key_id: str, algorithm: str = "ML-DSA-65") -> bytes:
        with self._lock:
            if key_id in self._keys:
                raise ValueError(f"Key {key_id!r} already exists")

            if algorithm == "ML-DSA-65":
                vk, sk = MLDSA.keygen()
                public_key = vk
            elif algorithm == "ML-KEM-768":
                ek, dk = MLKEM.keygen()
                public_key = ek
            else:
                raise ValueError(f"Unsupported algorithm: {algorithm}")

            self._keys[key_id] = {
                "public": public_key,
                "private": sk if algorithm == "ML-DSA-65" else dk,
                "algorithm": algorithm,
                "created_at": time.time(),
                "state": "active",
                "versions": [1],
            }
            return public_key

    def get_public_key(self, key_id: str) -> bytes:
        with self._lock:
            entry = self._keys.get(key_id)
            if entry is None:
                raise KeyError(f"Key {key_id!r} not found")
            return entry["public"]

    def sign(self, key_id: str, message: bytes) -> bytes:
        with self._lock:
            entry = self._keys.get(key_id)
            if entry is None:
                raise KeyError(f"Key {key_id!r} not found")
            if entry["algorithm"] != "ML-DSA-65":
                raise ValueError(f"Key {key_id!r} is not a signing key")
            return MLDSA.sign(entry["private"], message)

    def destroy_key(self, key_id: str) -> bool:
        with self._lock:
            if key_id in self._keys:
                del self._keys[key_id]
                return True
            return False

    def get_key_metadata(self, key_id: str) -> dict:
        with self._lock:
            entry = self._keys.get(key_id)
            if entry is None:
                raise KeyError(f"Key {key_id!r} not found")
            return {
                "key_id": key_id,
                "algorithm": entry["algorithm"],
                "created_at": entry["created_at"],
                "state": entry["state"],
                "versions": list(entry["versions"]),
            }

    def list_keys(self, prefix: str = "") -> list[str]:
        with self._lock:
            if not prefix:
                return list(self._keys.keys())
            return [k for k in self._keys if k.startswith(prefix)]

    def rotate_key(self, key_id: str) -> str:
        with self._lock:
            entry = self._keys.get(key_id)
            if entry is None:
                raise KeyError(f"Key {key_id!r} not found")

            new_version = max(entry["versions"]) + 1
            entry["versions"].append(new_version)

            # Re-generate key material for new version
            if entry["algorithm"] == "ML-DSA-65":
                vk, sk = MLDSA.keygen()
                entry["public"] = vk
                entry["private"] = sk
            elif entry["algorithm"] == "ML-KEM-768":
                ek, dk = MLKEM.keygen()
                entry["public"] = ek
                entry["private"] = dk

            return f"{key_id}-v{new_version}"
