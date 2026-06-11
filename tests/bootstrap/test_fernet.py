from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from discode.security.fernet import generate_hook_secret, load_multifernet


class TestLoadMultifernet:
    def test_valid_single_key_works(self) -> None:
        key = Fernet.generate_key().decode()
        mf = load_multifernet(key)
        assert mf is not None

    def test_valid_multiple_keys_work(self) -> None:
        key1 = Fernet.generate_key().decode()
        key2 = Fernet.generate_key().decode()
        mf = load_multifernet(f"{key1},{key2}")
        assert mf is not None

    def test_bad_key_raises(self) -> None:
        with pytest.raises(Exception):
            load_multifernet("not-a-valid-fernet-key")

    def test_empty_key_raises(self) -> None:
        with pytest.raises(Exception):
            load_multifernet("")

    def test_encrypt_decrypt_roundtrip(self) -> None:
        key = Fernet.generate_key().decode()
        mf = load_multifernet(key)
        plaintext = b"hello world"
        token = mf.encrypt(plaintext)
        assert mf.decrypt(token) == plaintext

    def test_roundtrip_with_multiple_keys(self) -> None:
        key1 = Fernet.generate_key().decode()
        key2 = Fernet.generate_key().decode()
        # Encrypt with key1 primary
        mf1 = load_multifernet(f"{key1},{key2}")
        token = mf1.encrypt(b"secret data")
        # Decrypt using same multi-key setup
        assert mf1.decrypt(token) == b"secret data"


class TestGenerateHookSecret:
    def test_returns_32_bytes(self) -> None:
        secret = generate_hook_secret()
        assert isinstance(secret, bytes)
        assert len(secret) == 32

    def test_is_random(self) -> None:
        # Two calls should produce different values (with overwhelming probability)
        s1 = generate_hook_secret()
        s2 = generate_hook_secret()
        assert s1 != s2
