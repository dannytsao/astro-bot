# 觀測區間氣象聚合與區間化 CCI 測試：
# Go/No-Go 必須以目標起落區間內的氣象為準，而非整夜平均。
from datetime import date, datetime, timedelta, timezone

from skyfield.api import wgs84

import weather
from weather import aggregate_weather_interval, aggregate_seeing_interval
from cci import compute_cci_for_date, resolve_observation_interval
from astro import compute_target_windows_for_targets
from targets import TARGET_LIBRARY

TST = timezone(timedelta(hours=8))
D = date(2026, 7, 17)


def _hour(h, day_offset=0):
    d = D + timedelta(days=day_offset)
    return datetime(d.year, d.month, d.day, h, tzinfo=TST)


def make_weather_day(cloud_by_hour):
    """cloud_by_hour: {aware datetime: cloud%}；其餘欄位給固定值。"""
    hourly = []
    for t, c in sorted(cloud_by_hour.items()):
        hourly.append({
            "time_tst": t, "cloud_cover": c, "humidity": 70,
            "temp_c": 15.0, "dew_point_c": 10.0,
            "visibility": 20000, "wind_speed": 10,
        })
    return {
        "cloud_cover": 50, "humidity": 70, "temp_c": 15.0, "dew_point_c": 10.0,
        "cloud_cover_max": 90, "min_temp_dew_diff": 5.0,
        "dew_risk": False, "good_weather": False, "visibility_km": 20.0,
        "wind_speed_kmh": 10, "wind_beaufort": 2,
        "data_status": "ok", "data_source": "Open-Meteo", "missing_reason": "",
        "aggregation": "night_avg", "hourly_night": hourly,
    }


class TestAggregateWeatherInterval:
    def test_uses_only_hours_inside_interval(self):
        # 入夜多雲（80%）、凌晨轉晴（10%）：目標區間在凌晨 → 應反映凌晨的好天氣
        cloud = {_hour(h): 80 for h in range(18, 24)}
        cloud.update({_hour(h, 1): 10 for h in range(0, 7)})
        day = make_weather_day(cloud)
        agg = aggregate_weather_interval(day, _hour(1, 1), _hour(4, 1))
        assert agg is not None
        assert agg["aggregation"] == "target_window"
        assert agg["cloud_cover"] == 10
        assert agg["cloud_cover_max"] == 10
        assert agg["good_weather"] is True

    def test_reports_peak_within_interval(self):
        cloud = {_hour(20): 10, _hour(21): 10, _hour(22): 90, _hour(23): 10}
        day = make_weather_day(cloud)
        agg = aggregate_weather_interval(day, _hour(20), _hour(23))
        assert agg["cloud_cover"] == 30
        assert agg["cloud_cover_max"] == 90

    def test_dew_uses_worst_hour(self):
        cloud = {_hour(20): 10, _hour(21): 10, _hour(22): 10}
        day = make_weather_day(cloud)
        # 22 時 T−Td 掉到 0.5°C（高風險），平均值卻安全 → 必須抓最差小時
        day["hourly_night"][2]["temp_c"] = 10.5
        day["hourly_night"][2]["dew_point_c"] = 10.0
        agg = aggregate_weather_interval(day, _hour(20), _hour(22))
        assert agg["min_temp_dew_diff"] == 0.5
        assert agg["dew_risk"] is True

    def test_wind_uses_interval_max(self):
        cloud = {_hour(20): 10, _hour(21): 10}
        day = make_weather_day(cloud)
        day["hourly_night"][1]["wind_speed"] = 45
        agg = aggregate_weather_interval(day, _hour(20), _hour(21))
        assert agg["wind_speed_kmh"] == 45
        assert agg["wind_beaufort"] == weather.wind_kmh_to_beaufort(45)

    def test_short_interval_snaps_to_adjacent_hours(self):
        cloud = {_hour(2, 1): 20, _hour(3, 1): 40}
        day = make_weather_day(cloud)
        agg = aggregate_weather_interval(day, _hour(2, 1) + timedelta(minutes=10),
                                         _hour(2, 1) + timedelta(minutes=50))
        assert agg is not None
        assert agg["hours_used"] >= 1

    def test_fallback_none_when_no_hourly(self):
        day = make_weather_day({_hour(20): 10})
        day["hourly_night"] = []
        assert aggregate_weather_interval(day, _hour(20), _hour(23)) is None

    def test_fallback_none_when_missing_day(self):
        assert aggregate_weather_interval({"data_status": "missing"}, _hour(20), _hour(23)) is None
        assert aggregate_weather_interval({}, _hour(20), _hour(23)) is None
        assert aggregate_weather_interval(None, _hour(20), _hour(23)) is None

    def test_fallback_none_when_interval_invalid(self):
        day = make_weather_day({_hour(20): 10})
        assert aggregate_weather_interval(day, None, _hour(23)) is None
        assert aggregate_weather_interval(day, _hour(23), _hour(20)) is None

    def test_fallback_none_when_no_points_in_interval(self):
        day = make_weather_day({_hour(20): 10})
        assert aggregate_weather_interval(day, _hour(2, 1), _hour(4, 1)) is None


