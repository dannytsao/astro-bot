# 出勤信心指數（CCI）：純 Python 計算，不依賴 LLM。
# profile: default | meteor | moonscape | lunar_eclipse | comet_layer1
import math
from datetime import datetime, timedelta, timezone

TZ_TST = timezone(timedelta(hours=8))


def resolve_observation_interval(cci_profile, moon_day, windows_for_date):
    """依題材決定當晚 Go/No-Go 判斷所用的觀測時間區間。

    回傳 (start_dt, end_dt, source)；無法解析時回傳 (None, None, None)，
    呼叫端 fallback 整夜平均（不可猜測原則：解析不到就明講用整夜平均）。
    source: "target_windows" | "dark_windows" | "moon_up"
    """
    d = moon_day.get("date")
    dark_wins = moon_day.get("dark_windows") or []
    night_start = datetime(d.year, d.month, d.day, 18, 0, tzinfo=TZ_TST) if d else None
    night_end = night_start + timedelta(hours=12) if night_start else None

    if cci_profile in ("moonscape", "lunar_eclipse"):
        # 月景／月蝕：月亮在天上的區間 ∩ 夜間（18:00–06:00）
        if night_start is None or moon_day.get("moon_below_all_night"):
            return None, None, None
        if moon_day.get("moon_above_all_night"):
            return night_start, night_end, "moon_up"
        rise, moonset = moon_day.get("moonrise"), moon_day.get("moonset")
        if rise and moonset:
            segs = [(rise, moonset)] if rise < moonset else \
                   [(night_start, moonset), (rise, night_end)]
        elif rise:
            segs = [(rise, night_end)]
        elif moonset:
            segs = [(night_start, moonset)]
        else:
            return None, None, None
        best = None
        for s, e in segs:
            s2, e2 = max(s, night_start), min(e, night_end)
            if e2 > s2 and (best is None or (e2 - s2) > (best[1] - best[0])):
                best = (s2, e2)
        if best:
            return best[0], best[1], "moon_up"
        return None, None, None

    if cci_profile in ("meteor", "comet_layer1"):
        # 流星雨／彗星第一層：暗空窗口邊界
        if dark_wins:
            return dark_wins[0][0], dark_wins[-1][1], "dark_windows"
        return None, None, None

    # default／深空：當晚目標可見區間聯集邊界；無目標窗口時退回暗空窗口
    starts = [w["window_start_tst"] for w in windows_for_date if w.get("window_start_tst")]
    ends = [w["window_end_tst"] for w in windows_for_date if w.get("window_end_tst")]
    if starts and ends:
        return min(starts), max(ends), "target_windows"
    if dark_wins:
        return dark_wins[0][0], dark_wins[-1][1], "dark_windows"
    return None, None, None


# ── 出勤信心指數（CCI） ────────────────────────────────────────

def _moon_illumination(moon_phase_pct):
    """從 moon_phase_pct（0–100）估算月面照度比例（0–1）。
    moon_phase_pct=0/100 → 新月 (~0%)；moon_phase_pct=50 → 滿月 (~100%)。
    """
    import math
    phase_angle_deg = moon_phase_pct / 100.0 * 360.0
    return (1.0 - math.cos(math.radians(phase_angle_deg))) / 2.0

