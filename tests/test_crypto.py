import pytest

from gemini_webapi_manager.crypto import VaultCipher, generate_key
from gemini_webapi_manager.errors import InvalidKeyError, VaultCorruptedError


def test_roundtrip_and_nonce_uniqueness():
    cipher = VaultCipher("a-long-enough-passphrase")
    first = cipher.encrypt(b"secret")
    second = cipher.encrypt(b"secret")
    assert first != second
    assert b"secret" not in first
    assert cipher.decrypt(first) == b"secret"
    # same salt reused across writes
    assert first[5:21] == second[5:21]


def test_opens_with_fresh_cipher_using_same_passphrase():
    blob = VaultCipher("a-long-enough-passphrase").encrypt(b"data")
    assert VaultCipher("a-long-enough-passphrase").decrypt(blob) == b"data"


def test_wrong_key_rejected():
    blob = VaultCipher("a-long-enough-passphrase").encrypt(b"data")
    with pytest.raises(InvalidKeyError):
        VaultCipher("another-long-passphrase!").decrypt(blob)


def test_tampering_detected():
    blob = bytearray(VaultCipher("a-long-enough-passphrase").encrypt(b"data"))
    blob[-1] ^= 1
    with pytest.raises(InvalidKeyError):
        VaultCipher("a-long-enough-passphrase").decrypt(bytes(blob))


def test_garbage_rejected():
    with pytest.raises(VaultCorruptedError):
        VaultCipher("a-long-enough-passphrase").decrypt(b"not a vault at all, really not")


def test_generate_key_is_long_and_random():
    assert len(generate_key()) >= 32
    assert generate_key() != generate_key()
