from __future__ import annotations

from bot.relay.admins import AdminCache


def test_known_admin_ttl() -> None:
    clock = {"t": 100.0}
    cache = AdminCache(ttl=10, monotonic=lambda: clock["t"])
    assert cache.known_admin(1, 5) is None
    cache.mark(1, 5, True)
    cache.mark(1, 7, False)
    assert cache.known_admin(1, 5) is True
    assert cache.known_admin(1, 7) is False
    clock["t"] += 11
    assert cache.known_admin(1, 5) is None


def test_apply_status_and_invalidate() -> None:
    cache = AdminCache(ttl=60)
    cache.apply_status(9, 1, "administrator")
    cache.apply_status(9, 2, "member")
    assert cache.known_admin(9, 1) is True
    assert cache.known_admin(9, 2) is False
    cache.invalidate(9)
    assert cache.known_admin(9, 1) is None
