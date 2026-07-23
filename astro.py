# 天文計算：Skyfield 初始化、薄暮、月出月落、暗空窗口、銀河構圖、目標觀測窗口。
# 純天文計算，不依賴 LLM 與氣象 API。
from datetime import datetime, timedelta, timezone, date

from skyfield.api import Star, load
from skyfield import almanac

from targets import METEOR_SHOWERS, MILKY_WAY_CORE

# ── Skyfield 初始化 ────────────────────────────────────────────

ts  = load.timescale()
eph = load("de421.bsp")


# ── 輔助函式 ──────────────────────────────────────────────────

def az_to_direction(az_deg):
    dirs = ["正北","東北","正東","東南","正南","西南","正西","西北"]
    idx = round(az_deg / 45) % 8
    return dirs[idx]

def get_moon_phase_emoji(p):
    p = p % 1.0
    if p < 0.03 or p > 0.97: return "🌑 新月（最佳拍攝）"
    elif p < 0.22:            return "🌒 眉月（尚可）"
    elif p < 0.28:            return "🌓 上弦月（有干擾）"
    elif p < 0.47:            return "🌔 盈凸月（明顯干擾）"
    elif p < 0.53:            return "🌕 滿月（深空不宜）"
    elif p < 0.72:            return "🌖 虧凸月（明顯干擾）"
    elif p < 0.78:            return "🌗 下弦月（有干擾）"
    else:                     return "🌘 殘月（尚可）"

def check_meteor_shower(query_date):
    results = []
    for shower in METEOR_SHOWERS:
        peak = date(query_date.year, shower["peak_month"], shower["peak_day"])
        if abs((query_date - peak).days) <= 3:
            results.append({**shower, "days_to_peak": (peak - query_date).days})
    return results

# ── 天文薄暮時刻計算 ──────────────────────────────────────────

def get_astronomical_twilight(observer, query_date):
    tz_tst = timezone(timedelta(hours=8))
    try:
        t0 = ts.from_datetime(datetime(query_date.year, query_date.month, query_date.day,
                                        4, 0, tzinfo=timezone.utc))
        t1 = ts.from_datetime(datetime(query_date.year, query_date.month, query_date.day,
                                        4, 0, tzinfo=timezone.utc) + timedelta(days=1))
        f = almanac.dark_twilight_day(eph, observer)
        times, events = almanac.find_discrete(t0, t1, f)
        evening_astro = None
        morning_astro = None
        for t, e in zip(times, events):
            dt_tst = t.astimezone(tz_tst)
            hour = dt_tst.hour + dt_tst.minute / 60
            if e == 0 and hour > 15:
                evening_astro = dt_tst
            elif e == 1 and hour < 12:
                morning_astro = dt_tst
        return {
            "evening_astro_twilight": evening_astro,
            "morning_astro_twilight": morning_astro,
        }
    except Exception as e:
        print(f"[薄暮計算錯誤] {e}", flush=True)
        return {"evening_astro_twilight": None, "morning_astro_twilight": None}

# ── 月出月落計算 ───────────────────────────────────────────────

