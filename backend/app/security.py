import hmac
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class EncryptionError(RuntimeError):
    pass


class SecretBox:
    def __init__(self, key: str):
        if not key:
            raise EncryptionError("ENCRYPTION_KEY is required for credential operations")
        try:
            self._fernet = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise EncryptionError("ENCRYPTION_KEY must be a valid Fernet key") from exc

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode()

    def encrypt_text(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt_text(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise EncryptionError("Encrypted credential could not be decrypted") from exc

    def encrypt_json(self, value: Any) -> str:
        return self.encrypt_text(json.dumps(value, separators=(",", ":"), default=str))

    def decrypt_json(self, token: str) -> Any:
        return json.loads(self.decrypt_text(token))


def constant_time_equal(left: str, right: str) -> bool:
    return bool(left and right) and hmac.compare_digest(left, right)
