from datetime import UTC, datetime

from app.briefs import _is_due


def test_due_window_uses_user_timezone(monkeypatch) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 7, 28, 11, 35, tzinfo=UTC)
            return value if tz else value.replace(tzinfo=None)

    monkeypatch.setattr("app.briefs.datetime", FixedDateTime)
    assert _is_due("America/Toronto", "07:30")
    assert not _is_due("America/Toronto", "08:00")
