import hashlib, http.client, math, requests, json, re, logging, os, threading, traceback

from datetime import datetime, timedelta, timezone, date
from skyfield.api import Star, wgs84, load
from skyfield import almanac
from flask import Flask, request, abort, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import (
    MessageEvent, PostbackEvent, TextMessage, TextSendMessage,
    QuickReply, QuickReplyButton, PostbackAction,
)
import gspread
from google.oauth2.service_account import Credentials

def read_openrouter_api_key():
    for env_name in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        raw = os.environ.get(env_name, "")
        key = raw.strip().strip('"').strip("'")
        key = re.sub(r"^Authorization:\s*", "", key, flags=re.IGNORECASE).strip()
        key = re.sub(r"^Bearer\s+", "", key, flags=re.IGNORECASE).strip()
        key = re.sub(r"\s+", "", key)
        key = "".join(ch for ch in key if ch.isascii() and ch.isprintable())
        if key:
            return key, env_name
    return "", ""

def describe_openrouter_key():
    if not OPENROUTER_API_KEY:
        return "not configured"
    if OPENROUTER_API_KEY.startswith("sk-or-v1-"):
        shape = "openrouter"
    elif OPENROUTER_API_KEY.startswith("sk-"):
        shape = "sk"
    else:
        shape = "unexpected"
    return f"source={OPENROUTER_API_KEY_SOURCE}, length={len(OPENROUTER_API_KEY)}, shape={shape}"

def fingerprint_openrouter_key():
    if not OPENROUTER_API_KEY:
        return "none"
    return hashlib.sha256(OPENROUTER_API_KEY.encode("utf-8")).hexdigest()[:12]

def fingerprint_line_access_token():
    if not LINE_ACCESS_TOKEN:
        return "none"
    return hashlib.sha256(LINE_ACCESS_TOKEN.encode("utf-8")).hexdigest()[:12]

def describe_line_token():
    if not LINE_ACCESS_TOKEN:
        return "not configured"
    return f"length={len(LINE_ACCESS_TOKEN)}, fingerprint={fingerprint_line_access_token()}"

OPENROUTER_API_KEY, OPENROUTER_API_KEY_SOURCE = read_openrouter_api_key()
OPENROUTER_MODEL     = os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash")
OPENROUTER_FALLBACK_MODELS = os.environ.get("OPENROUTER_FALLBACK_MODELS", "google/gemini-2.5-flash,openai/gpt-4o-mini")
OPENROUTER_SITE_URL  = os.environ.get("OPENROUTER_SITE_URL", "https://astro-bot-l9ae.onrender.com")
OPENROUTER_APP_NAME  = os.environ.get("OPENROUTER_APP_NAME", "astro-bot")
LINE_CHANNEL_SECRET  = os.environ.get("LINE_CHANNEL_SECRET")
LINE_ACCESS_TOKEN    = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
GOOGLE_CREDENTIALS   = os.environ.get("GOOGLE_CREDENTIALS_JSON")
SPREADSHEET_ID       = os.environ.get("GOOGLE_SPREADSHEET_ID", "1u-IDQPi0g-mFxPDetdV46p90xRgLAQZ3Jz90brLl6-M")
APP_VERSION          = os.environ.get("RENDER_GIT_COMMIT", "local-openrouter-healthz")[:12]

line_bot_api = LineBotApi(LINE_ACCESS_TOKEN)
handler      = WebhookHandler(LINE_CHANNEL_SECRET)
app          = Flask(__name__)
logging.basicConfig(level=logging.ERROR)
print(
    f"🔐 OpenRouter key source: {OPENROUTER_API_KEY_SOURCE or 'not configured'}",
    flush=True,
)
print(f"🔐 OpenRouter key check: {describe_openrouter_key()}", flush=True)
print(f"🔐 OpenRouter key fingerprint: {fingerprint_openrouter_key()}", flush=True)
print(f"🔐 LINE access token check: {describe_line_token()}", flush=True)
print(f"🚦 App version: {APP_VERSION}", flush=True)

