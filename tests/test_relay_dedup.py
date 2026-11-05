from __future__ import annotations

from bot.relay.dedup import Dedup


def test_message_and_text_window() -> None:
    clock = {"t": 1_000.0}
    dedup = Dedup(monotonic=lambda: clock["t"])
    assert dedup.claim(1, 10, 5, "taksi kerak", 600) is None
    assert dedup.claim(1, 10, 5, "taksi kerak", 600) == "dup-message"
    assert dedup.claim(2, 11, 5, "taksi kerak", 600) == "dup-text"
    assert dedup.claim(2, 12, 7, "taksi kerak", 600) is None
    clock["t"] += 601
    assert dedup.claim(3, 13, 5, "taksi kerak", 600) is None


def test_release_allows_retry() -> None:
    dedup = Dedup()
    assert dedup.claim(1, 10, 5, "taksi kerak", 600) is None
    dedup.release(1, 10, 5, "taksi kerak")
    assert dedup.claim(1, 10, 5, "taksi kerak", 600) is None


def test_remember_from_storage() -> None:
    clock = {"t": 5_000.0}
    dedup = Dedup(monotonic=lambda: clock["t"])
    dedup.remember_message(9, 9)
    assert dedup.claim(9, 9, 1, "x", 600) == "dup-message"
    dedup.remember_text(3, "abc", age_sec=30)
    assert dedup.claim(1, 1, 3, "abc", 600) == "dup-text"
