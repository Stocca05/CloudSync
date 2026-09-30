import base64
import hashlib
import hmac
import json
import secrets

from cryptography.fernet import Fernet


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(value: str) -> str:
    salt = secrets.token_bytes(16)
    result = hashlib.scrypt(value.encode(), salt=salt, n=16384, r=8, p=1)
    return base64.b64encode(salt + result).decode()


def verify_password(value: str, stored: str) -> bool:
    raw = base64.b64decode(stored)
    result = hashlib.scrypt(value.encode(), salt=raw[:16], n=16384, r=8, p=1)
    return hmac.compare_digest(raw[16:], result)


class Vault:
    def __init__(self, key: str):
        self.cipher = Fernet(key.encode())

    def encrypt(self, data: dict) -> str:
        return self.cipher.encrypt(json.dumps(data).encode()).decode()

    def decrypt(self, data: str) -> dict:
        return json.loads(self.cipher.decrypt(data.encode()))