def get_moon_rise_set(observer, query_date):
    tz_tst = timezone(timedelta(hours=8))
    try:
        t0 = ts.from_datetime(datetime(query_date.year, query_date.month, query_date.day,
                                        4, 0, tzinfo=timezone.utc))
        t1 = ts.from_datetime(datetime(query_date.year, query_date.month, query_date.day,
                                        4, 0, tzinfo=timezone.utc) + timedelta(days=1))
        f = almanac.risings_and_settings(eph, eph['moon'], observer)
        times, events = almanac.find_discrete(t0, t1, f)
        moonrise = moonset = None
        moonrise_az = moonset_az = None
        for t, e in zip(times, events):
            dt_tst = t.astimezone(tz_tst)
            astrometric = (eph['earth'] + observer).at(t).observe(eph['moon']).apparent()
            _, az, _ = astrometric.altaz()
            az_deg = round(az.degrees, 1)
            if e == 1 and moonrise is None:
                moonrise    = dt_tst
                moonrise_az = az_deg
            elif e == 0 and moonset is None:
                moonset    = dt_tst
                moonset_az = az_deg
        moon_above_all_night = (moonrise is None and moonset is None and len(times) == 0)
        astrometric_t0 = (eph['earth'] + observer).at(t0).observe(eph['moon']).apparent()
        alt_t0, _, _ = astrometric_t0.altaz()
        moon_above_all_night = moon_above_all_night and alt_t0.degrees > 0
        moon_below_all_night = (moonrise is None and moonset is None and alt_t0.degrees <= 0)
        return {
            "moonrise": moonrise, "moonset": moonset,
            "moonrise_az": moonrise_az, "moonset_az": moonset_az,
            "moon_above_all_night": moon_above_all_night,
            "moon_below_all_night": moon_below_all_night,
        }
    except Exception as e:
        print(f"[月出月落計算錯誤] {e}", flush=True)
        return {
            "moonrise": None, "moonset": None,
            "moonrise_az": None, "moonset_az": None,
            "moon_above_all_night": False, "moon_below_all_night": False,
        }

# ── 有效暗空窗口計算 ───────────────────────────────────────────

