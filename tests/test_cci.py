# 出勤信心指數（compute_cci_for_date / _moon_illumination）
from datetime import datetime, timedelta, timezone

import main

TST = timezone(timedelta(hours=8))


def _dt(h, m=0, day=1):
    return datetime(2026, 7, day, h, m, tzinfo=TST)


def good_weather():
    return {"data_status": "ok", "cloud_cover": 10, "temp_c": 15.0, "dew_point_c": 5.0,
            "wind_speed_kmh": 5, "wind_beaufort": 1}


def bad_weather():
    return {"data_status": "ok", "cloud_cover": 95, "temp_c": 10.0, "dew_point_c": 9.5,
            "wind_speed_kmh": 45, "wind_beaufort": 6}


def new_moon_info(dark_hours=6):
    end_h = 19 + dark_hours
    end = _dt(end_h - 24, day=2) if end_h >= 24 else _dt(end_h)
    return {"moon_phase_pct": 0, "moon_above_all_night": False, "moon_below_all_night": True,
            "dark_windows": [(_dt(19), end)]}


def full_moon_info():
    return {"moon_phase_pct": 50, "moon_above_all_night": True, "moon_below_all_night": False,
            "dark_windows": []}


def good_seeing():
    return {"data_status": "ok", "seeing": 2, "transparency": 2}


def missing_seeing():
    return {"data_status": "missing", "seeing": -1, "transparency": -1}


class TestMoonIllumination:
    def test_new_moon(self):
        assert main._moon_illumination(0) < 0.01
        assert main._moon_illumination(100) < 0.01

    def test_full_moon(self):
        assert main._moon_illumination(50) > 0.99

    def test_quarter(self):
        assert abs(main._moon_illumination(25) - 0.5) < 0.01


class TestCciDefault:
    def test_ideal_night_high_score(self):
        windows = [{"in_dark_window": True}]
        cci = main.compute_cci_for_date(good_weather(), new_moon_info(), good_seeing(), windows)
        assert cci["score"] >= 80
        assert cci["label"].startswith("✅")
        assert cci["completeness"] == "full"

    def test_terrible_night_low_score(self):
        cci = main.compute_cci_for_date(bad_weather(), full_moon_info(),
                                        {"data_status": "ok", "seeing": 7, "transparency": 7}, [])
        assert cci["score"] < 20
        assert cci["label"].startswith("❌")

    def test_missing_weather_flags_completeness(self):
        cci = main.compute_cci_for_date({"data_status": "missing"}, new_moon_info(),
                                        good_seeing(), [{"in_dark_window": True}])
        assert cci["completeness"] == "partial"
        assert cci["breakdown"]["cloud"]["score"] == 0

    def test_missing_weather_and_seeing_astronomy_only(self):
        cci = main.compute_cci_for_date({"data_status": "missing"}, new_moon_info(),
                                        missing_seeing(), [{"in_dark_window": True}])
        assert cci["completeness"] == "astronomy_only"

    def test_deep_sky_wind_stricter_than_milky_way(self):
        # 3 級風：深空（上限2級）應 0 分，星野（上限3級）應 65 分
        w = good_weather()
        w["wind_speed_kmh"] = 15
        w["wind_beaufort"] = 3
        cci_ds = main.compute_cci_for_date(w, new_moon_info(), good_seeing(),
                                           [{"in_dark_window": True}], wind_profile="deep_sky")
        cci_mw = main.compute_cci_for_date(w, new_moon_info(), good_seeing(),
                                           [{"in_dark_window": True}], wind_profile="milky_way")
        assert cci_ds["breakdown"]["wind"]["score"] == 0
        assert cci_mw["breakdown"]["wind"]["score"] == 65

    def test_score_bounds(self):
        for weather in (good_weather(), bad_weather(), {"data_status": "missing"}):
            for moon in (new_moon_info(), full_moon_info()):
                cci = main.compute_cci_for_date(weather, moon, good_seeing(), [])
                assert 0 <= cci["score"] <= 100


class TestCciProfiles:
    def test_moonscape_prefers_full_moon(self):
        # 月景題材：滿月分數應高於新月（與深空邏輯相反）
        cci_full = main.compute_cci_for_date(good_weather(), full_moon_info(), good_seeing(),
                                             [], cci_profile="moonscape")
        cci_new = main.compute_cci_for_date(good_weather(), new_moon_info(), good_seeing(),
                                            [], cci_profile="moonscape")
        assert cci_full["score"] > cci_new["score"]

    def test_meteor_zhr_peak_boost(self):
        showers = [{"zenithal_hourly_rate": 120, "days_to_peak": 0}]
        cci_peak = main.compute_cci_for_date(good_weather(), new_moon_info(), good_seeing(),
                                             [], cci_profile="meteor",
                                             extra_data={"showers": showers})
        cci_none = main.compute_cci_for_date(good_weather(), new_moon_info(), good_seeing(),
                                             [], cci_profile="meteor")
        assert cci_peak["score"] >= cci_none["score"]

    def test_comet_layer1_has_neutral_target_and_note(self):
        cci = main.compute_cci_for_date(good_weather(), new_moon_info(), good_seeing(),
                                        [], cci_profile="comet_layer1")
        assert cci["breakdown"]["target"]["score"] == 50
        assert any("彗星" in n for n in cci["profile_notes"])

    def test_lunar_eclipse_moon_below_zero_target(self):
        moon = {"moon_phase_pct": 50, "moon_above_all_night": False,
                "moon_below_all_night": True, "dark_windows": []}
        cci = main.compute_cci_for_date(good_weather(), moon, good_seeing(),
                                        [], cci_profile="lunar_eclipse")
        assert cci["breakdown"]["target"]["score"] == 0