# ── OpenRouter LLM client ─────────────────────────────────────

def openrouter_headers():
    return {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "HTTP-Referer": OPENROUTER_SITE_URL,
        "X-Title": OPENROUTER_APP_NAME,
    }

def openrouter_request(method, path, payload=None, timeout=60):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    conn = http.client.HTTPSConnection("openrouter.ai", timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=openrouter_headers())
        response = conn.getresponse()
        response_body = response.read().decode("utf-8", errors="replace")
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status}: {response_body[:500]}")
        return json.loads(response_body) if response_body else {}
    finally:
        conn.close()

OPENROUTER_KEY_PROBE_STATUS = "not_run"
LINE_API_PROBE_STATUS = "not_run"

def probe_openrouter_key():
    global OPENROUTER_KEY_PROBE_STATUS
    if not OPENROUTER_API_KEY:
        OPENROUTER_KEY_PROBE_STATUS = "skipped:no_key"
        print(f"🔐 OpenRouter key probe: {OPENROUTER_KEY_PROBE_STATUS}", flush=True)
        return
    try:
        data = openrouter_request("GET", "/api/v1/key", timeout=15)
        OPENROUTER_KEY_PROBE_STATUS = f"ok fields={','.join(sorted(data.keys()))}"
        print(f"🔐 OpenRouter key probe: {OPENROUTER_KEY_PROBE_STATUS}", flush=True)
    except Exception as e:
        message = str(e).strip() or repr(e)
        OPENROUTER_KEY_PROBE_STATUS = f"failed:{type(e).__name__}: {message[:200]}"
        print(f"🔐 OpenRouter key probe {OPENROUTER_KEY_PROBE_STATUS}", flush=True)

probe_openrouter_key()

def summarize_line_api_error(error):
    status_code = getattr(error, "status_code", "unknown")
    error_response = getattr(error, "error_response", None)
    message = getattr(error_response, "message", "") if error_response else ""
    headers = getattr(error, "headers", {}) or {}
    auth_error = headers.get("WWW-Authenticate", "")
    details = []
    if status_code:
        details.append(f"status={status_code}")
    if message:
        details.append(f"message={message}")
    if auth_error:
        details.append(f"auth={auth_error}")
    return "; ".join(details) or repr(error)

def log_unhandled_exception(context, error):
    print(f"[未預期錯誤] {context}: {type(error).__name__}: {error}", flush=True)
    print(traceback.format_exc(), flush=True)

def probe_line_access_token():
    global LINE_API_PROBE_STATUS
    if not LINE_ACCESS_TOKEN:
        LINE_API_PROBE_STATUS = "skipped:no_token"
        print(f"🔐 LINE token probe: {LINE_API_PROBE_STATUS}", flush=True)
        return
    try:
        if hasattr(line_bot_api, "get_bot_info"):
            line_bot_api.get_bot_info()
        LINE_API_PROBE_STATUS = "ok"
        print(f"🔐 LINE token probe: {LINE_API_PROBE_STATUS}", flush=True)
    except LineBotApiError as e:
        LINE_API_PROBE_STATUS = f"failed:{summarize_line_api_error(e)[:200]}"
        print(f"🔐 LINE token probe: {LINE_API_PROBE_STATUS}", flush=True)
    except Exception as e:
        LINE_API_PROBE_STATUS = f"failed:{type(e).__name__}: {str(e)[:200]}"
        print(f"🔐 LINE token probe: {LINE_API_PROBE_STATUS}", flush=True)

probe_line_access_token()

def safe_reply_message(reply_token, message, context="reply"):
    try:
        line_bot_api.reply_message(reply_token, message)
        return True
    except LineBotApiError as e:
        print(f"[LINE API 錯誤] {context}: {summarize_line_api_error(e)}", flush=True)
        return False
    except Exception as e:
        log_unhandled_exception(f"LINE {context}", e)
        return False

