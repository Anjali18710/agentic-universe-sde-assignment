"""PII protection.

Two different jobs need two different tools:

* **Matching** events to leads by phone needs the *same* output for the same phone,
  so phones are turned into a keyed hash (HMAC-SHA256). Without the secret key,
  nobody can rebuild the phone list by hashing all 10-digit numbers (a plain SHA-256
  of a phone number could be brute-forced in minutes).
* **Reading back** PII (for example to print the masked phone) needs reversible
  encryption, so PII values are encrypted with Fernet (AES-128-CBC + HMAC).

Both keys are derived from one master secret, taken from the LEDGER_SECRET
environment variable, or else from a key file created next to the database.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet


class Vault:
    def __init__(self, master_secret: bytes):
        self._hash_key = hmac.new(master_secret, b"phone-lookup-hash", hashlib.sha256).digest()
        enc_key = hmac.new(master_secret, b"pii-encryption", hashlib.sha256).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(enc_key))

    @classmethod
    def load(cls, key_file: Path) -> "Vault":
        env = os.environ.get("LEDGER_SECRET")
        if env:
            return cls(env.encode())
        if not key_file.exists():
            key_file.write_text(secrets.token_hex(32))
            key_file.chmod(0o600)
        return cls(key_file.read_text().strip().encode())

    def phone_hash(self, canonical_phone: str) -> str:
        return hmac.new(self._hash_key, canonical_phone.encode(), hashlib.sha256).hexdigest()

    def encrypt(self, value) -> bytes:
        return self._fernet.encrypt(json.dumps(value, ensure_ascii=False).encode())

    def decrypt(self, token: bytes):
        return json.loads(self._fernet.decrypt(token))
