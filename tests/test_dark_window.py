# 暗空窗口計算（compute_dark_sky_window）
from datetime import datetime, timedelta, timezone

import main

TST = timezone(timedelta(hours=8))


def _dt(h, m=0, day=1):
    return datetime(2026, 7, day, h, m, tzinfo=TST)


def _twilight(ev_h=19, mo_h=4):
    return {
        "evening_astro_twilight": _dt(ev_h),
        "morning_astro_twilight": _dt(mo_h, day=2),
    }


class TestComputeDarkSkyWindow:
    def test_no_moon_full_night(self):
        moon = {"moonrise": None, "moonset": None,
                "moon_above_all_night": False, "moon_below_all_night": True}
        windows, desc = main.compute_dark_sky_window(_twilight(), moon)
        assert len(windows) == 1
        assert windows[0] == (_dt(19), _dt(4, day=2))
        assert "全夜無月光" in desc

    def test_moon_above_all_night(self):
        moon = {"moonrise": None, "moonset": None,
                "moon_above_all_night": True, "moon_below_all_night": False}
        windows, desc = main.compute_dark_sky_window(_twilight(), moon)
        assert windows == []
        assert "無有效暗空窗口" in desc

    def test_moonset_mid_night_gives_late_window(self):
        # 月落 23:00：暗空窗口應為 23:00 ～ 晨光
        moon = {"moonrise": None, "moonset": _dt(23),
                "moon_above_all_night": False, "moon_below_all_night": False}
        windows, _ = main.compute_dark_sky_window(_twilight(), moon)
        assert windows == [(_dt(23), _dt(4, day=2))]

    def test_moonrise_mid_night_gives_early_window(self):
        # 月出 01:00：暗空窗口應為 薄暮 ～ 01:00
        moon = {"moonrise": _dt(1, day=2), "moonset": None,
                "moon_above_all_night": False, "moon_below_all_night": False}
        windows, _ = main.compute_dark_sky_window(_twilight(), moon)
        assert windows == [(_dt(19), _dt(1, day=2))]

    def test_short_sliver_excluded(self):
        # 月落在晨光前 10 分鐘：剩餘窗口 < 30 分鐘應被排除
        moon = {"moonrise": None, "moonset": _dt(3, 50, day=2),
                "moon_above_all_night": False, "moon_below_all_night": False}
        windows, desc = main.compute_dark_sky_window(_twilight(), moon)
        assert windows == []
        assert "月光干擾嚴重" in desc

    def test_missing_twilight(self):
        moon = {"moonrise": None, "moonset": None,
                "moon_above_all_night": False, "moon_below_all_night": False}
        windows, desc = main.compute_dark_sky_window(
            {"evening_astro_twilight": None, "morning_astro_twilight": None}, moon)
        assert windows == []
        assert "薄暮" in desc

    def test_dark_window_minutes_helper(self):
        moon_info_day = {"dark_windows": [(_dt(19), _dt(21)), (_dt(23), _dt(23, 30))]}
        assert main.dark_window_minutes(moon_info_day) == 150