def safe_push_message(user_id, message, context="push"):
    try:
        line_bot_api.push_message(user_id, message)
        return True
    except LineBotApiError as e:
        print(f"[LINE API 錯誤] {context}: {summarize_line_api_error(e)}", flush=True)
        return False
    except Exception as e:
        log_unhandled_exception(f"LINE {context}", e)
        return False

def openrouter_model_sequence():
    models = [OPENROUTER_MODEL]
    models.extend(m.strip() for m in OPENROUTER_FALLBACK_MODELS.split(",") if m.strip())
    deduped = []
    for model in models:
        if model not in deduped:
            deduped.append(model)
    return deduped

def call_openrouter(system, user_content, max_tokens, temperature=0.2):
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OpenRouter API key is not configured; checked OPENROUTER_API_KEY and ANTHROPIC_API_KEY")

    print(
        f"🔐 OpenRouter raw HTTPS auth: key_length={len(OPENROUTER_API_KEY)}, key_shape={describe_openrouter_key()}",
        flush=True,
    )
    errors = []
    for model in openrouter_model_sequence():
        try:
            print(f"🤖 OpenRouter model attempt: {model}", flush=True)
            data = openrouter_request(
                "POST",
                "/api/v1/chat/completions",
                {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_content},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            if model != OPENROUTER_MODEL:
                print(f"🤖 OpenRouter fallback model succeeded: {model}", flush=True)
            return data["choices"][0]["message"]["content"] or ""
        except Exception as e:
            error = describe_exception(e)
            errors.append(f"{model}: {error}")
            print(f"⚠️ OpenRouter model failed: {model}: {error}", flush=True)
    raise RuntimeError(f"OpenRouter request failed via raw HTTPS: {' | '.join(errors)}")

# ── Google Sheets ──────────────────────────────────────────────

def describe_exception(e):
    message = str(e).strip() or repr(e)
    return f"{type(e).__name__}: {message}"

def parse_google_credentials():
    if not GOOGLE_CREDENTIALS or not GOOGLE_CREDENTIALS.strip():
        raise RuntimeError("GOOGLE_CREDENTIALS_JSON is not configured")
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"GOOGLE_CREDENTIALS_JSON is not valid JSON: {e}") from e
    if not isinstance(creds_dict, dict):
        raise RuntimeError("GOOGLE_CREDENTIALS_JSON must be a JSON object")
    if not creds_dict.get("client_email"):
        raise RuntimeError("GOOGLE_CREDENTIALS_JSON is missing client_email")
    print(
        f"📄 Google Sheets target: spreadsheet={SPREADSHEET_ID}, service_account={creds_dict.get('client_email')}",
        flush=True,
    )
    return creds_dict

def init_sheets():
    creds_dict = parse_google_credentials()
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
        ws_feedback = sh.add_worksheet("用戶反饋", rows=1000, cols=3)
        ws_feedback.append_row(["日期及時間","Line User Name","建議事項的內容"])
    return ws_query, ws_feedback

try:
    ws_query, ws_feedback = init_sheets()
    print("✅ Google Sheets 連線成功", flush=True)
except Exception as e:
    print(f"⚠️ Google Sheets 連線失敗：{describe_exception(e)}", flush=True)
    ws_query = ws_feedback = None


@app.route("/", methods=["GET"])
def root():
    return jsonify({"ok": True, "service": "astro-bot", "version": APP_VERSION})


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({
        "ok": True,
        "version": APP_VERSION,
        "openrouter_key_source": OPENROUTER_API_KEY_SOURCE or "not_configured",
        "openrouter_key_shape": "openrouter" if OPENROUTER_API_KEY.startswith("sk-or-v1-") else "other",
        "openrouter_key_length": len(OPENROUTER_API_KEY),
        "openrouter_key_fingerprint": fingerprint_openrouter_key(),
        "openrouter_key_probe": OPENROUTER_KEY_PROBE_STATUS,
        "openrouter_model": OPENROUTER_MODEL,
        "openrouter_fallback_models": openrouter_model_sequence(),
        "line_token_configured": bool(LINE_ACCESS_TOKEN),
        "line_token_length": len(LINE_ACCESS_TOKEN or ""),
        "line_token_fingerprint": fingerprint_line_access_token(),
        "line_token_probe": LINE_API_PROBE_STATUS,
        "google_sheets_connected": ws_query is not None and ws_feedback is not None,
        "spreadsheet_id": SPREADSHEET_ID,
    })

