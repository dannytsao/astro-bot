# 氣象/視寧度 API 快取（weather._forecast_cache_*）
from datetime import date

import weather


def setup_function(_):
    with weather._forecast_cache_lock:
        weather._forecast_cache.clear()


class TestForecastCache:
    def test_put_and_get(self):
        key = weather._forecast_cache_key("open-meteo", 24.167, 121.283, [date(2026, 7, 5)])
        value = {date(2026, 7, 5): {"data_status": "ok", "cloud_cover": 10}}
        weather._forecast_cache_put(key, value)
        cached = weather._forecast_cache_get(key)
        assert cached == value

    def test_get_returns_copy_not_reference(self):
        key = weather._forecast_cache_key("open-meteo", 24.167, 121.283, [date(2026, 7, 5)])
        value = {date(2026, 7, 5): {"data_status": "ok", "cloud_cover": 10}}
        weather._forecast_cache_put(key, value)
        first = weather._forecast_cache_get(key)
        first[date(2026, 7, 5)]["cloud_cover"] = 999  # 呼叫端改動不可污染快取
        second = weather._forecast_cache_get(key)
        assert second[date(2026, 7, 5)]["cloud_cover"] == 10

    def test_key_normalization(self):
        k1 = weather._forecast_cache_key("7timer", 24.1670001, 121.283, [date(2026, 7, 5), date(2026, 7, 6)])
        k2 = weather._forecast_cache_key("7timer", 24.167, 121.2830002, [date(2026, 7, 6), date(2026, 7, 5)])
        assert k1 == k2  # 座標四捨五入 + 日期排序

    def test_expired_entry_evicted(self, monkeypatch):
        key = weather._forecast_cache_key("open-meteo", 24.0, 121.0, [date(2026, 7, 5)])
        value = {date(2026, 7, 5): {"data_status": "ok"}}
        weather._forecast_cache_put(key, value)
        real_monotonic = weather.time.monotonic
        monkeypatch.setattr(weather.time, "monotonic",
                            lambda: real_monotonic() + weather.FORECAST_CACHE_TTL_SECONDS + 1)
        assert weather._forecast_cache_get(key) is None

    def test_weather_api_failure_not_cached(self, monkeypatch):
        # API 失敗（全部 missing）不可寫入快取
        calls = {"n": 0}

        def fake_uncached(lat, lon, query_dates):
            calls["n"] += 1
            return {d: {"data_status": "missing"} for d in query_dates}

        monkeypatch.setattr(weather, "_check_weather_multi_uncached", fake_uncached)
        dates = [date(2026, 7, 5)]
        weather.check_weather_multi(24.0, 121.0, dates)
        weather.check_weather_multi(24.0, 121.0, dates)
        assert calls["n"] == 2  # 兩次都真的打 API

    def test_weather_success_cached(self, monkeypatch):
        calls = {"n": 0}

        def fake_uncached(lat, lon, query_dates):
            calls["n"] += 1
            return {d: {"data_status": "ok", "cloud_cover": 15} for d in query_dates}

        monkeypatch.setattr(weather, "_check_weather_multi_uncached", fake_uncached)
        dates = [date(2026, 7, 5)]
        r1 = weather.check_weather_multi(24.0, 121.0, dates)
        r2 = weather.check_weather_multi(24.0, 121.0, dates)
        assert calls["n"] == 1  # 第二次走快取
        assert r1 == r2

    def test_main_reexports_cached_functions(self):
        # main.py 透過 import 使用同一份函式物件
        import main
        assert main.check_weather_multi is weather.check_weather_multi
        assert main.get_7timer_seeing is weather.get_7timer_seeing