def compute_dark_sky_window(twilight_info, moon_info_day):
    ev  = twilight_info.get("evening_astro_twilight")
    mo  = twilight_info.get("morning_astro_twilight")
    if not ev or not mo:
        return [], "⚠️ 薄暮時刻計算失敗"
    moonrise = moon_info_day.get("moonrise")
    moonset  = moon_info_day.get("moonset")
    above    = moon_info_day.get("moon_above_all_night", False)
    below    = moon_info_day.get("moon_below_all_night", False)
    if above:
        return [], "🌕 整夜有月光，無有效暗空窗口"
    if below or (moonrise is None and moonset is None):
        duration = (mo - ev).seconds // 60
        h, m = divmod(duration, 60)
        desc = (f"🌑 全夜無月光\n"
                f"  暗空窗口：{ev.strftime('%H:%M')} ～ {mo.strftime('%H:%M')} TST"
                f"（共 {h}h{m:02d}m）")
        return [(ev, mo)], desc
    windows = []
    desc_parts = []
    moon_up_segments = []
    if moonrise and moonset:
        if moonrise < moonset:
            moon_up_segments.append((moonrise, moonset))
        else:
            moon_up_segments.append((ev, moonset))
            moon_up_segments.append((moonrise, mo))
    elif moonrise and not moonset:
        moon_up_segments.append((moonrise, mo))
    elif moonset and not moonrise:
        moon_up_segments.append((ev, moonset))
    dark_intervals = [(ev, mo)]
    for seg_start, seg_end in moon_up_segments:
        new_intervals = []
        for ds, de in dark_intervals:
            if seg_end <= ds or seg_start >= de:
                new_intervals.append((ds, de))
                continue
            if ds < seg_start:
                new_intervals.append((ds, seg_start))
            if seg_end < de:
                new_intervals.append((seg_end, de))
        dark_intervals = new_intervals
    MIN_WINDOW_MIN = 30
    for ds, de in dark_intervals:
        dur = (de - ds).seconds // 60
        if dur >= MIN_WINDOW_MIN:
            windows.append((ds, de))
            h, m = divmod(dur, 60)
            desc_parts.append(f"  {ds.strftime('%H:%M')} ～ {de.strftime('%H:%M')} TST（{h}h{m:02d}m）")
    if not windows:
        moon_str = ""
        if moonrise: moon_str += f"月出 {moonrise.strftime('%H:%M')}"
        if moonset:  moon_str += f"{'，' if moon_str else ''}月落 {moonset.strftime('%H:%M')}"
        return [], f"⚠️ 月光干擾嚴重，無 30 分鐘以上暗空窗口\n  （{moon_str}）"
    total_min = sum((de - ds).seconds // 60 for ds, de in windows)
    h_total, m_total = divmod(total_min, 60)
    header = f"✅ 有效暗空窗口（共 {h_total}h{m_total:02d}m）："
    desc = header + "\n" + "\n".join(desc_parts)
    return windows, desc

def get_milky_way_composition(observer, query_date, dark_windows):
    if not dark_windows:
        return None
    mw_star = Star(ra_hours=MILKY_WAY_CORE["ra_hours"],
                   dec_degrees=MILKY_WAY_CORE["dec_degrees"])
    best = None
    best_alt = -999
    for (win_start, win_end) in dark_windows:
        current = win_start
        while current <= win_end:
            t_utc = current.astimezone(timezone.utc)
            t = ts.from_datetime(t_utc)
            astrometric = (eph['earth'] + observer).at(t).observe(mw_star).apparent()
            alt, az, _  = astrometric.altaz()
            if alt.degrees > best_alt:
                best_alt = alt.degrees
                best = {
                    "datetime_tst": current,
                    "alt_deg":       round(alt.degrees, 1),
                    "az_deg":        round(az.degrees, 1),
                }
            current += timedelta(minutes=10)
    if best is None or best["alt_deg"] < 10:
        return None
    t_best = ts.from_datetime(best["datetime_tst"].astimezone(timezone.utc))
    moon_astrometric = (eph['earth'] + observer).at(t_best).observe(eph['moon']).apparent()
    moon_alt, moon_az, _ = moon_astrometric.altaz()
    mw_az   = best["az_deg"]
    moon_az_deg = round(moon_az.degrees, 1)
    angle_diff = abs(mw_az - moon_az_deg)
    if angle_diff > 180:
        angle_diff = 360 - angle_diff
    angle_diff = round(angle_diff, 1)
    if moon_alt.degrees < 0:
        moon_interference = "無干擾（月亮在地平線下）"
    elif angle_diff >= 60:
        moon_interference = f"低干擾（月亮在 {az_to_direction(moon_az_deg)} {moon_az_deg}°，相距 {angle_diff}°）"
    elif angle_diff >= 30:
        moon_interference = f"中等干擾（月亮在 {az_to_direction(moon_az_deg)} {moon_az_deg}°，相距 {angle_diff}°）"
    else:
        moon_interference = f"⚠️ 嚴重干擾（月亮與銀河僅相距 {angle_diff}°，構圖困難）"
    mw_direction = az_to_direction(mw_az)
    composition_tip = (
        f"面向 {mw_direction}（{mw_az}°）拍攝銀河核心\n"
        f"  仰角約 {best['alt_deg']}°，建議廣角鏡下壓地景"
    )
    if angle_diff < 30 and moon_alt.degrees > 0:
        composition_tip += "\n  ⚠️ 月亮方向與銀河重疊，可等月落後再拍或嘗試縮小構圖迴避"
    return {
        "best_datetime":     best["datetime_tst"],
        "mw_alt_deg":        best["alt_deg"],
        "mw_az_deg":         mw_az,
        "mw_direction":      mw_direction,
        "moon_az_deg":       moon_az_deg,
        "moon_alt_deg":      round(moon_alt.degrees, 1),
        "moon_direction":    az_to_direction(moon_az_deg),
        "angle_diff":        angle_diff,
        "moon_interference": moon_interference,
        "composition_tip":   composition_tip,
    }

# ── 原有計算邏輯 ───────────────────────────────────────────────

def _best_target_windows_at_times(observer_vector, targets, scan_times, in_dark_window):
    if not targets or not scan_times:
        return [None] * len(targets)
    stars = Star(
        ra_hours=[target["ra_hours"] for target in targets],
        dec_degrees=[target["dec_degrees"] for target in targets],
    )
    best_windows = [None] * len(targets)
    # 每個目標當晚的可見區間（首次／最後一個符合仰角限制的掃描時間）
    visible_spans = [[None, None] for _ in targets]
    for dt_tst in scan_times:
        t = ts.from_datetime(dt_tst.astimezone(timezone.utc))
        apparent = observer_vector.at(t).observe(stars).apparent()
        altitudes, azimuths, _ = apparent.altaz()
        for index, target in enumerate(targets):
            alt_deg = float(altitudes.degrees[index])
            if target.get("min_alt", 10) <= alt_deg <= target.get("max_alt", 80):
                span = visible_spans[index]
                if span[0] is None or dt_tst < span[0]:
                    span[0] = dt_tst
                if span[1] is None or dt_tst > span[1]:
                    span[1] = dt_tst
                best = best_windows[index]
                if best is None or alt_deg > best["alt_deg"]:
                    best_windows[index] = {
                        "target_name": target["name"],
                        "target_type": target["type"],
                        "datetime_tst": dt_tst,
                        "alt_deg": round(alt_deg, 1),
                        "az_deg": round(float(azimuths.degrees[index]), 1),
                        "in_dark_window": in_dark_window,
                    }
    for index, best in enumerate(best_windows):
        if best is not None:
            best["window_start_tst"] = visible_spans[index][0]
            best["window_end_tst"] = visible_spans[index][1]
    return best_windows


def compute_target_windows_for_targets(observer, targets, query_dates, dark_windows_by_date=None):
    if not targets:
        return []
    observer_vector = eph["earth"] + observer
    windows_by_target = [[] for _ in targets]
    tz_tst = timezone(timedelta(hours=8))
    for d in query_dates:
        day_windows = (
            dark_windows_by_date[d]
            if dark_windows_by_date and d in dark_windows_by_date
            else None
        )
        scan_times = []
        for win_start, win_end in day_windows or []:
            current = win_start
            while current <= win_end:
                scan_times.append(current)
                current += timedelta(minutes=10)
        best_for_day = _best_target_windows_at_times(
            observer_vector,
            targets,
            scan_times,
            True,
        )
        missing_indices = [
            index for index, best in enumerate(best_for_day) if best is None
        ]
        if missing_indices:
            fallback_times = [
                datetime(d.year, d.month, d.day, 18, 0, tzinfo=tz_tst)
                + timedelta(minutes=minutes)
                for minutes in range(0, 12 * 60, 10)
            ]
            missing_targets = [targets[index] for index in missing_indices]
            fallback_best = _best_target_windows_at_times(
                observer_vector,
                missing_targets,
                fallback_times,
                False,
            )
            for missing_index, best in zip(missing_indices, fallback_best):
                best_for_day[missing_index] = best
        for index, best in enumerate(best_for_day):
            if best:
                windows_by_target[index].append(best)
    return [window for target_windows in windows_by_target for window in target_windows]


def compute_target_windows(observer, target, query_dates, dark_windows_by_date=None):
    return compute_target_windows_for_targets(
        observer,
        [target],
        query_dates,
        dark_windows_by_date,
    )


def get_moon_info(observer, query_dates):
    results = []
    for d in query_dates:
        t0 = ts.utc(d.year, d.month, d.day, 11)
        mp = almanac.moon_phase(eph, t0)
        moon_rs = get_moon_rise_set(observer, d)
        twilight = get_astronomical_twilight(observer, d)
        dark_wins, dark_desc = compute_dark_sky_window(twilight, moon_rs)
        results.append({
            "date":               d,
            "moon_phase_pct":     round(float(mp.degrees) / 360.0 * 100, 1),
            "moon_phase_desc":    get_moon_phase_emoji(float(mp.degrees) / 360.0),
            "moonrise":           moon_rs["moonrise"],
            "moonset":            moon_rs["moonset"],
            "moonrise_az":        moon_rs["moonrise_az"],
            "moonset_az":         moon_rs["moonset_az"],
            "moon_above_all_night": moon_rs["moon_above_all_night"],
            "moon_below_all_night": moon_rs["moon_below_all_night"],
            "evening_twilight":   twilight["evening_astro_twilight"],
            "morning_twilight":   twilight["morning_astro_twilight"],
            "dark_windows":       dark_wins,
            "dark_window_desc":   dark_desc,
        })
    return results