def log_query(username, user_id, query, intent):
    global ws_query, ws_feedback
    if not ws_query:
        try:
            ws_query, ws_feedback = init_sheets()
        except Exception as e:
            print(f"[Sheets 錯誤] 查詢記錄初始化失敗：{describe_exception(e)}", flush=True)
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
        print(f"[Sheets 錯誤] {describe_exception(e)}", flush=True)

def normalize_feedback_content(rating, feedback_type, wish=""):
    content = (wish or "").strip()
    if content:
        content = re.sub(r"^(建議|許願|希望|wish|suggest|suggestion|feature request|功能建議)\s*[：:，,、-]*\s*", "", content, flags=re.IGNORECASE)
        return content or wish.strip()
    if rating:
        return f"{feedback_type}：{rating}"
    return feedback_type

def log_feedback(username, user_id, query, rating, feedback_type, wish=""):
    global ws_query, ws_feedback
    if not ws_feedback:
        try:
            ws_query, ws_feedback = init_sheets()
        except Exception as e:
            print(f"[Sheets 錯誤] 反饋記錄初始化失敗：{describe_exception(e)}", flush=True)
    if not ws_feedback:
        return False
    content = normalize_feedback_content(rating, feedback_type, wish)
    try:
        ws_feedback.append_row([
            datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
            username, content,
        ])
        print(f"[Sheets] 已記錄反饋：{feedback_type}", flush=True)
        return True
    except Exception as e:
        print(f"[Sheets 錯誤] {describe_exception(e)}", flush=True)
        try:
            ws_query, ws_feedback = init_sheets()
            ws_feedback.append_row([
                datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
                username, content,
            ])
            print(f"[Sheets] 重新連線後已記錄反饋：{feedback_type}", flush=True)
            return True
        except Exception as retry_error:
            print(f"[Sheets 錯誤] 反饋重試失敗：{describe_exception(retry_error)}", flush=True)
            return False

def log_wish(username, user_id, query, wish, wish_type="許願"):
    return log_feedback(username, user_id, query, "💡", wish_type, wish)

def is_direct_wish_text(text):
    normalized = text.strip().lower()
    prefixes = ("許願", "建議", "希望", "wish", "suggest", "suggestion", "feature request", "功能建議")
    keywords = ("希望支援", "希望加入", "建議加入", "建議新增", "想新增", "想支援", "功能建議")
    return any(normalized.startswith(prefix) for prefix in prefixes) or any(keyword in normalized for keyword in keywords)

def extract_mark_as_read_token(event):
    token = getattr(event.message, "mark_as_read_token", "") or getattr(event.message, "markAsReadToken", "")
    if token:
        return token
    try:
        return event.as_json_dict().get("message", {}).get("markAsReadToken", "")
    except Exception:
        return ""

def mark_message_as_read(mark_as_read_token):
    if not LINE_ACCESS_TOKEN or not mark_as_read_token:
        return False
    try:
        response = requests.post(
            "https://api.line.me/v2/bot/message/markAsRead",
            headers={"Authorization": f"Bearer {LINE_ACCESS_TOKEN}"},
            json={"markAsReadToken": mark_as_read_token},
            timeout=5,
        )
        if response.status_code == 200:
            print("[已讀] LINE message marked as read", flush=True)
            return True
        print(f"[已讀錯誤] {response.status_code}: {response.text[:200]}", flush=True)
    except Exception as e:
        print(f"[已讀錯誤] {type(e).__name__}: {e}", flush=True)
    return False

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

# ── 銀河核心方位角計算 ─────────────────────────────────────────

MILKY_WAY_CORE = {"ra_hours": 17.761, "dec_degrees": -29.0}

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

