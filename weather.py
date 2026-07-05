# 氣象與視寧度資料來源：Open-Meteo（雲量/溫濕度/風）、7Timer（視寧度/透明度）。
# 含 TTL 快取；只快取成功結果，API 錯誤不快取。不可猜測：缺資料一律回報 missing。
import requests
import threading
import time
from datetime import datetime, timedelta, timezone, date

def wind_kmh_to_beaufort(speed_kmh):
    if speed_kmh is None or speed_kmh < 0:
        return -1
    thresholds = [1, 6, 12, 20, 29, 39, 50, 62, 75, 89, 103, 118]
    for level, upper in enumerate(thresholds):
        if speed_kmh < upper:
            return level
    return 12


# ── 氣象 / 視寧度 API 快取 ─────────────────────────────────────
# 同一 (座標, 日期組) 的預報在 TTL 內直接重用，避免最佳地點排名與重複查詢
# 對 Open-Meteo / 7Timer 重複打 API。只快取成功結果，API 錯誤不快取。
FORECAST_CACHE_TTL_SECONDS = 30 * 60
_FORECAST_CACHE_MAX_ENTRIES = 512
_forecast_cache = {}
_forecast_cache_lock = threading.Lock()

def _forecast_cache_get(key):
    with _forecast_cache_lock:
        entry = _forecast_cache.get(key)
        if entry is None:
            return None
        stored_at, value = entry
        if (time.monotonic() - stored_at) >= FORECAST_CACHE_TTL_SECONDS:
            _forecast_cache.pop(key, None)
            return None
        # 回傳每日 dict 的淺拷貝，避免呼叫端改動污染快取
        return {d: dict(v) for d, v in value.items()}

def _forecast_cache_put(key, value):
    with _forecast_cache_lock:
        if len(_forecast_cache) >= _FORECAST_CACHE_MAX_ENTRIES:
            _forecast_cache.clear()
        _forecast_cache[key] = (time.monotonic(), {d: dict(v) for d, v in value.items()})

def _forecast_cache_key(kind, lat, lon, query_dates):
    return (kind, round(float(lat), 3), round(float(lon), 3),
            tuple(sorted(d.isoformat() for d in query_dates)))


def check_weather_multi(lat, lon, query_dates):
    if not query_dates:
        return {}
    key = _forecast_cache_key("open-meteo", lat, lon, query_dates)
    cached = _forecast_cache_get(key)
    if cached is not None:
        return cached
    result = _check_weather_multi_uncached(lat, lon, query_dates)
    if any(v.get("data_status") == "ok" for v in result.values()):
        _forecast_cache_put(key, result)
    return result