class TestAggregateSeeingInterval:
    def make_seeing_day(self):
        return {
            "seeing": 4.0, "transparency": 4.0, "data_status": "ok",
            "data_source": "7Timer", "missing_reason": "",
            "aggregation": "night_avg",
            "points_night": [
                {"time_tst": _hour(20), "seeing": 6, "transparency": 6},
                {"time_tst": _hour(23), "seeing": 4, "transparency": 4},
                {"time_tst": _hour(2, 1), "seeing": 2, "transparency": 2},
            ],
        }

    def test_three_hour_blocks_intersect_interval(self):
        # 區間 02:30–05:00 只與 02:00 起的 3 小時 block 相交 → 凌晨的好視寧度
        agg = aggregate_seeing_interval(self.make_seeing_day(),
                                        _hour(2, 1) + timedelta(minutes=30), _hour(5, 1))
        assert agg is not None
        assert agg["seeing"] == 2
        assert agg["transparency"] == 2
        assert agg["points_used"] == 1

    def test_none_when_no_overlap(self):
        agg = aggregate_seeing_interval(self.make_seeing_day(), _hour(6, 1), _hour(7, 1))
        assert agg is None

    def test_none_when_missing(self):
        assert aggregate_seeing_interval({"data_status": "missing"}, _hour(20), _hour(23)) is None
        assert aggregate_seeing_interval(None, _hour(20), _hour(23)) is None


class TestResolveObservationInterval:
    def moon_day(self, **kw):
        base = {
            "date": D,
            "dark_windows": [(_hour(20), _hour(23)), (_hour(1, 1), _hour(4, 1))],
            "moonrise": None, "moonset": None,
            "moon_above_all_night": False, "moon_below_all_night": False,
        }
        base.update(kw)
        return base

    def test_default_uses_target_window_bounds(self):
        wins = [
            {"window_start_tst": _hour(2, 1), "window_end_tst": _hour(4, 1)},
            {"window_start_tst": _hour(1, 1), "window_end_tst": _hour(3, 1)},
        ]
        s, e, src = resolve_observation_interval("default", self.moon_day(), wins)
        assert (s, e) == (_hour(1, 1), _hour(4, 1))
        assert src == "target_windows"

    def test_default_falls_back_to_dark_windows(self):
        s, e, src = resolve_observation_interval("default", self.moon_day(), [])
        assert (s, e) == (_hour(20), _hour(4, 1))
        assert src == "dark_windows"

    def test_meteor_uses_dark_windows(self):
        s, e, src = resolve_observation_interval("meteor", self.moon_day(), [])
        assert (s, e) == (_hour(20), _hour(4, 1))
        assert src == "dark_windows"

    def test_moonscape_moon_above_all_night(self):
        s, e, src = resolve_observation_interval(
            "moonscape", self.moon_day(moon_above_all_night=True), [])
        assert (s, e) == (_hour(18), _hour(6, 1))
        assert src == "moon_up"

    def test_moonscape_moon_below_all_night(self):
        s, e, src = resolve_observation_interval(
            "moonscape", self.moon_day(moon_below_all_night=True), [])
        assert (s, e, src) == (None, None, None)

    def test_moonscape_rise_before_set(self):
        s, e, src = resolve_observation_interval(
            "moonscape", self.moon_day(moonrise=_hour(21), moonset=_hour(3, 1)), [])
        assert (s, e) == (_hour(21), _hour(3, 1))
        assert src == "moon_up"

    def test_moonscape_set_then_rise_picks_longer_segment(self):
        # 傍晚月落 19:30、凌晨月出 04:00 → 兩段中取與夜間重疊較長者
        s, e, src = resolve_observation_interval(
            "moonscape",
            self.moon_day(moonset=_hour(19) + timedelta(minutes=30), moonrise=_hour(4, 1)), [])
        assert src == "moon_up"
        assert (s, e) == (_hour(4, 1), _hour(6, 1))

    def test_unresolvable_returns_none(self):
        s, e, src = resolve_observation_interval(
            "default", self.moon_day(dark_windows=[]), [])
        assert (s, e, src) == (None, None, None)


class TestTargetWindowSpan:
    def test_windows_include_visible_span(self):
        dark = {D: [(_hour(20), _hour(23))]}
        observer = wgs84.latlon(23.865, 120.917)
        windows = compute_target_windows_for_targets(
            observer, TARGET_LIBRARY[:5], [D], dark)
        assert windows
        for w in windows:
            assert w.get("window_start_tst") is not None
            assert w.get("window_end_tst") is not None
            assert w["window_start_tst"] <= w["datetime_tst"] <= w["window_end_tst"]


