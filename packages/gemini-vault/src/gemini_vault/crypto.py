"""Authenticated encryption for the vault (scrypt -> AES-256-GCM).

File layout::

    MAGIC(4) | VERSION(1) | SALT(16) | NONCE(12) | CIPHERTEXT+TAG

The header (magic, version, salt) is authenticated as associated data. The salt is part
of the file, so a vault copied to another machine opens with just the passphrase.
"""

from __future__ import annotations

import os
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .errors import InvalidKeyError, VaultCorruptedError

_MAGIC = b"GWMV"
_VERSION = 1
_SALT_LEN = 16
_NONCE_LEN = 12
_HEADER_LEN = len(_MAGIC) + 1 + _SALT_LEN

_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1


def generate_key() -> str:
    """Return a fresh random passphrase suitable for ``GEMINI_MANAGER_KEY``."""
    return secrets.token_urlsafe(32)


class VaultCipher:
    """Encrypts/decrypts vault blobs with a key derived from a passphrase."""

    def __init__(self, passphrase: str) -> None:
        self._passphrase = passphrase.encode("utf-8")
        self._derived: dict[bytes, bytes] = {}
        self._salt: bytes | None = None

    def _key_for(self, salt: bytes) -> bytes:
        key = self._derived.get(salt)
        if key is None:
            kdf = Scrypt(salt=salt, length=32, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
            key = kdf.derive(self._passphrase)
            self._derived[salt] = key
        return key

    def encrypt(self, plaintext: bytes) -> bytes:
        if self._salt is None:
            self._salt = os.urandom(_SALT_LEN)
        header = _MAGIC + bytes([_VERSION]) + self._salt
        nonce = os.urandom(_NONCE_LEN)
        ciphertext = AESGCM(self._key_for(self._salt)).encrypt(nonce, plaintext, header)
        return header + nonce + ciphertext

    def decrypt(self, blob: bytes) -> bytes:
        if len(blob) < _HEADER_LEN + _NONCE_LEN + 16 or not blob.startswith(_MAGIC):
            raise VaultCorruptedError("Vault file is not a recognised gemini vault.")
        version = blob[len(_MAGIC)]
        if version != _VERSION:
            raise VaultCorruptedError(f"Unsupported vault version: {version}.")
        header = blob[:_HEADER_LEN]
        salt = header[-_SALT_LEN:]
        nonce = blob[_HEADER_LEN : _HEADER_LEN + _NONCE_LEN]
        ciphertext = blob[_HEADER_LEN + _NONCE_LEN :]
        try:
            plaintext = AESGCM(self._key_for(salt)).decrypt(nonce, ciphertext, header)
        except InvalidTag as exc:
            raise InvalidKeyError(
                "Could not decrypt the vault: wrong GEMINI_MANAGER_KEY or the file was modified."
            ) from exc
        self._salt = salt  # keep the salt so later writes reuse the cached derived key
        return plaintext
