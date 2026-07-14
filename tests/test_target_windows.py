from datetime import date, datetime, timedelta, timezone

from skyfield.api import wgs84

from astro import compute_target_windows, compute_target_windows_for_targets
from targets import TARGET_LIBRARY


TST = timezone(timedelta(hours=8))


def test_batch_target_windows_match_individual_results():
    query_date = date(2026, 7, 17)
    query_dates = [query_date]
    dark_windows = {
        query_date: [
            (
                datetime(2026, 7, 17, 20, 0, tzinfo=TST),
                datetime(2026, 7, 17, 22, 0, tzinfo=TST),
            )
        ]
    }
    observer = wgs84.latlon(23.865, 120.917)
    targets = TARGET_LIBRARY[:5]

    expected = []
    for target in targets:
        expected.extend(
            compute_target_windows(observer, target, query_dates, dark_windows)
        )

    actual = compute_target_windows_for_targets(
        observer,
        targets,
        query_dates,
        dark_windows,
    )

    assert actual == expected