def compute_cci_for_date(weather_day, moon_info_day, seeing_day, windows_for_date, wind_profile="milky_way",
                         cci_profile="default", extra_data=None):
    """每晚出勤信心指數（0–100）。純 Python 計算，不依賴 LLM。
    cci_profile: "default" | "meteor" | "moonscape" | "lunar_eclipse" | "comet_layer1"
    extra_data:  {"showers": [...]}  供 meteor profile 讀取 ZHR
    """
    if extra_data is None:
        extra_data = {}
    breakdown = {}
    completeness_flags = []
    profile_notes = []  # 附加說明給 LLM

    # ── 各 profile 權重定義 ───────────────────────────────────────
    if cci_profile == "meteor":
        # 流星雨：雲量最重要；月亮亮度是關鍵負因子（放入 target）；視寧度次要；無需追蹤故風速寬鬆
        W = {"cloud":0.35,"dark_window":0.08,"seeing":0.05,"transparency":0.10,"target":0.27,"dew":0.05,"wind":0.10}
    elif cci_profile == "moonscape":
        # 月景：月光是主角；暗空窗口反轉；透明度更重要；視寧度次要
        W = {"cloud":0.35,"dark_window":0.05,"seeing":0.08,"transparency":0.15,"target":0.27,"dew":0.05,"wind":0.05}
    elif cci_profile == "lunar_eclipse":
        # 月蝕：不需要暗空；透明度最關鍵；月亮仰角（月在天上）是目標可見性
        W = {"cloud":0.35,"dark_window":0.03,"seeing":0.10,"transparency":0.17,"target":0.25,"dew":0.05,"wind":0.05}
    elif cci_profile == "comet_layer1":
        # 彗星第一層：同深空，但 target 固定中性（無準確座標）
        W = {"cloud":0.30,"dark_window":0.22,"seeing":0.13,"transparency":0.08,"target":0.10,"dew":0.05,"wind":0.12}
    else:  # default
        W = {"cloud":0.30,"dark_window":0.22,"seeing":0.13,"transparency":0.08,"target":0.10,"dew":0.05,"wind":0.12}

    # 1. 雲量（以觀測區間聚合為準；區間無法解析時 fallback 整夜平均）
    weather_ok = weather_day.get("data_status") == "ok"
    cloud = weather_day.get("cloud_cover", -1)
    interval_based = weather_day.get("aggregation") == "target_window"
    if interval_based:
        ws, we = weather_day.get("window_start"), weather_day.get("window_end")
        window_label = f"{ws.strftime('%H:%M')}–{we.strftime('%H:%M')} 觀測區間" if ws and we else "觀測區間"
    else:
        window_label = "整夜平均"
    if not weather_ok or cloud < 0:
        cloud_score, cloud_raw = 0, "氣象資料缺失"
        completeness_flags.append("weather_missing")
    elif cloud <= 20:  cloud_score = 100
    elif cloud <= 40:  cloud_score = 80
    elif cloud <= 60:  cloud_score = 40
    elif cloud <= 80:  cloud_score = 15
    else:              cloud_score = 0
    if weather_ok and cloud >= 0:
        cloud_max = weather_day.get("cloud_cover_max", -1)
        cloud_raw = f"雲量 {cloud}%（{window_label}）"
        if cloud_max >= 0 and interval_based:
            cloud_raw = f"雲量 {cloud}%（{window_label}平均，峰值 {cloud_max}%）"
        if cloud_max >= 60 and (cloud_max - cloud) >= 30:
            profile_notes.append(
                f"⚠️ 觀測區間內雲量起伏大（平均 {cloud}%、峰值 {cloud_max}%），"
                f"部分時段可能被雲層蓋掉，建議預留等雲空檔"
            )
        if not interval_based:
            profile_notes.append("ℹ️ 氣象以整夜平均計算（觀測區間無法解析或逐時資料缺失）")
    breakdown["cloud"] = {"score": cloud_score, "raw": cloud_raw, "weight": W["cloud"]}

    # 2. 有效暗空窗口 / 月光亮度（依 profile 調整語意）
    moon_pct  = moon_info_day.get("moon_phase_pct", 50)
    moon_illum = _moon_illumination(moon_pct)  # 0=新月, 1=滿月
    dark_wins = moon_info_day.get("dark_windows", [])

    if cci_profile == "moonscape":
        # 月景：月光是主角，illumination 越高越好
        if moon_illum >= 0.75:   dark_score, dark_raw = 100, f"月面照度 {round(moon_illum*100)}%（滿月期，絕佳月景）"
        elif moon_illum >= 0.50: dark_score, dark_raw = 80,  f"月面照度 {round(moon_illum*100)}%（盈月期，良好）"
        elif moon_illum >= 0.25: dark_score, dark_raw = 45,  f"月面照度 {round(moon_illum*100)}%（半月期，尚可）"
        else:                    dark_score, dark_raw = 10,  f"月面照度 {round(moon_illum*100)}%（新月期，月景不佳）"
        profile_notes.append("⚠️ 月景題材：月光強度為加分項，分析以月亮亮度為主，無暗空需求")
    elif cci_profile == "lunar_eclipse":
        # 月蝕：月亮需在天上（不需要暗空）；月蝕時月亮仰角是可見性
        moon_above = moon_info_day.get("moon_above_all_night", False)
        moon_below = moon_info_day.get("moon_below_all_night", False)
        if moon_above:
            dark_score, dark_raw = 90, "月亮整夜在天，月蝕可見"
        elif moon_below:
            dark_score, dark_raw = 0,  "月亮整夜低於地平，月蝕無法觀測"
        elif dark_wins:
            total_min = sum((de - ds).seconds // 60 for (ds, de) in dark_wins)
            moon_up_min = max(0, 480 - total_min)  # 估算月亮在天時間
            dark_score = min(90, round(moon_up_min / 480 * 100))
            dark_raw = f"月亮部分時段可見（估計 {moon_up_min} 分鐘）"
        else:
            dark_score, dark_raw = 50, "月亮出沒情況不明（中性）"
        profile_notes.append("⚠️ 月蝕題材：本系統不計算月蝕時間，天況評估僅供參考；月蝕時間請查詢台北天文館或 Stellarium")
    elif cci_profile in ("meteor", "default", "comet_layer1"):
        # 深空/流星雨：暗空窗口長度評分（原始邏輯）
        if not dark_wins:
            dark_score, dark_raw = 0, "無有效暗空窗口"
        else:
            total_min = sum((de - ds).seconds // 60 for (ds, de) in dark_wins)
            h, m_min = divmod(total_min, 60)
            dark_raw = f"暗空 {h}h{m_min:02d}m" if total_min > 0 else "暗空窗口極短"
            if total_min >= 300:   dark_score = 100
            elif total_min >= 240: dark_score = 90
            elif total_min >= 120: dark_score = 65
            elif total_min >= 60:  dark_score = 35
            elif total_min >= 30:  dark_score = 15
            else:                  dark_score = 5
    else:
        dark_score, dark_raw = 50, "暗空窗口資料不明（中性）"
    breakdown["dark_window"] = {"score": dark_score, "raw": dark_raw, "weight": W["dark_window"]}

    # 3. 視寧度  7Timer: 1=最佳, 8=最差
    seeing_ok = seeing_day.get("data_status") == "ok"
    seeing = seeing_day.get("seeing", -1)
    if not seeing_ok or seeing <= 0:
        seeing_score, seeing_raw = 50, "視寧度資料缺失"
        completeness_flags.append("seeing_missing")
    elif seeing <= 2: seeing_score, seeing_raw = 100, f"視寧度 {seeing}/8（優）"
    elif seeing <= 3: seeing_score, seeing_raw = 75,  f"視寧度 {seeing}/8（良）"
    elif seeing <= 4: seeing_score, seeing_raw = 50,  f"視寧度 {seeing}/8（中）"
    elif seeing <= 5: seeing_score, seeing_raw = 25,  f"視寧度 {seeing}/8（差）"
    else:             seeing_score, seeing_raw = 0,   f"視寧度 {seeing}/8（很差）"
    breakdown["seeing"] = {"score": seeing_score, "raw": seeing_raw, "weight": W["seeing"]}

    # 4. 大氣透明度  7Timer: 1=最佳, 8=最差
    transp = seeing_day.get("transparency", -1)
    if not seeing_ok or transp <= 0:
        transp_score, transp_raw = 50, "透明度資料缺失"
    elif transp <= 2: transp_score, transp_raw = 100, f"透明度 {transp}/8（優）"
    elif transp <= 3: transp_score, transp_raw = 75,  f"透明度 {transp}/8（良）"
    elif transp <= 4: transp_score, transp_raw = 50,  f"透明度 {transp}/8（中）"
    elif transp <= 5: transp_score, transp_raw = 25,  f"透明度 {transp}/8（差）"
    else:             transp_score, transp_raw = 0,   f"透明度 {transp}/8（很差）"
    breakdown["transparency"] = {"score": transp_score, "raw": transp_raw, "weight": W["transparency"]}

    # 5. 目標天體可見性（依 profile 調整語意）
    if cci_profile == "meteor":
        # 流星雨：月面照度是最大干擾因子；ZHR 決定值得程度
        if moon_illum <= 0.10:   target_score = 100
        elif moon_illum <= 0.25: target_score = 80
        elif moon_illum <= 0.50: target_score = 55
        elif moon_illum <= 0.75: target_score = 25
        else:                    target_score = 8
        showers = extra_data.get("showers", [])
        if showers:
            peak = max(showers, key=lambda s: s["zenithal_hourly_rate"])
            zhr  = peak["zenithal_hourly_rate"]
            days = abs(peak.get("days_to_peak", 3))
            zhr_label = f"ZHR {zhr}"
            if zhr >= 100 and days == 0:   target_score = min(100, target_score + 20)
            elif zhr >= 100 and days <= 1: target_score = min(100, target_score + 10)
            elif zhr >= 50:                target_score = min(100, target_score + 5)
            target_raw = f"月面照度 {round(moon_illum*100)}%（干擾）・{zhr_label}・距極大 {days:+d}天"
        else:
            target_raw = f"月面照度 {round(moon_illum*100)}%（干擾）・無已知極大期"
        profile_notes.append("⚠️ 流星雨題材：目標可見性以月面照度為主要干擾因子；ZHR 為靜態歷史值，實際流量可能有差異")
    elif cci_profile == "moonscape":
        # 月景：月光強度即是目標可見性（同 dark_window 邏輯但獨立計分）
        if moon_illum >= 0.75:   target_score, target_raw = 100, f"月面照度 {round(moon_illum*100)}%（滿月，月景最強）"
        elif moon_illum >= 0.50: target_score, target_raw = 80,  f"月面照度 {round(moon_illum*100)}%（盈月）"
        elif moon_illum >= 0.25: target_score, target_raw = 45,  f"月面照度 {round(moon_illum*100)}%（半月）"
        else:                    target_score, target_raw = 10,  f"月面照度 {round(moon_illum*100)}%（新月期，月景不適合）"
    elif cci_profile == "lunar_eclipse":
        # 月蝕：月亮在天上即可；月蝕時間另行查詢
        moon_above = moon_info_day.get("moon_above_all_night", False)
        moon_below = moon_info_day.get("moon_below_all_night", False)
        if moon_above:
            target_score, target_raw = 90, "月亮整夜可觀測，天況條件充足"
        elif moon_below:
            target_score, target_raw = 0,  "月亮整夜低於地平，月蝕無法觀測"
        else:
            target_score, target_raw = 60, "月亮部分時段可見"
    elif cci_profile == "comet_layer1":
        # 彗星第一層：無準確位置，給中性分數
        target_score, target_raw = 50, "彗星位置資料缺失（靜態座標），以中性值計算"
        profile_notes.append("⚠️ 彗星題材（第一層）：本評估僅提供天況 CCI，不含彗星方位角；位置請自行查詢 Stellarium 或 JPL Horizons")
    else:
        # default：原始暗空窗口邏輯
        in_dark = any(w.get("in_dark_window", False) for w in windows_for_date)
        has_win  = len(windows_for_date) > 0
        if in_dark:   target_score, target_raw = 100, "暗空窗口內可見"
        elif has_win: target_score, target_raw = 50,  "僅月光時段可見"
        else:         target_score, target_raw = 0,   "目標不可見"
    breakdown["target"] = {"score": target_score, "raw": target_raw, "weight": W["target"]}

    # 6. 結露 / 起霧風險（優先用區間內最差小時的 T−Td，物理上比平均值準確）
    if not weather_ok:
        dew_score, dew_raw = 80, "結露資料缺失"
    else:
        temp   = weather_day.get("temp_c")
        dew_pt = weather_day.get("dew_point_c")
        min_diff = weather_day.get("min_temp_dew_diff")
        if min_diff is not None and min_diff > -900:
            diff, diff_label = min_diff, "最差小時 "
        elif temp is None or dew_pt is None:
            diff, diff_label = None, ""
        else:
            diff, diff_label = temp - dew_pt, ""
        if diff is None:
            dew_score, dew_raw = 80, "結露資料缺失"
        elif diff >= 3.0: dew_score, dew_raw = 100, f"{diff_label}T−Td={diff:.1f}°C（安全）"
        elif diff >= 1.5: dew_score, dew_raw = 50,  f"{diff_label}T−Td={diff:.1f}°C（注意）"
        else:             dew_score, dew_raw = 0,   f"{diff_label}T−Td={diff:.1f}°C（高風險）"
    breakdown["dew"] = {"score": dew_score, "raw": dew_raw, "weight": W["dew"]}

    # 7. 風速穩定性
    # 流星雨（廣角、無追蹤）容忍 4 級；深空 2 級；其他 3 級
    if cci_profile == "meteor":
        wind_limit = 4
    elif wind_profile == "deep_sky":
        wind_limit = 2
    else:
        wind_limit = 3
    wind_kmh = weather_day.get("wind_speed_kmh", -1)
    wind_bft = weather_day.get("wind_beaufort", -1)
    if not weather_ok or wind_kmh < 0 or wind_bft < 0:
        wind_score, wind_raw = 50, "風速資料缺失"
        completeness_flags.append("wind_missing")
    else:
        wind_raw = f"最大風速 {wind_kmh} km/h（{wind_bft}級風，上限 {wind_limit}級）"
        if wind_bft <= max(wind_limit - 1, 0):
            wind_score = 100
        elif wind_bft == wind_limit:
            wind_score = 65
        else:
            wind_score = 0
    breakdown["wind"] = {"score": wind_score, "raw": wind_raw, "weight": W["wind"]}

    score = round(
        cloud_score  * W["cloud"]        +
        dark_score   * W["dark_window"]  +
        seeing_score * W["seeing"]       +
        transp_score * W["transparency"] +
        target_score * W["target"]       +
        dew_score    * W["dew"]          +
        wind_score   * W["wind"]
    )

    if "weather_missing" in completeness_flags and "seeing_missing" in completeness_flags:
        completeness = "astronomy_only"
    elif completeness_flags:
        completeness = "partial"
    else:
        completeness = "full"

    if score >= 80:   label = "✅ 強烈推薦出勤"
    elif score >= 60: label = "🟢 值得出勤"
    elif score >= 40: label = "⚠️ 謹慎考慮"
    elif score >= 20: label = "🟠 不建議"
    else:             label = "❌ 不值得出勤"

    return {
        "score":        score,
        "label":        label,
        "breakdown":    breakdown,
        "completeness": completeness,
        "cci_profile":  cci_profile,
        "profile_notes": profile_notes,
    }
