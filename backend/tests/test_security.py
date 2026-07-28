import hashlib
import hmac
import time

from app.security import SecretBox, verify_messages_webhook


def test_secret_box_round_trip() -> None:
    box = SecretBox(SecretBox.generate_key())
    encrypted = box.encrypt_text("session-token")
    assert encrypted != "session-token"
    assert box.decrypt_text(encrypted) == "session-token"


def test_webhook_signature_and_timestamp() -> None:
    body = b'{"event":"message.received"}'
    timestamp = str(int(time.time() * 1000))
    signature = hmac.new(b"secret", f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    assert verify_messages_webhook(body, signature, timestamp, "secret")
    assert not verify_messages_webhook(body + b" ", signature, timestamp, "secret")
    assert not verify_messages_webhook(body, signature, "1", "secret")
