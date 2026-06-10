import math, requests, json, re, asyncio, logging, os

from datetime import datetime, timedelta, timezone, date
from skyfield.api import Star, wgs84, load
from skyfield import almanac
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes)
from telegram.request import HTTPXRequest
import anthropic
import gspread
from google.oauth2.service_account import Credentials

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS_JSON")
SPREADSHEET_ID    = "1fYmucd6mB8nlzblJsl44QDerUjx-1cI3Ll9EgO_KPnU"

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
logging.basicConfig(level=logging.ERROR)

# ── Google Sheets ──────────────────────────────────────────────

def init_sheets():
    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    scopes = ["https://spreadsheets.google.com/feeds",
              "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    try:
        ws_query = sh.worksheet("查詢記錄")
    except gspread.WorksheetNotFound:
        ws_query = sh.add_worksheet("查詢記錄", rows=1000, cols=10)
        ws_query.append_row(["時間","用戶名","用戶ID","查詢內容","地點","日期區間","標的","類型"])
    try:
        ws_feedback = sh.worksheet("用戶反饋")
    except gspread.WorksheetNotFound:
        ws_feedback = sh.add_worksheet("用戶反饋", rows=1000, cols=8)
        ws_feedback.append_row(["時間","用戶名","用戶ID","查詢內容","評分","類型","許願內容"])
    return ws_query, ws_feedback

try:
    ws_query, ws_feedback = init_sheets()
    print("✅ Google Sheets 連線成功", flush=True)
except Exception as e:
    print(f"⚠️ Google Sheets 連線失敗：{e}", flush=True)
    ws_query = ws_feedback = None

def log_query(username, user_id, query, intent):
    if not ws_query:
        return
    try:
        ws_query.append_row([
            datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
            username, str(user_id), query,
            intent.get("location_name",""),
            f"{intent.get('date_start','')} ～ {intent.get('date_end','')}",
            ", ".join(intent.get("targets",[])) or "開放探索",
            "A" if intent.get("query_type")=="A" else "B",
        ])
    except Exception as e:
        print(f"[Sheets 錯誤] {e}", flush=True)

def log_feedback(username, user_id, query, rating, feedback_type, wish=""):
    if not ws_feedback:
        return
    try:
        ws_feedback.append_row([
            datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
            username, str(user_id), query, rating, feedback_type, wish,
        ])
    except Exception as e:
        print(f"[Sheets 錯誤] {e}", flush=True)

# ── Skyfield 初始化 ────────────────────────────────────────────

ts  = load.timescale()
eph = load("de421.bsp")

# ── 標的資料庫 ────────────────────────────────────────────────

TARGET_LIBRARY = [
    {"name":"銀河核心",          "ra_hours":17.761, "dec_degrees":-29.0,  "type":"galaxy",        "min_alt":15,"max_alt":60},
    {"name":"獵戶座",            "ra_hours":84.05/15,"dec_degrees":-1.20,  "type":"constellation", "min_alt":10,"max_alt":50},
    {"name":"天蠍座",            "ra_hours":16.49,  "dec_degrees":-26.43, "type":"constellation", "min_alt":10,"max_alt":50},
    {"name":"獅子座",            "ra_hours":10.14,  "dec_degrees":11.97,  "type":"constellation", "min_alt":10,"max_alt":70},
    {"name":"仙女座",            "ra_hours":0.712,  "dec_degrees":41.27,  "type":"constellation", "min_alt":10,"max_alt":80},
    {"name":"南十字座",          "ra_hours":12.45,  "dec_degrees":-60.0,  "type":"constellation", "min_alt":5, "max_alt":30},
    {"name":"獵戶座大星雲 M42",  "ra_hours":5.588,  "dec_degrees":-5.39,  "type":"nebula",        "min_alt":10,"max_alt":60},
    {"name":"玫瑰星雲 NGC2244",  "ra_hours":6.532,  "dec_degrees":4.95,   "type":"nebula",        "min_alt":10,"max_alt":60},
    {"name":"礁湖星雲 M8",       "ra_hours":18.063, "dec_degrees":-24.38, "type":"nebula",        "min_alt":10,"max_alt":50},
    {"name":"鷹星雲 M16",        "ra_hours":18.313, "dec_degrees":-13.79, "type":"nebula",        "min_alt":10,"max_alt":60},
    {"name":"猴頭星雲 NGC2174",  "ra_hours":6.092,  "dec_degrees":20.30,  "type":"nebula",        "min_alt":10,"max_alt":70},
    {"name":"昆蟲星雲 NGC6302",  "ra_hours":17.225, "dec_degrees":-37.10, "type":"nebula",        "min_alt":8, "max_alt":40},
    {"name":"仙女座星系 M31",    "ra_hours":0.712,  "dec_degrees":41.27,  "type":"nebula",        "min_alt":10,"max_alt":80},
    {"name":"紫金山-ATLAS彗星",  "ra_hours":3.20,   "dec_degrees":15.0,   "type":"comet",         "min_alt":10,"max_alt":60},
]

METEOR_SHOWERS = [
    {"name":"象限儀座流星雨","peak_month":1,  "peak_day":4,  "zenithal_hourly_rate":120},
    {"name":"英仙座流星雨", "peak_month":8,  "peak_day":12, "zenithal_hourly_rate":100},
    {"name":"雙子座流星雨", "peak_month":12, "peak_day":14, "zenithal_hourly_rate":150},
    {"name":"獅子座流星雨", "peak_month":11, "peak_day":17, "zenithal_hourly_rate":15},
    {"name":"天琴座流星雨", "peak_month":4,  "peak_day":22, "zenithal_hourly_rate":18},
]

# ── 輔助函式 ──────────────────────────────────────────────────

def az_to_direction(az_deg):
    """將方位角（度）轉換為八方向中文描述"""
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

# ── ★ 新功能：天文薄暮時刻計算 ─────────────────────────────────

def get_astronomical_twilight(observer, query_date):
    """
    計算某日的天文薄暮（太陽低於 -18°）開始與結束時刻（TST，UTC+8）。
    回傳 dict：
      evening_astro_twilight: 傍晚天文薄暮開始（暗空開始）
      morning_astro_twilight: 清晨天文薄暮開始（暗空結束）
    若計算失敗則回傳 None。
    """
    tz_tst = timezone(timedelta(hours=8))
    try:
        # 搜尋範圍：當日 12:00 TST 到隔日 12:00 TST（涵蓋整個夜晚）
        t0 = ts.from_datetime(datetime(query_date.year, query_date.month, query_date.day,
                                        4, 0, tzinfo=timezone.utc))   # 12:00 TST = 04:00 UTC
        t1 = ts.from_datetime(datetime(query_date.year, query_date.month, query_date.day,
                                        4, 0, tzinfo=timezone.utc) + timedelta(days=1))

        # almanac.dark_twilight_day 回傳 0=夜（暗空）,1=天文薄暮,2=航海薄暮,3=民用薄暮,4=白晝
        f = almanac.dark_twilight_day(eph, observer)
        times, events = almanac.find_discrete(t0, t1, f)

        evening_astro = None
        morning_astro = None

        for t, e in zip(times, events):
            dt_tst = t.astimezone(tz_tst)
            hour = dt_tst.hour + dt_tst.minute / 60
            # 傍晚：從非夜間→夜間（事件值從高→低，進入 dark=0）
            # 清晨：從夜間→非夜間（事件值從低→高，離開 dark=0）
            # almanac 回傳的是進入該狀態的時刻
            # 狀態 0 = 完全夜間（天文薄暮後）
            if e == 0 and hour > 15:   # 傍晚進入暗空（15:00後 = 19:xx TST 附近）
                evening_astro = dt_tst
            elif e == 1 and hour < 12: # 清晨離開暗空，進入天文薄暮
                morning_astro = dt_tst

        return {
            "evening_astro_twilight": evening_astro,
            "morning_astro_twilight": morning_astro,
        }
    except Exception as e:
        print(f"[薄暮計算錯誤] {e}", flush=True)
        return {"evening_astro_twilight": None, "morning_astro_twilight": None}


# ── ★ 新功能：月出月落計算 ──────────────────────────────────────

def get_moon_rise_set(observer, query_date):
    """
    計算某夜月出、月落時刻（TST，UTC+8）及其方位角。
    搜尋範圍：當日 12:00 TST ～ 隔日 12:00 TST。
    回傳 dict：
      moonrise:     月出時刻（datetime with tz）或 None
      moonset:      月落時刻（datetime with tz）或 None
      moonrise_az:  月出方位角（度）或 None
      moonset_az:   月落方位角（度）或 None
      moon_above_all_night: bool，是否整夜月亮都在地平線以上
      moon_below_all_night: bool，是否整夜月亮都在地平線以下
    """
    tz_tst = timezone(timedelta(hours=8))
    try:
        t0 = ts.from_datetime(datetime(query_date.year, query_date.month, query_date.day,
                                        4, 0, tzinfo=timezone.utc))   # 12:00 TST
        t1 = ts.from_datetime(datetime(query_date.year, query_date.month, query_date.day,
                                        4, 0, tzinfo=timezone.utc) + timedelta(days=1))

        f = almanac.risings_and_settings(eph, eph['moon'], observer)
        times, events = almanac.find_discrete(t0, t1, f)

        moonrise = moonset = None
        moonrise_az = moonset_az = None

        for t, e in zip(times, events):
            dt_tst = t.astimezone(tz_tst)
            # 計算月出/月落時的方位角
            astrometric = (eph['earth'] + observer).at(t).observe(eph['moon']).apparent()
            _, az, _ = astrometric.altaz()
            az_deg = round(az.degrees, 1)

            if e == 1 and moonrise is None:   # 1 = rising
                moonrise    = dt_tst
                moonrise_az = az_deg
            elif e == 0 and moonset is None:  # 0 = setting
                moonset    = dt_tst
                moonset_az = az_deg

        # 判斷是否整夜可見 / 整夜不見
        moon_above_all_night = (moonrise is None and moonset is None and len(times) == 0)
        # 進一步確認：在 t0 時刻月亮是否在地平線上
        astrometric_t0 = (eph['earth'] + observer).at(t0).observe(eph['moon']).apparent()
        alt_t0, _, _ = astrometric_t0.altaz()
        moon_above_all_night = moon_above_all_night and alt_t0.degrees > 0
        moon_below_all_night = (moonrise is None and moonset is None and alt_t0.degrees <= 0)

        return {
            "moonrise": moonrise,
            "moonset":  moonset,
            "moonrise_az": moonrise_az,
            "moonset_az":  moonset_az,
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


# ── ★ 新功能：有效暗空窗口計算 ─────────────────────────────────

def compute_dark_sky_window(twilight_info, moon_info_day):
    """
    結合天文薄暮與月出月落，計算真正無月光干擾的暗空窗口。
    
    邏輯：
      暗空基礎範圍 = [evening_astro_twilight, morning_astro_twilight]
      再從中排除月亮在地平線上的時段
    
    回傳 list of (start, end) tuples（TST datetime），可能有 0~2 個窗口。
    同時回傳文字描述。
    """
    ev  = twilight_info.get("evening_astro_twilight")
    mo  = twilight_info.get("morning_astro_twilight")

    if not ev or not mo:
        return [], "⚠️ 薄暮時刻計算失敗"

    moonrise = moon_info_day.get("moonrise")
    moonset  = moon_info_day.get("moonset")
    above    = moon_info_day.get("moon_above_all_night", False)
    below    = moon_info_day.get("moon_below_all_night", False)

    # 整夜月亮都在地平線上 → 無暗空
    if above:
        return [], "🌕 整夜有月光，無有效暗空窗口"

    # 整夜月亮都在地平線下 → 全段暗空
    if below or (moonrise is None and moonset is None):
        duration = (mo - ev).seconds // 60
        h, m = divmod(duration, 60)
        desc = (f"🌑 全夜無月光\n"
                f"  暗空窗口：{ev.strftime('%H:%M')} ～ {mo.strftime('%H:%M')} TST"
                f"（共 {h}h{m:02d}m）")
        return [(ev, mo)], desc

    windows = []
    desc_parts = []

    # 情況一：月落在暗空範圍內（月亮先升後落，月落後才有暗空）
    # 情況二：月升在暗空範圍內（暗空開始後月亮才升起，月升前有暗空）
    # 情況三：月亮跨越整個暗空（月落在暗空前升起，月升在暗空後）

    # 建立「有月光」時段（只考慮暗空範圍內的月亮時段）
    moon_up_segments = []

    if moonrise and moonset:
        # 月出在月落之前（正常情況）
        if moonrise < moonset:
            moon_up_segments.append((moonrise, moonset))
        else:
            # 月落在月出之前：表示月亮從昨日升起，今日先落後再升
            moon_up_segments.append((ev, moonset))       # 從暗空開始到月落
            moon_up_segments.append((moonrise, mo))      # 從月升到暗空結束
    elif moonrise and not moonset:
        # 只有月出，沒有月落（月升後整夜都在）
        moon_up_segments.append((moonrise, mo))
    elif moonset and not moonrise:
        # 只有月落，沒有月出（月亮從昨晚就在，今晨才落）
        moon_up_segments.append((ev, moonset))

    # 從基礎暗空範圍 [ev, mo] 中剔除有月光時段
    dark_intervals = [(ev, mo)]
    for seg_start, seg_end in moon_up_segments:
        new_intervals = []
        for ds, de in dark_intervals:
            # 無交集
            if seg_end <= ds or seg_start >= de:
                new_intervals.append((ds, de))
                continue
            # 剔除交集
            if ds < seg_start:
                new_intervals.append((ds, seg_start))
            if seg_end < de:
                new_intervals.append((seg_end, de))
        dark_intervals = new_intervals

    # 過濾掉太短的窗口（< 30 分鐘）
    MIN_WINDOW_MIN = 30
    for ds, de in dark_intervals:
        dur = (de - ds).seconds // 60
        if dur >= MIN_WINDOW_MIN:
            windows.append((ds, de))
            h, m = divmod(dur, 60)
            desc_parts.append(f"  {ds.strftime('%H:%M')} ～ {de.strftime('%H:%M')} TST（{h}h{m:02d}m）")

    if not windows:
        # 補充月出月落資訊
        moon_str = ""
        if moonrise: moon_str += f"月出 {moonrise.strftime('%H:%M')}"
        if moonset:  moon_str += f"{'，' if moon_str else ''}月落 {moonset.strftime('%H:%M')}"
        return [], f"⚠️ 月光干擾嚴重，無 30 分鐘以上暗空窗口\n  （{moon_str}）"

    total_min = sum((de - ds).seconds // 60 for ds, de in windows)
    h_total, m_total = divmod(total_min, 60)
    header = f"✅ 有效暗空窗口（共 {h_total}h{m_total:02d}m）："
    desc = header + "\n" + "\n".join(desc_parts)

    return windows, desc


# ── ★ 新功能：銀河核心方位角計算 ──────────────────────────────

MILKY_WAY_CORE = {"ra_hours": 17.761, "dec_degrees": -29.0}  # 銀河核心座標

def get_milky_way_composition(observer, query_date, dark_windows):
    """
    在有效暗空窗口內計算銀河核心的最佳構圖資訊：
    - 銀河核心仰角、方位角（最佳時刻）
    - 月亮方位角（同時刻）
    - 兩者的角距離
    - 建議構圖方向
    
    回傳 dict 或 None（若銀河整晚不可見）。
    """
    if not dark_windows:
        return None

    mw_star = Star(ra_hours=MILKY_WAY_CORE["ra_hours"],
                   dec_degrees=MILKY_WAY_CORE["dec_degrees"])
    tz_tst = timezone(timedelta(hours=8))

    best = None
    best_alt = -999

    # 在所有暗空窗口內，每 10 分鐘掃描一次，找仰角最高時刻
    for (win_start, win_end) in dark_windows:
        current = win_start
        while current <= win_end:
            # 轉為 UTC
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
        return None   # 銀河核心在暗空窗口內仰角不足

    # 同時刻計算月亮方位角
    t_best = ts.from_datetime(best["datetime_tst"].astimezone(timezone.utc))
    moon_astrometric = (eph['earth'] + observer).at(t_best).observe(eph['moon']).apparent()
    moon_alt, moon_az, _ = moon_astrometric.altaz()

    mw_az   = best["az_deg"]
    moon_az_deg = round(moon_az.degrees, 1)

    # 計算銀河與月亮的方位角差（0~180°）
    angle_diff = abs(mw_az - moon_az_deg)
    if angle_diff > 180:
        angle_diff = 360 - angle_diff
    angle_diff = round(angle_diff, 1)

    # 月亮干擾判斷
    if moon_alt.degrees < 0:
        moon_interference = "無干擾（月亮在地平線下）"
    elif angle_diff >= 60:
        moon_interference = f"低干擾（月亮在 {az_to_direction(moon_az_deg)} {moon_az_deg}°，相距 {angle_diff}°）"
    elif angle_diff >= 30:
        moon_interference = f"中等干擾（月亮在 {az_to_direction(moon_az_deg)} {moon_az_deg}°，相距 {angle_diff}°）"
    else:
        moon_interference = f"⚠️ 嚴重干擾（月亮與銀河僅相距 {angle_diff}°，構圖困難）"

    # 建議構圖方向（面向銀河核心方向）
    mw_direction = az_to_direction(mw_az)
    composition_tip = (
        f"面向 {mw_direction}（{mw_az}°）拍攝銀河核心\n"
        f"  仰角約 {best['alt_deg']}°，建議廣角鏡下壓地景"
    )
    # 若月亮與銀河方向相近，給出閃避建議
    if angle_diff < 30 and moon_alt.degrees > 0:
        composition_tip += "\n  ⚠️ 月亮方向與銀河重疊，可等月落後再拍或嘗試縮小構圖迴避"

    return {
        "best_datetime":        best["datetime_tst"],
        "mw_alt_deg":           best["alt_deg"],
        "mw_az_deg":            mw_az,
        "mw_direction":         mw_direction,
        "moon_az_deg":          moon_az_deg,
        "moon_alt_deg":         round(moon_alt.degrees, 1),
        "moon_direction":       az_to_direction(moon_az_deg),
        "angle_diff":           angle_diff,
        "moon_interference":    moon_interference,
        "composition_tip":      composition_tip,
    }


# ── 原有計算邏輯（保留，加入暗空窗口篩選）────────────────────

def compute_target_windows(observer, target, query_dates, dark_windows_by_date=None):
    """
    計算標的在各日期的最佳觀測時刻。
    若提供 dark_windows_by_date，則只在有效暗空窗口內搜尋。
    """
    star = Star(ra_hours=target["ra_hours"], dec_degrees=target["dec_degrees"])
    windows = []

    for d in query_dates:
        # 取該日的有效暗空窗口（若沒有則用預設 19:00~05:00）
        if dark_windows_by_date and d in dark_windows_by_date:
            day_windows = dark_windows_by_date[d]
        else:
            day_windows = None

        if day_windows is not None and len(day_windows) == 0:
            # 當日無暗空窗口，跳過
            continue

        if day_windows:
            # 只在暗空窗口內掃描
            scan_times = []
            for (win_start, win_end) in day_windows:
                current = win_start
                while current <= win_end:
                    scan_times.append(current)
                    current += timedelta(minutes=10)
        else:
            # 預設：19:00 ~ 04:50 TST，每 10 分鐘
            tz_tst = timezone(timedelta(hours=8))
            scan_times = [
                datetime(d.year, d.month, d.day, 19, 0, tzinfo=tz_tst) + timedelta(minutes=mo)
                for mo in range(0, 10 * 60, 10)
            ]

        best_for_day = None
        for dt_tst in scan_times:
            dt_utc = dt_tst.astimezone(timezone.utc)
            t = ts.from_datetime(dt_utc)
            apparent = (eph['earth'] + observer).at(t).observe(star).apparent()
            alt, az, _ = apparent.altaz()

            if target.get("min_alt", 10) <= alt.degrees <= target.get("max_alt", 80):
                if best_for_day is None or alt.degrees > best_for_day["alt_deg"]:
                    best_for_day = {
                        "target_name":  target["name"],
                        "target_type":  target["type"],
                        "datetime_tst": dt_tst,
                        "alt_deg":      round(alt.degrees, 1),
                        "az_deg":       round(az.degrees, 1),
                        "in_dark_window": day_windows is not None,
                    }

        if best_for_day:
            windows.append(best_for_day)

    return windows


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
            # ★ 新增
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


def check_weather_multi(lat, lon, query_dates):
    if not query_dates:
        return {}
    today = date.today()
    max_d = today + timedelta(days=15)
    valid = [d for d in query_dates if today <= d <= max_d]
    fb = {"cloud_cover": -1, "humidity": -1, "temp_c": -1,
          "dew_point_c": -1, "dew_risk": False, "good_weather": True,
          "visibility_km": -1}
    if not valid:
        return {d: fb for d in query_dates}

    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
           f"&hourly=cloud_cover,visibility,relative_humidity_2m,temperature_2m,dew_point_2m"
           f"&start_date={min(valid).isoformat()}&end_date={max(valid).isoformat()}"
           f"&timezone=Asia%2FTaipei")
    raw = requests.get(url, timeout=10).json()
    if "hourly" not in raw:
        return {d: fb for d in query_dates}

    data = raw["hourly"]
    hi   = {}
    for i, t_str in enumerate(data["time"]):
        dt = datetime.fromisoformat(t_str)
        hi[dt] = {
            "cloud_cover": data["cloud_cover"][i],
            "humidity":    data["relative_humidity_2m"][i],
            "temp_c":      data["temperature_2m"][i],
            "dew_point_c": data["dew_point_2m"][i],
            "visibility":  data["visibility"][i],   # 公尺，稍後轉換為公里
        }

    daily = {}
    for d in query_dates:
        if d not in valid:
            daily[d] = fb
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
            av = round(sum(x["visibility"]  for x in night) / len(night) / 1000, 1)  # m→km
            daily[d] = {
                "cloud_cover": ac, "humidity": ah,
                "temp_c": at, "dew_point_c": ad,
                "dew_risk":       (at - ad) < 3.0,
                "good_weather":   ac <= 40,
                "visibility_km":  av,
            }
    return daily


# ── ★ 新功能：7Timer 視寧度與大氣透明度 ──────────────────────────

def get_7timer_seeing(lat, lon, query_dates):
    """
    從 7Timer ASTRO API 取得夜間平均視寧度與大氣透明度。
    每 3 小時一筆，取每晚 20:00–02:00 TST 的平均值。

    seeing：1=極佳(<0.5"), 2=很好, 3=良好, 4=普通, 5=尚可, 6=差, 7=很差, 8=極差(>2.5")
    transparency：1=極佳, 2=很好, 3=良好, 4=普通, 5=尚可, 6=差, 7=很差, 8=極差
    數字越小越好。
    """
    fallback = {"seeing": -1, "transparency": -1}
    try:
        url = (f"http://www.7timer.info/bin/astro.php"
               f"?lon={lon}&lat={lat}&ac=0&unit=metric&output=json&tzoffset=8")
        raw = requests.get(url, timeout=10).json()
        init_dt = datetime.strptime(raw["init"], "%Y%m%d%H").replace(tzinfo=timezone.utc)
    except Exception as e:
        print(f"[7Timer 錯誤] {e}", flush=True)
        return {d: fallback for d in query_dates}

    tz_tst = timezone(timedelta(hours=8))

    # 建立各時間點的資料字典（key = datetime in TST）
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
            # 當夜 20:00–23:00 或隔日 00:00–02:00
            if (dt_tst.date() == d and h >= 20) or \
               (dt_tst.date() == d + timedelta(days=1) and h <= 2):
                night.append(v)
        if night:
            daily[d] = {
                "seeing":       round(sum(x["seeing"]       for x in night) / len(night), 1),
                "transparency": round(sum(x["transparency"] for x in night) / len(night), 1),
            }
        else:
            daily[d] = fallback
    return daily


def parse_intent(user_query):
    today_str = date.today().isoformat()
    system = f"""你是天文攝影查詢系統的意圖解析器。今天是 {today_str}。
從用戶查詢中提取以下欄位，以 JSON 格式回覆，絕對不要加任何說明文字或 markdown。
{{"query_type":"A或B","location_name":"地名","lat":緯度,"lon":經度,
"date_start":"YYYY-MM-DD","date_end":"YYYY-MM-DD","targets":[],"extra_notes":""}}
query_type：A=有具體天體（銀河/獵戶座/M42等），B=開放探索
日期：「這個週末」→最近週六日；具體日期年份用{today_str[:4]}；未指定範圍則首尾同日
地名座標：日月潭(23.865,120.917),合歡山(24.167,121.283),外澳(24.870,121.862),
墾丁(21.945,120.803),阿里山(23.517,120.800),嘉明湖(23.250,121.000),
武陵農場(24.367,121.367),太平山(24.517,121.617),七星山(25.167,121.533),
清境農場(24.083,121.167),奧萬大(23.850,121.083),桃源谷(25.100,121.867)"""

    resp = client.messages.create(
        model="claude-sonnet-4-5", max_tokens=400, system=system,
        messages=[{"role": "user", "content": user_query}]
    )
    text = re.sub(r"```(?:json)?|```", "", resp.content[0].text.strip()).strip()
    return json.loads(text)


def match_targets(target_names):
    if not target_names:
        return TARGET_LIBRARY
    matched = []
    for name in target_names:
        for t in TARGET_LIBRARY:
            if name.lower() in t["name"].lower() or t["name"].lower() in name.lower():
                if t not in matched:
                    matched.append(t)
    # ★ Bug #2 修正：找不到時回傳空陣列，不 fallback 整個標的庫
    # 避免未知天體（如 C/2026 A1）觸發全庫計算導致卡死
    return matched


# ── ★ 超出範圍偵測 ────────────────────────────────────────────

# 不支援的天體關鍵字 → (類型標籤, 知會說明)
UNSUPPORTED_KEYWORDS = {
    # 行星
    "水星": ("planet", "行星位置"),
    "金星": ("planet", "行星位置"),
    "火星": ("planet", "行星位置"),
    "木星": ("planet", "行星位置"),
    "土星": ("planet", "行星位置"),
    "天王星": ("planet", "行星位置"),
    "海王星": ("planet", "行星位置"),
    "冥王星": ("planet", "行星位置"),
    "planet": ("planet", "行星位置"),
    "大距": ("planet", "行星位置"),       # 水星西大距、東大距
    "衝":   ("planet", "行星位置"),       # 木星衝、火星衝
    "合月": ("planet", "行星位置"),       # 行星合月
    "凌日": ("planet", "行星位置"),       # 金星凌日、水星凌日
    # 日食月食（含各種口語說法）
    "日食": ("eclipse", "日食／月食預測"),
    "月食": ("eclipse", "日食／月食預測"),
    "日蝕": ("eclipse", "日食／月食預測"),
    "月蝕": ("eclipse", "日食／月食預測"),
    "全食": ("eclipse", "日食／月食預測"),   # 月全食、日全食
    "偏食": ("eclipse", "日食／月食預測"),   # 月偏食、日偏食
    "環食": ("eclipse", "日食／月食預測"),   # 日環食
    "食既": ("eclipse", "日食／月食預測"),
    "生光": ("eclipse", "日食／月食預測"),
    "eclipse": ("eclipse", "日食／月食預測"),
}

# 彗星查詢：支援但座標為近似值（已知彗星）
COMET_KEYWORDS = ["彗星", "comet", "atlas", "紫金山"]

# 未知彗星 IAU 命名格式（如 C/2026 A1、P/2025 R3）
import re as _re
UNKNOWN_COMET_PATTERN = _re.compile(r'\b[CPDXIcp]/\d{4}\b', _re.IGNORECASE)

def check_unsupported(user_query: str, intent: dict) -> dict:
    """
    分析查詢是否包含超出支援範圍的天體。
    回傳：
      {
        "has_unsupported": bool,       # 完全不支援（行星/日食月食/未知彗星）
        "has_comet_warning": bool,     # 有支援但座標為近似值（已知彗星）
        "unsupported_labels": [str],   # 不支援的功能名稱列表
        "wish_text": str,              # 自動填入許願池的內容
      }
    """
    query_lower = user_query.lower()
    targets_lower = [t.lower() for t in intent.get("targets", [])]
    all_text = query_lower + " " + " ".join(targets_lower)

    unsupported_labels = []
    for keyword, (ktype, label) in UNSUPPORTED_KEYWORDS.items():
        if keyword in all_text:
            if label not in unsupported_labels:
                unsupported_labels.append(label)

    # ★ 未知彗星偵測：IAU 命名格式（C/2026 A1、P/2025 R3 等）
    # 若符合格式但不是已知支援的彗星 → 視為不支援
    unknown_comet_match = UNKNOWN_COMET_PATTERN.search(user_query)
    is_known_comet = any(kw in all_text for kw in ["紫金山", "atlas"])
    if unknown_comet_match and not is_known_comet:
        comet_name = unknown_comet_match.group(0)
        label = f"彗星即時座標（{comet_name}）"
        if label not in unsupported_labels:
            unsupported_labels.append(label)

    has_comet_warning = any(kw in all_text for kw in COMET_KEYWORDS) and not unknown_comet_match

    return {
        "has_unsupported":    len(unsupported_labels) > 0,
        "has_comet_warning":  has_comet_warning,
        "unsupported_labels": unsupported_labels,
        "wish_text":          f"希望支援：{'、'.join(unsupported_labels)}（原始查詢：{user_query}）",
    }


def run_query(user_query, prefetched_intent=None):
    intent    = prefetched_intent if prefetched_intent else parse_intent(user_query)
    observer  = wgs84.latlon(intent["lat"], intent["lon"])
    date_start = date.fromisoformat(intent["date_start"])
    date_end   = date.fromisoformat(intent["date_end"])
    query_dates = [date_start + timedelta(days=i)
                   for i in range((date_end - date_start).days + 1)]

    # ★ 先算月相＋月出月落＋暗空窗口
    moon_info = get_moon_info(observer, query_dates)

    # ★ 建立 date → dark_windows 的查找表
    dark_windows_by_date = {m["date"]: m["dark_windows"] for m in moon_info}

    # ★ 計算標的時用暗空窗口篩選
    all_windows = []
    for target in match_targets(intent.get("targets", [])):
        all_windows.extend(
            compute_target_windows(observer, target, query_dates, dark_windows_by_date)
        )

    showers = [s for d in query_dates for s in check_meteor_shower(d)]
    weather     = check_weather_multi(intent["lat"], intent["lon"], query_dates)
    seeing_data = get_7timer_seeing(intent["lat"], intent["lon"], query_dates)

    for w in all_windows:
        wx = weather.get(w["datetime_tst"].date(), {})
        sd = seeing_data.get(w["datetime_tst"].date(), {})
        w.update({
            "cloud_cover":    wx.get("cloud_cover",   -1),
            "humidity":       wx.get("humidity",      -1),
            "temp_c":         wx.get("temp_c",        -1),
            "dew_point_c":    wx.get("dew_point_c",   -1),
            "dew_risk":       wx.get("dew_risk",      False),
            "good_weather":   wx.get("good_weather",  False),
            "visibility_km":  wx.get("visibility_km", -1),
            "seeing":         sd.get("seeing",        -1),
            "transparency":   sd.get("transparency",  -1),
        })

    good = [w for w in all_windows if w.get("good_weather", False)]

    # ★ Bug #5 修正：判斷是否所有查詢日期都超出氣象預報範圍
    today = date.today()
    max_forecast = today + timedelta(days=15)
    all_windows_out_of_range = all(d > max_forecast for d in query_dates)

    # ★ 計算銀河構圖方位（取第一天有暗空窗口的結果）
    mw_composition_by_date = {}
    for m in moon_info:
        d = m["date"]
        comp = get_milky_way_composition(observer, d, m["dark_windows"])
        if comp:
            mw_composition_by_date[d] = comp

    # 直接計算平均雲量（不依賴天文窗口）
    cloud_values = [v["cloud_cover"] for v in weather.values() if v.get("cloud_cover", -1) >= 0]
    avg_cloud_cover = round(sum(cloud_values) / len(cloud_values), 1) if cloud_values else -1

    # ★ 計算平均能見度
    vis_values = [v["visibility_km"] for v in weather.values() if v.get("visibility_km", -1) >= 0]
    avg_visibility_km = round(sum(vis_values) / len(vis_values), 1) if vis_values else -1

    # ★ 計算平均視寧度與大氣透明度（7Timer）
    seeing_values = [v["seeing"] for v in seeing_data.values() if v.get("seeing", -1) > 0]
    transp_values = [v["transparency"] for v in seeing_data.values() if v.get("transparency", -1) > 0]
    avg_seeing       = round(sum(seeing_values) / len(seeing_values), 1) if seeing_values else -1
    avg_transparency = round(sum(transp_values) / len(transp_values), 1) if transp_values else -1

    return {
        "intent":      intent,
        "good_windows": good[:10],
        "all_windows":  all_windows,
        "moon_info":   moon_info,
        "showers":     showers,
        "mw_composition_by_date":    mw_composition_by_date,
        "all_windows_out_of_range":  all_windows_out_of_range,
        "avg_cloud_cover":           avg_cloud_cover,
        "avg_visibility_km":         avg_visibility_km,
        "avg_seeing":                avg_seeing,
        "avg_transparency":          avg_transparency,
    }


# ── 回覆生成（新增兩個區塊）────────────────────────────────────

def _format_time(dt):
    """格式化時刻顯示"""
    if dt is None:
        return "N/A"
    return dt.strftime("%H:%M")

def generate_reply(result):
    intent    = result["intent"]
    good      = result["good_windows"]
    all_wins  = result.get("all_windows", [])
    moon_info = result["moon_info"]
    showers   = result["showers"]
    mw_comp   = result["mw_composition_by_date"]

    # ── 送給 LLM 的時刻資料 ──────────────────────────────────
    # 天氣不佳時 good 可能為空 → fallback 到天文窗口（最多 10 個），讓 LLM 仍能提供時刻建議
    windows_for_llm = good if good else sorted(
        all_wins, key=lambda w: w.get("alt_deg", 0), reverse=True
    )[:10]
    weather_fallback = (not good) and bool(all_wins)  # True 代表使用 fallback

    ws = json.dumps([{
        "標的":    w["target_name"],
        "日期時間": w["datetime_tst"].strftime("%m/%d %H:%M TST"),
        "仰角":    f"{w['alt_deg']}°",
        "方位角":  f"{w['az_deg']}°",
        "暗空窗口內": w.get("in_dark_window", False),
        "雲量":    f"{w['cloud_cover']}%" if w['cloud_cover'] >= 0 else "預報範圍外",
        "濕度":    f"{w['humidity']}%"    if w['humidity'] >= 0    else "N/A",
        "溫度":    f"{w['temp_c']}°C"     if w['temp_c'] >= -50    else "N/A",
        "結露風險": w["dew_risk"],
        "能見度":  f"{w['visibility_km']} km" if w.get('visibility_km', -1) >= 0 else "N/A",
        "視寧度":  f"{w['seeing']}/8（1最佳）"       if w.get('seeing', -1) > 0 else "N/A",
        "大氣透明度": f"{w['transparency']}/8（1最佳）" if w.get('transparency', -1) > 0 else "N/A",
    } for w in windows_for_llm], ensure_ascii=False, indent=2)

    # ★ 月亮窗口資訊（給 LLM）
    moon_summary = []
    for m in moon_info:
        d_str = m["date"].isoformat()
        rise_str = _format_time(m["moonrise"])
        set_str  = _format_time(m["moonset"])
        moon_summary.append({
            "日期":   d_str,
            "月相":   m["moon_phase_desc"],
            "月出":   f"{rise_str} 方位 {m['moonrise_az']}°" if m["moonrise_az"] else rise_str,
            "月落":   f"{set_str} 方位 {m['moonset_az']}°"  if m["moonset_az"]  else set_str,
            "天文薄暮": f"{_format_time(m['evening_twilight'])} ～ {_format_time(m['morning_twilight'])}",
            "暗空窗口": m["dark_window_desc"],
        })
    ms = json.dumps(moon_summary, ensure_ascii=False, indent=2)

    # ★ 銀河構圖資訊（給 LLM）
    if mw_comp:
        mw_list = []
        for d, c in mw_comp.items():
            mw_list.append({
                "日期":     d.isoformat(),
                "最佳時刻": c["best_datetime"].strftime("%H:%M TST"),
                "銀河方位": f"{c['mw_az_deg']}°（{c['mw_direction']}），仰角 {c['mw_alt_deg']}°",
                "月亮方位": f"{c['moon_az_deg']}°（{c['moon_direction']}），仰角 {c['moon_alt_deg']}°",
                "月亮干擾": c["moon_interference"],
                "構圖建議": c["composition_tip"],
            })
        mw_str = json.dumps(mw_list, ensure_ascii=False, indent=2)
    else:
        mw_str = "銀河核心在有效暗空窗口內仰角不足，不建議拍攝"

    ss = json.dumps([{
        "流星雨": s["name"], "距極大期": f"{s['days_to_peak']:+d}天",
        "ZHR": s["zenithal_hourly_rate"]
    } for s in showers], ensure_ascii=False) if showers else "無"

    # ── ★ 氣象狀態評估（第一道篩選）────────────────────────────
    all_windows_out    = result.get("all_windows_out_of_range", False)
    avg_cloud          = result.get("avg_cloud_cover",    -1)
    avg_visibility_km  = result.get("avg_visibility_km",  -1)
    avg_seeing         = result.get("avg_seeing",         -1)
    avg_transparency   = result.get("avg_transparency",   -1)

    if all_windows_out:
        weather_status = "out_of_range"
    elif avg_cloud < 0:
        weather_status = "unknown"
    elif avg_cloud > 80:
        weather_status = "bad"
    elif avg_cloud > 40:
        weather_status = "unstable"
    else:
        weather_status = "good"

    # ── ★ 動態 system prompt（依氣象狀態調整）──────────────────
    if weather_status == "out_of_range":
        weather_instruction = """
⚠️ 氣象資料不可用（查詢日期超出預報範圍 15 天）。
規則：
- 【結論】只能基於天文條件（月相、暗空窗口），絕對不可提及天氣好壞
- 【結論】必須開頭說明「氣象未知」
- 【氣象分析】寫「查詢日期超出預報範圍，無法提供氣象資料」
- 其他區塊正常提供天文計算結果"""

    elif weather_status == "bad":
        weather_instruction = """
⛔ 氣象條件極差（雲量極高或有降雨）。
規則：
- 【結論】直接說明天況不適合出門拍攝，但仍給出天文條件最佳時刻供參考
- 【推薦時刻】仍列出 Top 3 天文窗口（標注「天況不佳，僅供天文參考」），包含仰角、方位角、雲量
- 【氣象分析】詳細說明惡劣天況
- 【銀河構圖方位】可簡化或省略
- 主動建議改期或換天氣更好的日期"""

    elif weather_status == "unstable":
        weather_instruction = """
⚠️ 氣象條件不穩定（雲量 40~80%）。
規則：
- 【結論】標註「天況不穩定，建議當天再確認即時預報」
- 【氣象分析】說明雲量變化風險
- 其他天文資訊正常提供"""

    else:
        weather_instruction = "✅ 氣象條件良好，正常提供完整分析。"

    system = f"""你是專業天文攝影顧問，熟悉台灣各地拍攝環境。繁體中文，親切專業。

【重要】氣象條件是第一優先判斷：
{weather_instruction}

回覆格式（每區塊標題用【】，依氣象狀態調整詳細程度）：

【結論】
- 若查詢跨多天：每天一行，icon 代表「這天值不值得去」的最終建議：
  ✅ = 天氣與天文都好，值得前往
  ⚠️ = 天氣或天文其中一項有顧慮（例如月光縮短暗空、能見度偏低）
  ❌ = 天況或天文條件太差，不建議
  格式：「04/08 ⚠️ 雲量X%・能見度Xkm，[天文或氣象問題說明]」
  最後一行：「➡️ 最佳：04/XX 凌晨 HH:MM，[一句話原因]」
- 若查詢單天：一句話點出最佳時刻＋天況（氣象＋天文各一個重點）
- 每行只用一個 icon 代表綜合建議，不要同時出現兩個 icon 造成混淆
- 目標：讓用戶一眼看出哪幾天能去、哪天最好、為什麼

【推薦時刻】Top 3，每條格式：
  日期 時刻 ⭐（若暗空窗口內）
  仰角 X°、方位角 X°（中文方向）｜雲量 X%、能見度 X km、濕度 X%
  天況不佳時標注「天況不佳，僅供天文參考」

【月亮窗口】每日一條：
  - 月出/月落時刻＋方位角
  - 有效暗空窗口時段（時長）
  - 對深空攝影的影響評估（月相＋亮度）

【銀河構圖方位】（天況極差時可省略）
  - 銀河核心方位角＋中文方向＋仰角
  - 月亮相對位置
  - 具體構圖建議（鏡頭焦段、前景選擇）

【氣象分析】
  - 雲量：夜間平均 X%
  - 能見度：平均 X km
  - 結露風險：溫度/露點差，是否需要加熱帶
  - 若有視寧度與透明度（7Timer，1=最佳 8=最差），簡要評估對星點清晰度的影響

【裝備提醒】針對地點高度、溫度、交通特性給出具體建議

若有流星雨加【流星雨加碼】

核心原則：
- 天文數據（仰角、方位角、月出月落）來自精確計算，如實呈現
- 氣象判斷只根據提供的數據，不自行假設
- 天況不佳時主動建議替代方案（改期、換地點、轉攻其他題材）
- 總長不超過 500 字"""

    resp = client.messages.create(
        model="claude-sonnet-4-5", max_tokens=1000, system=system,
        messages=[{"role": "user", "content":
            f"查詢類型：{'指定標的' if intent['query_type']=='A' else '開放探索'}\n"
            f"地點：{intent['location_name']}\n"
            f"日期：{intent['date_start']} ～ {intent['date_end']}\n"
            f"氣象狀態：{weather_status}\n"
            f"夜間平均雲量：{avg_cloud}%\n"
            f"夜間平均能見度：{f'{avg_visibility_km} km' if avg_visibility_km >= 0 else 'N/A'}\n"
            f"夜間平均視寧度（7Timer）：{f'{avg_seeing}/8（1=最佳）' if avg_seeing > 0 else 'N/A'}\n"
            f"夜間平均大氣透明度（7Timer）：{f'{avg_transparency}/8（1=最佳）' if avg_transparency > 0 else 'N/A'}\n\n"
            f"候選時刻{'（天氣不佳，以下為天文窗口供參考）' if weather_fallback else ''}：\n{ws if windows_for_llm else '無天文觀測窗口'}\n\n"
            f"月相與暗空窗口：\n{ms}\n\n"
            f"銀河構圖資訊：\n{mw_str}\n\n"
            f"流星雨：{ss}"
        }]
    )
    return resp.content[0].text


# ── 對話狀態 ──────────────────────────────────────────────────

WAITING_WISH = 1
user_last_query = {}

def make_feedback_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👍 氣象準確", callback_data="rate_good"),
         InlineKeyboardButton("👎 氣象不準", callback_data="rate_bad")],
        [InlineKeyboardButton("💡 許願 / 建議", callback_data="wish")],
    ])

