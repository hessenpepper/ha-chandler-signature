# Written by Claude Sonnet 5.5 (claude-sonnet-5-5), effort level: medium.
import pytest

from chandler_signature.models import clock_drift_minutes, should_auto_sync


@pytest.mark.parametrize(
    ("valve", "now", "expected"),
    [
        ((12, 0), (12, 0), 0),
        ((12, 5), (12, 0), 5),  # valve ahead
        ((11, 55), (12, 0), -5),  # valve behind
        ((23, 59), (0, 1), -2),  # wraps midnight, behind
        ((0, 1), (23, 59), 2),  # wraps midnight, ahead
        ((0, 8), (19, 48), 260),  # 4 h 20 min ahead (as seen on the softener)
        ((16, 5), (19, 48), -223),
        ((6, 0), (18, 0), -720),  # exactly opposite: reported as -12 h
    ],
)
def test_drift_is_signed_and_circular(valve, now, expected):
    assert clock_drift_minutes(*valve, *now) == expected


@pytest.mark.parametrize(
    ("hour", "minute"), [(None, 5), (5, None), ("5", 5), (24, 0), (0, 60), (-1, 0), (True, 3)]
)
def test_drift_is_none_without_a_valid_time(hour, minute):
    assert clock_drift_minutes(hour, minute, 12, 0) is None


def test_should_auto_sync_threshold():
    assert not should_auto_sync(None, 2, None, 3600)
    assert not should_auto_sync(1, 2, None, 3600)
    assert not should_auto_sync(-1, 2, None, 3600)
    assert should_auto_sync(2, 2, None, 3600)
    assert should_auto_sync(-2, 2, None, 3600)
    assert should_auto_sync(260, 2, None, 3600)


def test_should_auto_sync_cooldown():
    assert not should_auto_sync(30, 2, 120.0, 3600)  # tried 2 min ago
    assert not should_auto_sync(30, 2, 3599.0, 3600)
    assert should_auto_sync(30, 2, 3600.0, 3600)
