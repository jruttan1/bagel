from app.security import SecretBox, constant_time_equal


def test_secret_box_round_trip() -> None:
    box = SecretBox(SecretBox.generate_key())
    encrypted = box.encrypt_text("session-token")
    assert encrypted != "session-token"
    assert box.decrypt_text(encrypted) == "session-token"


def test_constant_time_equal_requires_matching_nonempty_values() -> None:
    assert constant_time_equal("secret", "secret")
    assert not constant_time_equal("secret", "wrong")
    assert not constant_time_equal("", "")