def _check_weather_multi_uncached(lat, lon, query_dates):
    if not query_dates:
        return {}
    today = date.today()
    max_d = today + timedelta(days=15)
    valid = [d for d in query_dates if today <= d <= max_d]
    def weather_missing(reason):
        return {
            "cloud_cover": -1, "humidity": -1, "temp_c": -1,
            "dew_point_c": -1, "dew_risk": False, "good_weather": False,
            "visibility_km": -1, "wind_speed_kmh": -1, "wind_beaufort": -1,
            "data_status": "missing",
            "data_source": "Open-Meteo", "missing_reason": reason,
        }
    if not valid:
        return {d: weather_missing("查詢日期超出 Open-Meteo 預報範圍 15 天") for d in query_dates}
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
           f"&hourly=cloud_cover,visibility,relative_humidity_2m,temperature_2m,dew_point_2m,wind_speed_10m"
           f"&start_date={min(valid).isoformat()}&end_date={max(valid).isoformat()}"
           f"&timezone=Asia%2FTaipei")
    try:
        raw = requests.get(url, timeout=10).json()
    except Exception as e:
        print(f"[Open-Meteo 錯誤] {type(e).__name__}: {e}", flush=True)
        return {d: weather_missing(f"Open-Meteo API 錯誤：{type(e).__name__}") for d in query_dates}
    if "hourly" not in raw:
        return {d: weather_missing("Open-Meteo 回傳缺少 hourly 資料") for d in query_dates}
    data = raw["hourly"]
    hi   = {}
    for i, t_str in enumerate(data["time"]):
        dt = datetime.fromisoformat(t_str)
        hi[dt] = {
            "cloud_cover": data["cloud_cover"][i],
            "humidity":    data["relative_humidity_2m"][i],
            "temp_c":      data["temperature_2m"][i],
            "dew_point_c": data["dew_point_2m"][i],
            "visibility":  data["visibility"][i],
            "wind_speed":  data.get("wind_speed_10m", [-1] * len(data["time"]))[i],
        }
    daily = {}
    for d in query_dates:
        if d not in valid:
            daily[d] = weather_missing("查詢日期超出 Open-Meteo 預報範圍 15 天")
            continue
        night = []
        for h in [20, 21, 22, 23, 0, 1, 2]:
            cd = d if h >= 20 else d + timedelta(days=1)
            k  = datetime(cd.year, cd.month, cd.day, h)
            if k in hi:
                night.append(hi[k])
        if night:
            ac = round(sum(x["cloud_cover"] for x in night) / len(night), 1)
            ah = round(sum(x["humidity"]    for x in night) / len(night), 1)
            at = round(sum(x["temp_c"]      for x in night) / len(night), 1)
            ad = round(sum(x["dew_point_c"] for x in night) / len(night), 1)
            av = round(sum(x["visibility"]  for x in night) / len(night) / 1000, 1)
            max_wind = round(max(x.get("wind_speed", -1) for x in night), 1)
            daily[d] = {
                "cloud_cover": ac, "humidity": ah,
                "temp_c": at, "dew_point_c": ad,
                "dew_risk":       (at - ad) < 1.5,
                "good_weather":   ac <= 40,
                "visibility_km":  av,
                "wind_speed_kmh": max_wind,
                "wind_beaufort":  wind_kmh_to_beaufort(max_wind),
                "data_status":    "ok",
                "data_source":    "Open-Meteo",
                "missing_reason": "",
            }
        else:
            daily[d] = weather_missing("Open-Meteo 回傳中缺少夜間時段資料")
    return daily


def get_7timer_seeing(lat, lon, query_dates):
    if not query_dates:
        return {}
    key = _forecast_cache_key("7timer", lat, lon, query_dates)
    cached = _forecast_cache_get(key)
    if cached is not None:
        return cached
    result = _get_7timer_seeing_uncached(lat, lon, query_dates)
    if any(v.get("data_status") == "ok" for v in result.values()):
        _forecast_cache_put(key, result)
    return result


def _get_7timer_seeing_uncached(lat, lon, query_dates):
    def seeing_missing(reason):
        return {
            "seeing": -1, "transparency": -1, "data_status": "missing",
            "data_source": "7Timer", "missing_reason": reason,
        }
    try:
        url = (f"http://www.7timer.info/bin/astro.php"
               f"?lon={lon}&lat={lat}&ac=0&unit=metric&output=json&tzoffset=8")
        raw = requests.get(url, timeout=10).json()
        init_dt = datetime.strptime(raw["init"], "%Y%m%d%H").replace(tzinfo=timezone.utc)
    except Exception as e:
        print(f"[7Timer 錯誤] {e}", flush=True)
        return {d: seeing_missing(f"7Timer API 錯誤：{type(e).__name__}") for d in query_dates}
    tz_tst = timezone(timedelta(hours=8))
    hourly = {}
    for item in raw.get("dataseries", []):
        dt_tst = (init_dt + timedelta(hours=item["timepoint"])).astimezone(tz_tst)
        s = item.get("seeing", -1)
        t = item.get("transparency", -1)
        if s > 0 and t > 0:
            hourly[dt_tst] = {"seeing": s, "transparency": t}
    daily = {}
    for d in query_dates:
        night = []
        for dt_tst, v in hourly.items():
            h = dt_tst.hour
            if (dt_tst.date() == d and h >= 20) or \
               (dt_tst.date() == d + timedelta(days=1) and h <= 2):
                night.append(v)
        if night:
            daily[d] = {
                "seeing":       round(sum(x["seeing"]       for x in night) / len(night), 1),
                "transparency": round(sum(x["transparency"] for x in night) / len(night), 1),
                "data_status":  "ok",
                "data_source":  "7Timer",
                "missing_reason": "",
            }
        else:
            daily[d] = seeing_missing("7Timer 回傳中缺少夜間視寧度/透明度資料")
    return daily