def compute_target_windows(observer, target, query_dates, dark_windows_by_date=None):
    star = Star(ra_hours=target["ra_hours"], dec_degrees=target["dec_degrees"])
    windows = []
    for d in query_dates:
        if dark_windows_by_date and d in dark_windows_by_date:
            day_windows = dark_windows_by_date[d]
        else:
            day_windows = None
        if day_windows is not None and len(day_windows) == 0:
            continue
        if day_windows:
            scan_times = []
            for (win_start, win_end) in day_windows:
                current = win_start
                while current <= win_end:
                    scan_times.append(current)
                    current += timedelta(minutes=10)
        else:
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
            "visibility":  data["visibility"][i],
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
            av = round(sum(x["visibility"]  for x in night) / len(night) / 1000, 1)
            daily[d] = {
                "cloud_cover": ac, "humidity": ah,
                "temp_c": at, "dew_point_c": ad,
                "dew_risk":       (at - ad) < 1.5,
                "good_weather":   ac <= 40,
                "visibility_km":  av,
            }
    return daily


def get_7timer_seeing(lat, lon, query_dates):
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
    text = call_openrouter(system, user_query, max_tokens=400)
    text = re.sub(r"```(?:json)?|```", "", text.strip()).strip()
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
    return matched


# ── 超出範圍偵測 ───────────────────────────────────────────────

UNSUPPORTED_KEYWORDS = {
    "水星": ("planet", "行星位置"),
    "金星": ("planet", "行星位置"),
    "火星": ("planet", "行星位置"),
    "木星": ("planet", "行星位置"),
    "土星": ("planet", "行星位置"),
    "天王星": ("planet", "行星位置"),
    "海王星": ("planet", "行星位置"),
    "冥王星": ("planet", "行星位置"),
    "planet": ("planet", "行星位置"),
    "大距": ("planet", "行星位置"),
    "衝":   ("planet", "行星位置"),
    "合月": ("planet", "行星位置"),
    "凌日": ("planet", "行星位置"),
    "日食": ("eclipse", "日食／月食預測"),
    "月食": ("eclipse", "日食／月食預測"),
    "日蝕": ("eclipse", "日食／月食預測"),
    "月蝕": ("eclipse", "日食／月食預測"),
    "全食": ("eclipse", "日食／月食預測"),
    "偏食": ("eclipse", "日食／月食預測"),
    "環食": ("eclipse", "日食／月食預測"),
    "食既": ("eclipse", "日食／月食預測"),
    "生光": ("eclipse", "日食／月食預測"),
    "eclipse": ("eclipse", "日食／月食預測"),
}

COMET_KEYWORDS = ["彗星", "comet", "atlas", "紫金山"]
UNKNOWN_COMET_PATTERN = re.compile(r'\b[CPDXIcp]/\d{4}\b', re.IGNORECASE)

def check_unsupported(user_query: str, intent: dict) -> dict:
    query_lower = user_query.lower()
    targets_lower = [t.lower() for t in intent.get("targets", [])]
    all_text = query_lower + " " + " ".join(targets_lower)
    unsupported_labels = []
    for keyword, (ktype, label) in UNSUPPORTED_KEYWORDS.items():
        if keyword in all_text:
            if label not in unsupported_labels:
                unsupported_labels.append(label)
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
    moon_info = get_moon_info(observer, query_dates)
    dark_windows_by_date = {m["date"]: m["dark_windows"] for m in moon_info}
    matched_targets = match_targets(intent.get("targets", []))
    is_galaxy_query = any(t.get("type") == "galaxy" for t in matched_targets)
    all_windows = []
    for target in matched_targets:
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
    today = date.today()
    max_forecast = today + timedelta(days=15)
    all_windows_out_of_range = all(d > max_forecast for d in query_dates)
    mw_composition_by_date = {}
    if is_galaxy_query:
        for m in moon_info:
            d = m["date"]
            comp = get_milky_way_composition(observer, d, m["dark_windows"])
            if comp:
                mw_composition_by_date[d] = comp
    cloud_values = [v["cloud_cover"] for v in weather.values() if v.get("cloud_cover", -1) >= 0]
    avg_cloud_cover = round(sum(cloud_values) / len(cloud_values), 1) if cloud_values else -1
    vis_values = [v["visibility_km"] for v in weather.values() if v.get("visibility_km", -1) >= 0]
    avg_visibility_km = round(sum(vis_values) / len(vis_values), 1) if vis_values else -1
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
        "is_galaxy_query":           is_galaxy_query,
        "all_windows_out_of_range":  all_windows_out_of_range,
        "avg_cloud_cover":           avg_cloud_cover,
        "avg_visibility_km":         avg_visibility_km,
        "avg_seeing":                avg_seeing,
        "avg_transparency":          avg_transparency,
    }