def make_unsupported_keyboard():
    """超出範圍查詢的許願按鈕"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💡 加入許願池", callback_data="wish_auto"),
         InlineKeyboardButton("略過", callback_data="wish_skip")],
    ])

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text     = update.message.text.strip()
    username = update.effective_user.first_name or "朋友"
    user_id  = update.effective_user.id
    chat_id  = update.effective_chat.id

    print(f"[收到] {username}: {text}", flush=True)

    if text in ["/start", "/help", "help", "說明"]:
        await update.message.reply_text(
            "🔭 *天文攝影查詢 Bot*\n\n直接用自然語言問我，例如：\n"
            "• `4月15日 合歡山 銀河`\n"
            "• `這個週末 阿里山 有什麼可以拍？`\n"
            "• `5月1日到3日 墾丁 天蠍座`\n\n"
            "我會幫你計算最佳觀測時刻、月亮暗空窗口、銀河構圖方位和氣象條件 🌌",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    cancel_keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ 取消", callback_data="cancel")
    ]])
    thinking_msg = await update.message.reply_text(
        "🔭 計算中，請稍候...",
        reply_markup=cancel_keyboard
    )
    context.user_data["thinking_msg_id"] = thinking_msg.message_id
    context.user_data["cancelled"]       = False

    try:
        if context.user_data.get("cancelled"):
            return ConversationHandler.END

        # ★ Bug #1 #3 修正：先解析意圖，立即攔截完全不支援的查詢
        #    不進入 run_query()，避免給出錯誤答案或浪費計算資源
        intent_for_check = parse_intent(text)
        scope = check_unsupported(text, intent_for_check)

        if scope["has_unsupported"]:
            # 完全不支援 → 直接知會，不跑天文計算
            labels = "、".join(scope["unsupported_labels"])
            notice = (
                f"⚠️ *目前版本尚不支援：{labels}*\n\n"
                f"很抱歉，這個查詢超出目前的功能範圍。\n"
                f"想把這個需求加入許願池，讓我們優先開發嗎？"
            )
            context.user_data["wish_auto_text"] = scope["wish_text"]
            user_last_query[chat_id] = text
            await thinking_msg.delete()
            await update.message.reply_text(
                notice,
                parse_mode="Markdown",
                reply_markup=make_unsupported_keyboard()
            )
            print(f"[攔截] 不支援查詢：{labels}", flush=True)
            return ConversationHandler.END

        # ★ Bug #2 修正：未知彗星在 check_unsupported 階段會被 has_comet_warning 標記
        #    但如果是完全未知的彗星名稱（不含支援關鍵字），
        #    match_targets() 返回空陣列而非整個標的庫（見 match_targets 修正）

        result = run_query(text, prefetched_intent=intent_for_check)

        if context.user_data.get("cancelled"):
            return ConversationHandler.END

        reply = generate_reply(result)

        if context.user_data.get("cancelled"):
            return ConversationHandler.END

        user_last_query[chat_id] = text
        log_query(username, user_id, text, result["intent"])

        await thinking_msg.delete()

        # ★ 彗星警告：無論天候好壞都附上（Bug #4 修正移至 generate_reply）
        if scope["has_comet_warning"]:
            comet_notice = (
                "\n\n⚠️ *彗星座標說明*：目前使用近似固定座標，不反映每日實際位置，"
                "僅供參考。如需即時座標，歡迎加入許願池催促我們升級！"
            )
            context.user_data["wish_auto_text"] = scope["wish_text"]
            await update.message.reply_text(
                reply + comet_notice,
                parse_mode="Markdown",
                reply_markup=make_feedback_keyboard()
            )
        else:
            await update.message.reply_text(
                reply,
                parse_mode="Markdown",
                reply_markup=make_feedback_keyboard()
            )

        print("[回覆] 完成", flush=True)

    except Exception as e:
        if not context.user_data.get("cancelled"):
            await thinking_msg.delete()
            await update.message.reply_text(
                f"⚠️ 發生錯誤，請重新嘗試。\n\n`{type(e).__name__}: {e}`",
                parse_mode="Markdown"
            )
        print(f"[錯誤] {type(e).__name__}: {e}", flush=True)

    return ConversationHandler.END


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    username = query.from_user.first_name or "朋友"
    user_id  = query.from_user.id
    chat_id  = query.message.chat_id
    data     = query.data
    last_q   = user_last_query.get(chat_id, "")

    await query.answer()

    if data == "cancel":
        context.user_data["cancelled"] = True
        await query.edit_message_text("❌ 已取消查詢")
        return ConversationHandler.END
    elif data == "rate_good":
        log_feedback(username, user_id, last_q, "👍", "評分")
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("謝謝你的回饋！👍 已記錄")
        return ConversationHandler.END
    elif data == "rate_bad":
        log_feedback(username, user_id, last_q, "👎", "評分")
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("謝謝你的回饋！👎 已記錄，我們會繼續改進")
        return ConversationHandler.END
    elif data == "wish":
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("💡 請說說你的建議或想新增的功能，直接輸入文字就好：")
        return WAITING_WISH
    elif data == "wish_auto":
        # 自動許願：直接用 context 裡預存的文字記錄，不需用戶再輸入
        wish_text = context.user_data.get("wish_auto_text", last_q)
        log_feedback(username, user_id, last_q, "💡", "許願（自動）", wish_text)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("💡 已加入許願池！謝謝你的支持，我們會優先考慮開發 🙏")
        print(f"[許願-自動] {username}: {wish_text}", flush=True)
        return ConversationHandler.END
    elif data == "wish_skip":
        await query.edit_message_reply_markup(reply_markup=None)
        return ConversationHandler.END

    return ConversationHandler.END


async def handle_wish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text     = update.message.text.strip()
    username = update.effective_user.first_name or "朋友"
    user_id  = update.effective_user.id
    chat_id  = update.effective_chat.id
    last_q   = user_last_query.get(chat_id, "")

    log_feedback(username, user_id, last_q, "💡", "許願", text)
    await update.message.reply_text("謝謝你的建議！💡 已記錄到許願池 🙏")
    print(f"[許願] {username}: {text}", flush=True)
    return ConversationHandler.END


# ── 主程式 ────────────────────────────────────────────────────

async def main():
    request = HTTPXRequest(
        connection_pool_size=8,
        read_timeout=30, write_timeout=30,
        connect_timeout=30, pool_timeout=30,
    )
    app = Application.builder().token(TELEGRAM_TOKEN).request(request).build()

    conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message),
            MessageHandler(filters.COMMAND, handle_message),
            CallbackQueryHandler(handle_callback),
        ],
        states={
            WAITING_WISH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_wish)],
        },
        fallbacks=[MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)],
    )
    app.add_handler(conv)

    print("🚀 Bot 啟動中...", flush=True)
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()


if __name__ == "__main__":
    import time
    while True:
        try:
            asyncio.run(main())
        except Exception as e:
            print(f"[重啟] Bot 崩潰：{e}，5 秒後重啟...", flush=True)
            time.sleep(5)