class TestCciIntervalScoring:
    def moon_day(self):
        return {
            "date": D, "moon_phase_pct": 0.0,
            "dark_windows": [(_hour(20), _hour(4, 1))],
        }

    def base_weather(self, **kw):
        day = {
            "cloud_cover": 10, "humidity": 70, "temp_c": 15.0, "dew_point_c": 10.0,
            "cloud_cover_max": 10, "min_temp_dew_diff": 5.0,
            "dew_risk": False, "good_weather": True, "visibility_km": 20.0,
            "wind_speed_kmh": 10, "wind_beaufort": 2,
            "data_status": "ok", "data_source": "Open-Meteo", "missing_reason": "",
            "aggregation": "target_window",
            "window_start": _hour(1, 1), "window_end": _hour(4, 1),
        }
        day.update(kw)
        return day

    def seeing_day(self):
        return {"seeing": 2, "transparency": 2, "data_status": "ok"}

    def windows(self):
        return [{"in_dark_window": True}]

    def test_cloud_raw_labels_interval(self):
        cci = compute_cci_for_date(self.base_weather(), self.moon_day(),
                                   self.seeing_day(), self.windows())
        assert "01:00–04:00 觀測區間" in cci["breakdown"]["cloud"]["raw"]
        assert "峰值" in cci["breakdown"]["cloud"]["raw"]

    def test_night_avg_fallback_is_labelled(self):
        day = self.base_weather(aggregation="night_avg")
        day.pop("window_start"); day.pop("window_end")
        cci = compute_cci_for_date(day, self.moon_day(),
                                   self.seeing_day(), self.windows())
        assert "整夜平均" in cci["breakdown"]["cloud"]["raw"]
        assert any("整夜平均" in n for n in cci["profile_notes"])

    def test_cloud_peak_spread_adds_risk_note(self):
        day = self.base_weather(cloud_cover=25, cloud_cover_max=85)
        cci = compute_cci_for_date(day, self.moon_day(),
                                   self.seeing_day(), self.windows())
        assert any("雲量起伏大" in n for n in cci["profile_notes"])

    def test_dew_scoring_uses_worst_hour(self):
        # 平均 T−Td=5°C 安全，但最差小時 0.5°C → 高風險 0 分
        day = self.base_weather(min_temp_dew_diff=0.5, dew_risk=True)
        cci = compute_cci_for_date(day, self.moon_day(),
                                   self.seeing_day(), self.windows())
        assert cci["breakdown"]["dew"]["score"] == 0
        assert "最差小時" in cci["breakdown"]["dew"]["raw"]

    def test_interval_cloud_changes_go_nogo(self):
        # 同一晚：整夜平均雲量 60%（謹慎），目標區間 10%（好天）→ CCI 顯著提升
        night_avg = self.base_weather(aggregation="night_avg", cloud_cover=60,
                                      cloud_cover_max=90, good_weather=False)
        interval = self.base_weather(cloud_cover=10, cloud_cover_max=15)
        cci_avg = compute_cci_for_date(night_avg, self.moon_day(),
                                       self.seeing_day(), self.windows())
        cci_int = compute_cci_for_date(interval, self.moon_day(),
                                       self.seeing_day(), self.windows())
        assert cci_int["score"] > cci_avg["score"]


class TestOpenMeteoHourlyNight:
    def test_hourly_night_spans_evening_to_dawn(self, monkeypatch):
        # 兩天逐小時假資料：驗證 hourly_night 涵蓋當日 18:00 → 隔日 06:00
        times, cloud = [], []
        for day_offset in range(0, 2):
            d = D + timedelta(days=day_offset)
            for h in range(0, 24):
                times.append(f"{d.isoformat()}T{h:02d}:00")
                cloud.append(30)
        n = len(times)
        fake = {"hourly": {
            "time": times, "cloud_cover": cloud,
            "relative_humidity_2m": [70] * n, "temperature_2m": [15.0] * n,
            "dew_point_2m": [10.0] * n, "visibility": [20000] * n,
            "wind_speed_10m": [10] * n,
        }}

        class FakeResp:
            def json(self):
                return fake

        monkeypatch.setattr(weather, "date", _FakeDate)
        monkeypatch.setattr(weather.requests, "get", lambda url, timeout: FakeResp())
        result = weather._check_weather_multi_uncached(24.0, 121.0, [D])
        day = result[D]
        assert day["data_status"] == "ok"
        hours = [x["time_tst"] for x in day["hourly_night"]]
        assert hours[0] == _hour(18)
        assert hours[-1] == _hour(6, 1)
        assert len(hours) == 13
        assert day["cloud_cover_max"] == 30
        assert "min_temp_dew_diff" in day


class _FakeDate:
    @staticmethod
    def today():
        return D