# ── 回覆生成 ───────────────────────────────────────────────────

def _format_time(dt):
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

    windows_for_llm = good if good else sorted(
        all_wins, key=lambda w: w.get("alt_deg", 0), reverse=True
    )[:10]
    weather_fallback = (not good) and bool(all_wins)

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

    is_galaxy_query = result.get("is_galaxy_query", False)
    if is_galaxy_query:
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
    else:
        mw_str = None

    ss = json.dumps([{
        "流星雨": s["name"], "距極大期": f"{s['days_to_peak']:+d}天",
        "ZHR": s["zenithal_hourly_rate"]
    } for s in showers], ensure_ascii=False) if showers else "無"

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
    elif weather_status == "unknown":
        weather_instruction = """
⚠️ 氣象資料暫時無法取得（API 回傳異常或資料缺失）。
規則：
- 【結論】開頭標註「氣象資料暫時無法取得，以下僅供天文參考」
- 【氣象分析】說明資料無法取得，建議另行查詢中央氣象署或 Windy
- 天文計算（月相、暗空窗口、仰角）照常呈現
- 天況相關欄位以 N/A 表示，不做天況好壞評估"""
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

{'''【銀河構圖方位】（天況極差時可省略）
  - 銀河核心方位角＋中文方向＋仰角
  - 月亮相對位置
  - 具體構圖建議（鏡頭焦段、前景選擇）

''' if is_galaxy_query else ''}【氣象分析】
  - 雲量：夜間平均 X%
  - 能見度：平均 X km
  - 結露風險：溫度/露點差，是否需要加熱帶
  - 若有視寧度與透明度（7Timer，1=最佳 8=最差），簡要評估對星點清晰度的影響

【裝備提醒】針對地點高度、溫度、交通特性給出具體建議
  曝光建議（天文條件合適時提供）：
  - 快門：500 法則（500 ÷ 焦距 = 最長曝光秒數，有赤道儀可延長至 2～4 倍）
  - ISO：新月期建議 1600～3200；眉月／下弦月建議 800～1600；明顯月光時降至 400～800
  - 光圈：盡量全開（f/1.4～f/2.8）以收集最多星光；f/4 以上星點更銳利但需提高 ISO 補償

若有流星雨加【流星雨加碼】

