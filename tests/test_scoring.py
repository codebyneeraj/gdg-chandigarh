from datetime import datetime, timedelta, timezone

import pytest

from saafroute.scoring import ReportPoint, count_repeats, hotspot_score

NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


def test_breakdown_adds_up():
    b = hotspot_score(4, NOW - timedelta(hours=36), NOW, 2, 21)
    assert (b.severity, b.age, b.repeats, b.site) == (40, 10, 10, 5)
    assert b.total == 65


def test_bounds():
    low = hotspot_score(1, NOW, NOW, 0, 21)
    high = hotspot_score(5, NOW - timedelta(days=30), NOW, 99, 17)
    assert low.total == 15
    assert high.total == 100          # age and repeats are capped


def test_sensitive_sector_scores_higher():
    assert hotspot_score(3, NOW, NOW, 0, 12).total > hotspot_score(3, NOW, NOW, 0, 21).total


def test_monotonic_in_severity_and_age():
    assert hotspot_score(4, NOW, NOW, 0, 21).total > hotspot_score(3, NOW, NOW, 0, 21).total
    assert (hotspot_score(3, NOW - timedelta(hours=10), NOW, 0, 21).total
            > hotspot_score(3, NOW - timedelta(hours=1), NOW, 0, 21).total)


def test_deterministic():
    a = hotspot_score(3, NOW - timedelta(hours=5), NOW, 1, 22)
    assert a == hotspot_score(3, NOW - timedelta(hours=5), NOW, 1, 22)


def test_invalid_severity():
    with pytest.raises(ValueError):
        hotspot_score(0, NOW, NOW, 0, 21)


def test_repeats_counts_only_near_recent_open():
    t = ReportPoint(1, 30.7333, 76.7794, NOW, True)
    others = [
        t,
        ReportPoint(2, 30.7340, 76.7800, NOW - timedelta(days=1), True),   # ~100 m, recent, open -> counts
        ReportPoint(3, 30.7340, 76.7800, NOW - timedelta(days=10), True),  # too old
        ReportPoint(4, 30.7340, 76.7800, NOW, False),                       # closed
        ReportPoint(5, 30.7500, 76.7800, NOW, True),                        # ~1.8 km away
    ]
    assert count_repeats(t, others) == 1