核心原則：
- 天文數據（仰角、方位角、月出月落）來自精確計算，如實呈現
- 氣象判斷只根據提供的數據，不自行假設
- 天況不佳時主動建議替代方案（改期、換地點、轉攻其他題材）
- 總長不超過 500 字"""

    return call_openrouter(
        system,
        (
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
            + (f"銀河構圖資訊：\n{mw_str}\n\n" if mw_str is not None else "")
            + f"流星雨：{ss}"
        ),
        max_tokens=1000,
    )


# ── LINE Bot 狀態管理 ─────────────────────────────────────────
# user_state:      {user_id: "waiting_wish"}
# user_last_query: {user_id: "上次查詢文字"}
# user_wish_text:  {user_id: "自動許願文字"}

user_state      = {}
user_last_query = {}
user_wish_text  = {}


def make_feedback_quick_reply():
    return QuickReply(items=[
        QuickReplyButton(action=PostbackAction(label="👍 氣象準確", data="rate_good")),
        QuickReplyButton(action=PostbackAction(label="👎 氣象不準", data="rate_bad")),
        QuickReplyButton(action=PostbackAction(label="💡 許願 / 建議", data="wish")),
    ])


def make_unsupported_quick_reply():
    return QuickReply(items=[
        QuickReplyButton(action=PostbackAction(label="💡 加入許願池", data="wish_auto")),
        QuickReplyButton(action=PostbackAction(label="略過", data="wish_skip")),
    ])


def get_display_name(user_id):
    try:
        profile = line_bot_api.get_profile(user_id)
        return profile.display_name or "朋友"
    except Exception as e:
        print(f"[LINE profile 錯誤] {type(e).__name__}: {e}", flush=True)
        return "朋友"


def process_and_reply(user_id, text, mark_as_read_token=""):
    """
    背景執行緒：執行天文計算後以 push_message 回傳結果。
    reply_token 30 秒過期，長時間計算須改用 push_message。
    """
    username = get_display_name(user_id)
    try:
        intent_for_check = parse_intent(text)
        scope = check_unsupported(text, intent_for_check)

        if scope["has_unsupported"]:
            labels = "、".join(scope["unsupported_labels"])
            notice = (
                f"⚠️ 目前版本尚不支援：{labels}\n\n"
                f"很抱歉，這個查詢超出目前的功能範圍。\n"
                f"想把這個需求加入許願池，讓我們優先開發嗎？"
            )
            user_wish_text[user_id] = scope["wish_text"]
            user_last_query[user_id] = text
            safe_push_message(user_id, TextSendMessage(
                text=notice,
                quick_reply=make_unsupported_quick_reply()
            ), "unsupported notice")
            mark_message_as_read(mark_as_read_token)
            print(f"[攔截] 不支援查詢：{labels}", flush=True)
            return

        result = run_query(text, prefetched_intent=intent_for_check)
        reply  = generate_reply(result)
        user_last_query[user_id] = text
        log_query(username, user_id, text, result["intent"])

        if scope["has_comet_warning"]:
            comet_notice = (
                "\n\n⚠️ 彗星座標說明：目前使用近似固定座標，不反映每日實際位置，"
                "僅供參考。如需即時座標，歡迎加入許願池催促我們升級！"
            )
            user_wish_text[user_id] = scope["wish_text"]
            safe_push_message(user_id, TextSendMessage(
                text=reply + comet_notice,
                quick_reply=make_feedback_quick_reply()
            ), "query reply with comet notice")
        else:
            safe_push_message(user_id, TextSendMessage(
                text=reply,
                quick_reply=make_feedback_quick_reply()
            ), "query reply")
        mark_message_as_read(mark_as_read_token)
        print("[回覆] 完成", flush=True)

    except Exception as e:
        log_unhandled_exception("process_and_reply", e)
        safe_push_message(user_id, TextSendMessage(
            text=f"⚠️ 發生錯誤，請重新嘗試。\n{type(e).__name__}: {e}"
        ), "error reply")
        print(f"[錯誤] {type(e).__name__}: {e}", flush=True)


# ── LINE Webhook 處理 ─────────────────────────────────────────

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    except LineBotApiError as e:
        print(f"[LINE API 錯誤] callback: {summarize_line_api_error(e)}", flush=True)
        return "OK"
    except Exception as e:
        body_preview = body[:500].replace("\n", "\\n")
        print(f"[callback 未預期錯誤] body={body_preview}", flush=True)
        log_unhandled_exception("callback", e)
        return "OK"
    return "OK"


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    text    = event.message.text.strip()
    mark_as_read_token = extract_mark_as_read_token(event)
    print(f"[收到] {user_id}: {text}", flush=True)

    # 許願等待狀態
    if user_state.get(user_id) == "waiting_wish":
        user_state.pop(user_id, None)
        username = get_display_name(user_id)
        last_q   = user_last_query.get(user_id, "")
        saved = log_wish(username, user_id, last_q, text, "許願")
        safe_reply_message(event.reply_token, TextSendMessage(
            text="謝謝你的建議！💡 已記錄到許願池 🙏" if saved else "⚠️ 建議已收到，但寫入 Google Sheet 失敗，請稍後再試。"
        ), "wish reply")
        mark_message_as_read(mark_as_read_token)
        print(f"[許願] {username}: {text}", flush=True)
        return

    # 直接以文字送出的許願/建議。這可補上 Render 重啟造成 waiting_wish 記憶體遺失的情境。
    if is_direct_wish_text(text):
        username = get_display_name(user_id)
        last_q = user_last_query.get(user_id, "")
        saved = log_wish(username, user_id, last_q, text, "許願（文字）")
        safe_reply_message(event.reply_token, TextSendMessage(
            text="謝謝你的建議！💡 已記錄到許願池 🙏" if saved else "⚠️ 建議已收到，但寫入 Google Sheet 失敗，請稍後再試。"
        ), "direct wish reply")
        mark_message_as_read(mark_as_read_token)
        print(f"[許願-文字] {username}: {text}", flush=True)
        return

    # 說明指令
    if text in ["/start", "/help", "help", "說明"]:
        safe_reply_message(event.reply_token, TextSendMessage(
            text=(
                "🔭 天文攝影查詢 Bot\n\n"
                "直接用自然語言問我，例如：\n"
                "・4月15日 合歡山 銀河\n"
                "・這個週末 阿里山 有什麼可以拍？\n"
                "・5月1日到3日 墾丁 天蠍座\n\n"
                "我會幫你計算最佳觀測時刻、月亮暗空窗口、銀河構圖方位和氣象條件 🌌"
            )
        ), "help reply")
        mark_message_as_read(mark_as_read_token)
        return

    # 一般查詢：立即回應「計算中」，背景執行運算
    if not safe_reply_message(event.reply_token, TextSendMessage(
        text="🔭 計算中，請稍候（約 30～60 秒）..."
    ), "initial query reply"):
        return
    mark_message_as_read(mark_as_read_token)
    thread = threading.Thread(
        target=process_and_reply,
        args=(user_id, text, mark_as_read_token),
        daemon=True,
    )
    thread.start()


@handler.add(PostbackEvent)
def handle_postback(event):
    user_id  = event.source.user_id
    data     = event.postback.data
    username = get_display_name(user_id)
    last_q   = user_last_query.get(user_id, "")

    if data == "rate_good":
        log_feedback(username, user_id, last_q, "👍", "評分")
        safe_reply_message(event.reply_token, TextSendMessage(
            text="謝謝你的回饋！👍 已記錄"
        ), "good rating reply")
    elif data == "rate_bad":
        log_feedback(username, user_id, last_q, "👎", "評分")
        safe_reply_message(event.reply_token, TextSendMessage(
            text="謝謝你的回饋！👎 已記錄，我們會繼續改進"
        ), "bad rating reply")
    elif data == "wish":
        user_state[user_id] = "waiting_wish"
        safe_reply_message(event.reply_token, TextSendMessage(
            text="💡 請說說你的建議或想新增的功能。建議用「建議：...」開頭，這樣即使服務重啟也能被記錄。"
        ), "wish prompt reply")
    elif data == "wish_auto":
        wish = user_wish_text.get(user_id, last_q)
        log_wish(username, user_id, last_q, wish, "許願（自動）")
        safe_reply_message(event.reply_token, TextSendMessage(
            text="💡 已加入許願池！謝謝你的支持，我們會優先考慮開發 🙏"
        ), "auto wish reply")
        print(f"[許願-自動] {username}: {wish}", flush=True)
    elif data == "wish_skip":
        safe_reply_message(event.reply_token, TextSendMessage(text="好的 👍"), "wish skip reply")


# ── 主程式 ────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 LINE Bot 啟動中（port {port}）...", flush=True)
    app.run(host="0.0.0.0", port=port)
