import hashlib, http.client, requests, json, re, logging, os, traceback
from concurrent.futures import ThreadPoolExecutor

from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from skyfield.api import wgs84
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
DEFAULT_OPENROUTER_MODEL = "anthropic/claude-sonnet-4.5"
OPENROUTER_MODEL_SOURCE = "OPENROUTER_MODEL" if os.environ.get("OPENROUTER_MODEL", "").strip() else "default"
OPENROUTER_MODEL     = os.environ.get("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)
OPENROUTER_FALLBACK_MODELS = os.environ.get("OPENROUTER_FALLBACK_MODELS", "openai/gpt-4o-mini")
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
        ws_query = sh.add_worksheet("查詢記錄", rows=1000, cols=12)
        ws_query.append_row(["時間","用戶名","用戶ID","查詢內容","地點","日期區間","標的","類型","資料品質摘要","資料品質JSON"])
    try:
        headers = ws_query.row_values(1)
        if len(headers) < 9 or headers[8] != "資料品質摘要":
            ws_query.update_cell(1, 9, "資料品質摘要")
        if len(headers) < 10 or headers[9] != "資料品質JSON":
            ws_query.update_cell(1, 10, "資料品質JSON")
    except Exception as e:
        print(f"[Sheets 警告] 查詢記錄欄位檢查失敗：{describe_exception(e)}", flush=True)
    try:
        ws_feedback = sh.worksheet("用戶反饋")
    except gspread.WorksheetNotFound:
        ws_feedback = sh.add_worksheet("用戶反饋", rows=1000, cols=3)
        ws_feedback.append_row(["日期及時間","Line User Name","建議事項的內容"])
    try:
        ws_locations = sh.worksheet("自定義地點")
    except gspread.WorksheetNotFound:
        ws_locations = sh.add_worksheet("自定義地點", rows=500, cols=5)
        ws_locations.append_row(["地點名稱", "緯度", "經度", "新增時間", "原始查詢"])
    return ws_query, ws_feedback, ws_locations

try:
    ws_query, ws_feedback, ws_locations = init_sheets()
    print("✅ Google Sheets 連線成功", flush=True)
except Exception as e:
    print(f"⚠️ Google Sheets 連線失敗：{describe_exception(e)}", flush=True)
    ws_query = ws_feedback = ws_locations = None


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
        "openrouter_model_source": OPENROUTER_MODEL_SOURCE,
        "openrouter_model_default": DEFAULT_OPENROUTER_MODEL,
        "openrouter_fallback_models": openrouter_model_sequence(),
        "line_token_configured": bool(LINE_ACCESS_TOKEN),
        "line_token_length": len(LINE_ACCESS_TOKEN or ""),
        "line_token_fingerprint": fingerprint_line_access_token(),
        "line_token_probe": LINE_API_PROBE_STATUS,
        "google_sheets_connected": ws_query is not None and ws_feedback is not None,
        "spreadsheet_id": SPREADSHEET_ID,
    })

def log_query(username, user_id, query, intent, data_quality=None):
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
            format_data_quality_for_log(data_quality or {}),
            json.dumps(data_quality or {}, ensure_ascii=False),
        ])
    except Exception as e:
        print(f"[Sheets 錯誤] {describe_exception(e)}", flush=True)

    # A2：未命中目標自動寫入用戶反饋（讓開發者知道哪些標的缺資料）
    unmatched = (data_quality or {}).get("celestial_positions", {}).get("unmatched_targets", [])
    if unmatched and ws_feedback:
        try:
            ws_feedback.append_row([
                datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
                username,
                f"【未命中標的】{', '.join(unmatched)}（查詢：{query}）",
            ])
            print(f"[Sheets] 已記錄未命中目標：{unmatched}", flush=True)
        except Exception as e:
            print(f"[Sheets 錯誤] 未命中目標記錄失敗：{describe_exception(e)}", flush=True)

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

def is_likely_new_query(text):
    query_keywords = ("適合", "可以", "能不能", "可不可以", "銀河", "星", "拍", "今晚", "明天", "後天", "週末")
    return bool(find_known_location_in_query(text)) or any(keyword in text for keyword in query_keywords)

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

# ── 拆分模組（Phase 3 前置重構）───────────────────────────────
# 標的資料（targets）、天文計算（astro）、氣象 API（weather）、CCI（cci）
# 已拆至獨立模組；main.py 保留 Flask/LINE webhook、意圖解析、地點資料、
# 回覆組裝與最佳地點排名。
from targets import TARGET_LIBRARY, METEOR_SHOWERS, MILKY_WAY_CORE
from astro import (
    ts, eph,
    az_to_direction, get_moon_phase_emoji, check_meteor_shower,
    get_astronomical_twilight, get_moon_rise_set, compute_dark_sky_window,
    get_milky_way_composition, compute_target_windows, get_moon_info,
)
from weather import wind_kmh_to_beaufort, check_weather_multi, get_7timer_seeing
from cci import _moon_illumination, compute_cci_for_date

DEFAULT_KNOWN_LOCATIONS = {
    "日月潭": (23.865, 120.917),
    "合歡山": (24.167, 121.283),
    "外澳": (24.870, 121.862),
    "墾丁": (21.945, 120.803),
    "阿里山": (23.517, 120.800),
    "嘉明湖": (23.250, 121.000),
    "武陵農場": (24.367, 121.367),
    "太平山": (24.517, 121.617),
    "七星山": (25.167, 121.533),
    "清境農場": (24.083, 121.167),
    "奧萬大": (23.850, 121.083),
    "桃源谷": (25.100, 121.867),
    "池上": (23.124, 121.216),
}

LOCATION_DATA_PATH = Path(__file__).resolve().parent / "data" / "taiwan_locations.json"

def coerce_float(value):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned:
            return float(cleaned)
    raise ValueError(f"not a number: {value!r}")

def load_location_data():
    try:
        with LOCATION_DATA_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as e:
        print(f"[地點資料警告] 無法載入 {LOCATION_DATA_PATH}: {type(e).__name__}: {e}", flush=True)
        return {
            name: {
                "lat": lat,
                "lon": lon,
                "aliases": [],
                "source": "legacy-fallback",
                "confidence": "high",
                "review_status": "approved",
            }
            for name, (lat, lon) in DEFAULT_KNOWN_LOCATIONS.items()
        }
    approved = {}
    for name, item in data.items():
        if item.get("review_status") != "approved":
            continue
        try:
            approved[name] = {
                **item,
                "lat": coerce_float(item.get("lat")),
                "lon": coerce_float(item.get("lon")),
                "aliases": item.get("aliases") or [],
            }
        except (TypeError, ValueError) as e:
            print(f"[地點資料警告] 略過無效地點 {name}: {e}", flush=True)
    return approved or load_location_data_fallback()

def load_location_data_fallback():
    return {
        name: {
            "lat": lat,
            "lon": lon,
            "aliases": [],
            "source": "legacy-fallback",
            "confidence": "high",
            "review_status": "approved",
        }
        for name, (lat, lon) in DEFAULT_KNOWN_LOCATIONS.items()
    }

LOCATION_DATA = load_location_data()
KNOWN_LOCATIONS = {name: (item["lat"], item["lon"]) for name, item in LOCATION_DATA.items()}

def load_custom_locations():
    """啟動時從 Google Sheets「自定義地點」載入用戶提供的地點，合併進 LOCATION_DATA。"""
    if not ws_locations:
        return
    try:
        rows = ws_locations.get_all_values()
        if len(rows) <= 1:
            return  # 只有標題列
        for row in rows[1:]:
            if len(row) < 3:
                continue
            name, lat_str, lon_str = row[0].strip(), row[1].strip(), row[2].strip()
            if not name or name in LOCATION_DATA:
                continue
            try:
                lat, lon = coerce_float(lat_str), coerce_float(lon_str)
            except ValueError:
                continue
            LOCATION_DATA[name] = {
                "lat": lat, "lon": lon, "aliases": [],
                "source": "user-provided", "confidence": "user",
                # 用戶提供座標未經人工審核：可用於該地點的直接查詢，
                # 但不進入最佳地點排名與意圖解析目錄（地點審核制）
                "review_status": "pending",
            }
            KNOWN_LOCATIONS[name] = (lat, lon)
        print(f"[自定義地點] 已載入 {len(rows)-1} 筆用戶地點（pending，未進排名）", flush=True)
    except Exception as e:
        print(f"[自定義地點] 載入失敗：{describe_exception(e)}", flush=True)

def save_custom_location(name, lat, lon, original_query=""):
    """將用戶提供座標的新地點存入 Sheets 並更新記憶體。"""
    if name in LOCATION_DATA:
        existing_lat, existing_lon = KNOWN_LOCATIONS.get(name, (None, None))
        if existing_lat == lat and existing_lon == lon:
            return  # 完全相同，不重複儲存
    LOCATION_DATA[name] = {
        "lat": lat, "lon": lon, "aliases": [],
        "source": "user-provided", "confidence": "user",
        # 未經人工審核：不進排名與意圖解析目錄，需在 taiwan_locations.json 補審後才 approved
        "review_status": "pending",
    }
    KNOWN_LOCATIONS[name] = (lat, lon)
    if ws_locations:
        try:
            ts_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
            ws_locations.append_row([name, str(lat), str(lon), ts_str, original_query[:100]])
            print(f"[自定義地點] 已儲存：{name} ({lat}, {lon})", flush=True)
        except Exception as e:
            print(f"[自定義地點] 儲存失敗：{describe_exception(e)}", flush=True)

load_custom_locations()  # 啟動時載入用戶自定義地點

def location_search_terms(name, item):
    return [name] + [alias for alias in item.get("aliases", []) if alias]

def location_prompt_catalog():
    # 只提供 approved 地點給意圖解析 LLM（pending 用戶地點不進 prompt，
    # 避免未審核座標被擴散到其他用戶的查詢）
    entries = []
    for name, item in LOCATION_DATA.items():
        if item.get("review_status") != "approved":
            continue
        entries.append(f"{name}({item['lat']:.3f},{item['lon']:.3f})")
    return "，".join(entries)

def google_maps_url(lat, lon):
    return f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}"

def format_location_resolution(intent, original_query):
    location_name = intent.get("location_name", "未知地點")
    lat = coerce_float(intent.get("lat"))
    lon = coerce_float(intent.get("lon"))
    item = LOCATION_DATA.get(location_name, {})
    source = item.get("source", "user_supplied_coordinates")
    confidence = item.get("confidence", "unknown")
    return (
        "【地點解析】\n"
        f"你輸入：{original_query}\n"
        f"解析地點：{location_name}\n"
        f"座標：{lat:.6f}, {lon:.6f}\n"
        f"Google Maps：{google_maps_url(lat, lon)}\n"
        f"資料來源：{source}｜信心：{confidence}"
    )

TAIWAN_LOOSE_LAT_RANGE = (20.0, 26.5)
TAIWAN_LOOSE_LON_RANGE = (118.0, 123.8)
AMBIGUOUS_LOCATION_TERMS = {"飛行場", "機場", "山上", "海邊", "海岸", "湖邊"}

class IntentParseError(RuntimeError):
    """LLM 意圖解析失敗（重試後仍無法取得合法 JSON）。"""
    pass


class LocationResolutionError(RuntimeError):
    def __init__(self, location_name, intent, message):
        super().__init__(message)
        self.location_name = location_name
        self.intent = intent

def extract_location_hint(user_query):
    text = user_query.strip()
    text = re.sub(r"^(今天|今晚|明天|後天|這個週末|週末|下週[一二三四五六日天]?)", "", text).strip()
    match = re.search(r"(.+?)(?:適合|可以|能不能|可不可以|有沒有|好不好|能拍|拍)", text)
    if not match:
        return ""
    hint = match.group(1).strip(" ，,。？?：:")
    hint = re.sub(r"^(在|去|到)", "", hint).strip()
    return hint

def location_name_matches_query(location_name, user_query):
    if not location_name:
        return False
    hint = extract_location_hint(user_query)
    if location_name in user_query:
        return True
    if hint and (hint in location_name or location_name in hint):
        return True
    return False

def is_ambiguous_location(location_name, user_query):
    hint = extract_location_hint(user_query)
    candidates = {location_name, hint}
    return any(candidate in AMBIGUOUS_LOCATION_TERMS for candidate in candidates if candidate)

def find_known_location_in_query(user_query):
    candidates = []
    for name, item in LOCATION_DATA.items():
        for term in location_search_terms(name, item):
            candidates.append((term, name))
    for term, name in sorted(candidates, key=lambda pair: len(pair[0]), reverse=True):
        if term in user_query:
            return name
    return ""

def extract_compare_locations_from_text(user_query):
    """Split on compare keywords and find one location per half; returns (name_a, name_b) or (None, None)."""
    for sep in ["vs", "VS", "Vs", "還是", "比較", "對比", "哪裡比較好", "哪個好"]:
        if sep in user_query:
            idx = user_query.index(sep)
            left  = user_query[:idx]
            right = user_query[idx + len(sep):]
            loc_a = find_known_location_in_query(left)
            loc_b = find_known_location_in_query(right)
            if loc_a and loc_b and loc_a != loc_b:
                return loc_a, loc_b
    return None, None

def extract_inline_coordinate_location_name(user_query, fallback_name=""):
    """Best-effort place name for queries like '南橫啞口 23.264, 120.961'."""
    prefix = re.split(r"-?\d+(?:\.\d+)?", user_query, maxsplit=1)[0]
    prefix = re.sub(
        r"(今天|今晚|明天|後天|這個週末|週末|下週[一二三四五六日天]?)",
        " ",
        prefix,
    )
    prefix = re.sub(r"(拍|觀測|適合|可以|能不能|可不可以|有沒有|好不好|查詢|搜尋|看)", " ", prefix)
    prefix = re.sub(r"(銀河|星座|星雲|星系|流星雨|彗星|月亮|月景)", " ", prefix)
    prefix = re.sub(r"\s+", " ", prefix).strip(" ，,。？?：:")
    if prefix:
        return normalize_location_name_text(prefix)

    text = re.sub(
        r"(?:lat(?:itude)?|緯度|北緯|座標)?\s*[=:：]?\s*-?\d+(?:\.\d+)?\s*[,，、]\s*"
        r"(?:lon(?:gitude)?|lng|經度|東經)?\s*[=:：]?\s*-?\d+(?:\.\d+)?",
        " ",
        user_query,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(今天|今晚|明天|後天|這個週末|週末|下週[一二三四五六日天]?)",
        " ",
        text,
    )
    for target in TARGET_LIBRARY:
        text = text.replace(target["name"], " ")
    text = re.sub(r"(銀河|星座|星雲|星系|流星雨|彗星|月亮|月景)", " ", text)
    text = re.sub(r"(拍|觀測|適合|可以|能不能|可不可以|有沒有|好不好|查詢|搜尋|看)", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ，,。？?：:")
    return normalize_location_name_text(text or fallback_name or "自訂座標")

def normalize_location_name_text(location_name):
    normalized = str(location_name or "").strip()
    typo_map = {
        "南橫亞口": "南橫啞口",
    }
    return typo_map.get(normalized, normalized)

def apply_inline_coordinates(intent, user_query, fallback_name=""):
    coordinates = extract_user_coordinates(user_query)
    if not coordinates:
        return None
    updated = dict(intent or {})
    updated["lat"], updated["lon"] = coordinates
    updated["location_name"] = extract_inline_coordinate_location_name(user_query, fallback_name)
    save_custom_location(
        updated["location_name"],
        updated["lat"], updated["lon"],
        original_query=user_query,
    )
    return updated

def normalize_intent(intent, user_query):
    if not isinstance(intent, dict):
        raise RuntimeError("意圖解析結果格式錯誤，請重新輸入查詢。")

    location_name = str(intent.get("location_name") or "").strip()
    try:
        supplied_coordinates = extract_user_coordinates(user_query)
    except ValueError as e:
        raise LocationResolutionError(location_name, intent, str(e)) from e

    if supplied_coordinates:
        return apply_inline_coordinates(intent, user_query, location_name)

    known_location = find_known_location_in_query(user_query)
    if known_location:
        lat, lon = KNOWN_LOCATIONS[known_location]
        intent["location_name"] = known_location
        intent["lat"] = lat
        intent["lon"] = lon
    else:
        for name, (lat, lon) in KNOWN_LOCATIONS.items():
            if name == location_name and location_name_matches_query(location_name, user_query):
                intent["location_name"] = name
                intent["lat"] = lat
                intent["lon"] = lon
                break
        else:
            if is_ambiguous_location(location_name, user_query):
                location_hint = extract_location_hint(user_query) or location_name
                intent["location_name"] = location_hint
                raise LocationResolutionError(
                    location_hint,
                    intent,
                    f"地點名稱過於籠統，無法可靠解析座標：{location_hint}"
                )
            elif location_name and not location_name_matches_query(location_name, user_query):
                location_hint = extract_location_hint(user_query) or location_name
                intent["location_name"] = location_hint
                raise LocationResolutionError(
                    location_hint,
                    intent,
                    f"地點解析不可信：使用者輸入像是「{location_hint}」，但解析結果是「{location_name}」。"
                )

    try:
        intent["lat"] = coerce_float(intent.get("lat"))
        intent["lon"] = coerce_float(intent.get("lon"))
    except (TypeError, ValueError) as e:
        raise LocationResolutionError(
            location_name,
            intent,
            f"無法解析地點座標：{location_name or '未指定地點'}。"
        ) from e

    if not (-90 <= intent["lat"] <= 90 and -180 <= intent["lon"] <= 180):
        raise LocationResolutionError(
            location_name,
            intent,
            f"地點座標超出全球合法範圍：lat={intent['lat']}, lon={intent['lon']}"
        )

    return intent

def resolve_compare_location(loc_dict):
    """比較模式專用：解析單一地點 dict，回傳 (name, lat, lon) 或拋 LocationResolutionError。"""
    name = str(loc_dict.get("name") or "").strip()
    lat  = loc_dict.get("lat")
    lon  = loc_dict.get("lon")
    if name in KNOWN_LOCATIONS:
        lat, lon = KNOWN_LOCATIONS[name]
        return name, lat, lon
    for kname, (klat, klon) in KNOWN_LOCATIONS.items():
        if name.lower() == kname.lower():
            return kname, klat, klon
    for kname, item in LOCATION_DATA.items():
        for alias in item.get("aliases", []):
            if name == alias or name.lower() == alias.lower():
                return kname, item["lat"], item["lon"]
    try:
        lat = coerce_float(lat)
        lon = coerce_float(lon)
        if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
            return name, lat, lon
    except (TypeError, ValueError):
        pass
    raise LocationResolutionError(
        name, {},
        f"比較地點「{name}」不在審核地點資料庫，比較模式目前只支援已審核地點"
    )


def is_in_taiwan_loose_range(lat, lon):
    return (
        TAIWAN_LOOSE_LAT_RANGE[0] <= lat <= TAIWAN_LOOSE_LAT_RANGE[1]
        and TAIWAN_LOOSE_LON_RANGE[0] <= lon <= TAIWAN_LOOSE_LON_RANGE[1]
    )

def extract_user_coordinates(text):
    normalized = text.strip()
    patterns = [
        r"(?:lat(?:itude)?|緯度|北緯)\s*[=:：]?\s*(-?\d+(?:\.\d+)?)\D+"
        r"(?:lon(?:gitude)?|lng|經度|東經)\s*[=:：]?\s*(-?\d+(?:\.\d+)?)",
        r"(-?\d+\.\d+)\s*[,，、]\s*(-?\d+\.\d+)",
        r"(-?\d+\.\d+)\s+(-?\d+\.\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        first = float(match.group(1))
        second = float(match.group(2))
        if 118.0 <= first <= 123.8 and 20.0 <= second <= 26.5:
            lat, lon = second, first
        else:
            lat, lon = first, second
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(f"座標超出全球合法範圍：lat={lat}, lon={lon}")
        return lat, lon
    numbers = re.findall(r"-?\d+\.\d+", normalized)
    for idx in range(len(numbers) - 1):
        first = float(numbers[idx])
        second = float(numbers[idx + 1])
        if 118.0 <= first <= 123.8 and 20.0 <= second <= 26.5:
            return second, first
        if -90 <= first <= 90 and -180 <= second <= 180:
            return first, second
    return None

def location_coordinate_prompt(location_name):
    place = location_name or "這個地點"
    return (
        f"我目前無法在地點資料庫中穩定解析「{place}」的座標。\n"
        "我已主動將這個地點列入地點許願池，後續可補進資料庫。\n\n"
        "請回覆經緯度，我會接續剛剛的查詢繼續計算。\n"
        "格式範例：\n"
        "座標：23.124, 121.216\n"
        "或：北緯 23.124 東經 121.216\n\n"
        "緯度需在 -90～90，經度需在 -180～180。"
    )

def parse_intent(user_query):
    today_str = date.today().isoformat()
    system = f"""你是天文攝影查詢系統的意圖解析器。今天是 {today_str}。
以 JSON 回覆，不加說明或 markdown。

【標準查詢】（預設）：
{{"query_type":"A或B","compare_mode":false,"location_name":"地名","lat":緯度,"lon":經度,
"date_start":"YYYY-MM-DD","date_end":"YYYY-MM-DD","targets":[],"extra_notes":""}}

【地點比較查詢】用戶用「vs」「還是」「比較」「哪裡比較好」「哪個好」「對比」明確比較兩個地點時：
{{"query_type":"A或B","compare_mode":true,
"locations":[{{"name":"地點A","lat":緯度A,"lon":經度A}},{{"name":"地點B","lat":緯度B,"lon":經度B}}],
"location_name":"地點A","lat":緯度A,"lon":經度A,
"date_start":"YYYY-MM-DD","date_end":"YYYY-MM-DD","targets":[],"extra_notes":""}}
注意：只有明確比較兩個具體地點才設 compare_mode=true；「台北去合歡山」不是比較，不設 true。
句型舉例：「是鳶峰好還是日月潭好」→ locations=[{{name:"鳶峰",...}},{{name:"日月潭",...}}]；「是」字開頭是強調語氣，不是地名，第一個地名緊接在「是」之後。
locations 兩個 name 必須不同；如果兩個地點相同，表示解析錯誤，請重新仔細讀查詢。

query_type：A=有具體天體（銀河/獵戶座/M42等），B=開放探索
日期：「這個週末」→最近週六日；具體日期年份用{today_str[:4]}；未指定範圍則首尾同日
已審核地名座標：{location_prompt_catalog()}。
若地名不在清單，請只解析使用者實際輸入的地點，不可替換成清單中的其他地點。
若地名太籠統或無法可靠估算，location_name 保留使用者輸入的地名，lat/lon 回 null。"""
    last_error = None
    for attempt in range(2):
        prompt = user_query if attempt == 0 else (
            f"{user_query}\n\n（上次回覆不是合法 JSON。請只回覆一個合法 JSON 物件，"
            "不加任何說明、前後綴或 markdown。）"
        )
        try:
            text = call_openrouter(system, prompt, max_tokens=400,
                                   temperature=0.2 if attempt == 0 else 0.0)
            text = re.sub(r"```(?:json)?|```", "", text.strip()).strip()
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ValueError(f"意圖解析結果不是 JSON 物件：{type(parsed).__name__}")
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            print(f"[意圖解析] 第 {attempt + 1} 次解析失敗：{describe_exception(e)}", flush=True)
            continue
        return normalize_intent(parsed, user_query)
    raise IntentParseError("意圖解析失敗：LLM 未回傳合法 JSON") from last_error


_M_NUM_RE = re.compile(r'^m\d+$')

def _target_matches(query_name: str, target: dict) -> bool:
    """回傳 True 若 query_name 與 target 的名稱或任何 alias 匹配。
    規則：
    - M 編號（如 m2、m20）使用數字邊界比對，避免 m2 誤中 m20。
    - 其他文字使用雙向 substring 比對。
    """
    q = query_name.lower().strip()
    all_names = [target["name"]] + target.get("aliases", [])
    for n in all_names:
        n_l = n.lower().strip()
        if q == n_l:
            return True
        if _M_NUM_RE.match(n_l):
            # n_l 是純 M 編號：要求在 query 中出現且前後不接數字
            if re.search(r'(?<!\d)' + re.escape(n_l) + r'(?!\d)', q):
                return True
        elif _M_NUM_RE.match(q):
            # query 是純 M 編號：要求在 n_l 中出現且前後不接數字
            if re.search(r'(?<!\d)' + re.escape(q) + r'(?!\d)', n_l):
                return True
        else:
            if q in n_l or n_l in q:
                return True
    return False

def match_targets(target_names):
    if not target_names:
        return TARGET_LIBRARY
    matched = []
    for name in target_names:
        for t in TARGET_LIBRARY:
            if _target_matches(name, t) and t not in matched:
                matched.append(t)
    return matched

def find_unmatched_targets(target_names, matched_targets):
    if not target_names:
        return []
    unmatched = []
    for name in target_names:
        found = any(_target_matches(name, t) for t in matched_targets)
        if not found:
            unmatched.append(name)
    return unmatched

def determine_wind_profile(intent, matched_targets):
    if intent.get("wind_profile") in ("milky_way", "deep_sky"):
        return intent["wind_profile"]
    requested_targets = intent.get("targets", [])
    if any("深空" in t or "星雲" in t or "星系" in t for t in requested_targets):
        return "deep_sky"
    if not requested_targets:
        return "milky_way"
    if any(t.get("type") == "galaxy" or "銀河" in t.get("name", "") for t in matched_targets):
        return "milky_way"
    if any(t.get("type") in ("nebula", "comet", "cluster") for t in matched_targets):
        return "deep_sky"
    return "milky_way"

def determine_cci_profile(intent, matched_targets, showers, unsupported_info=None):
    """決定 CCI 計算使用哪個題材 profile。
    Returns: "default" | "meteor" | "moonscape" | "lunar_eclipse" | "comet_layer1"
    """
    if unsupported_info is None:
        unsupported_info = {}
    # 月蝕：明確提到月蝕關鍵字，且本系統支援第一層天況 CCI
    if unsupported_info.get("has_lunar_eclipse"):
        return "lunar_eclipse"
    # 月景：明確提到月景攝影
    if unsupported_info.get("has_moonscape"):
        return "moonscape"
    # 流星雨：查詢包含流星雨關鍵字且當期有流星雨活動
    targets_text = " ".join(intent.get("targets", []))
    raw_query = intent.get("raw_query", "")
    all_text = (targets_text + " " + raw_query).lower()
    if showers and any(kw in all_text for kw in ["流星雨", "流星", "meteor"]):
        return "meteor"
    # 彗星第一層：matched_targets 中有 comet 類型
    if any(t.get("type") == "comet" for t in matched_targets):
        return "comet_layer1"
    return "default"

def summarize_data_quality(intent, query_dates, weather, seeing_data, matched_targets, unmatched_targets):
    weather_missing = [
        {
            "date": d.isoformat(),
            "reason": weather.get(d, {}).get("missing_reason", "氣象資料缺失"),
        }
        for d in query_dates
        if weather.get(d, {}).get("data_status") != "ok"
    ]
    seeing_missing = [
        {
            "date": d.isoformat(),
            "reason": seeing_data.get(d, {}).get("missing_reason", "視寧度資料缺失"),
        }
        for d in query_dates
        if seeing_data.get(d, {}).get("data_status") != "ok"
    ]
    target_status = "ok"
    if intent.get("targets") and unmatched_targets:
        target_status = "partial" if matched_targets else "missing"
    return {
        "policy": "no_guessing_without_evidence",
        "weather": {
            "source": "Open-Meteo",
            "status": "missing" if len(weather_missing) == len(query_dates) else ("partial" if weather_missing else "ok"),
            "missing": weather_missing,
        },
        "seeing": {
            "source": "7Timer",
            "status": "missing" if len(seeing_missing) == len(query_dates) else ("partial" if seeing_missing else "ok"),
            "missing": seeing_missing,
        },
        "celestial_positions": {
            "source": "Skyfield + internal target catalog",
            "status": target_status,
            "matched_targets": [t["name"] for t in matched_targets],
            "unmatched_targets": unmatched_targets,
        },
    }

def format_data_quality_for_log(data_quality):
    issues = []
    weather = data_quality.get("weather", {})
    seeing = data_quality.get("seeing", {})
    celestial = data_quality.get("celestial_positions", {})
    location = data_quality.get("location", {})
    if location.get("status") and location.get("status") != "ok":
        issues.append(f"location:{location.get('status')}:{location.get('requested_location', '')}")
    if weather.get("status") and weather.get("status") != "ok":
        dates = ", ".join(item["date"] for item in weather.get("missing", []))
        issues.append(f"weather:{weather.get('status')}:{dates or 'none'}")
    if seeing.get("status") and seeing.get("status") != "ok":
        dates = ", ".join(item["date"] for item in seeing.get("missing", []))
        issues.append(f"seeing:{seeing.get('status')}:{dates or 'none'}")
    if celestial.get("status") and celestial.get("status") != "ok":
        targets = ", ".join(celestial.get("unmatched_targets", []))
        issues.append(f"celestial:{celestial.get('status')}:{targets or 'none'}")
    return "ok" if not issues else " | ".join(issues)


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
    "日食": ("solar_eclipse", "日食預測"),
    "日蝕": ("solar_eclipse", "日食預測"),
    # 「凌日」原重複定義於 planet 與 solar_eclipse（dict 後者生效）；
    # 移除重複、保留 solar_eclipse 分類以維持既有行為
    "凌日": ("solar_eclipse", "日食預測"),
    # 月蝕/月食已移出 hard-block，改為 cci_profile="lunar_eclipse" + 軟性提示
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
    LUNAR_ECLIPSE_KEYWORDS = ["月蝕", "月食", "全食", "偏食", "環食", "食既", "生光", "lunar eclipse"]
    has_lunar_eclipse = any(kw in all_text for kw in LUNAR_ECLIPSE_KEYWORDS)
    MOONSCAPE_KEYWORDS = ["月景", "月光攝影", "月色攝影", "moonscape"]
    has_moonscape = any(kw in all_text for kw in MOONSCAPE_KEYWORDS)
    return {
        "has_unsupported":       len(unsupported_labels) > 0,
        "has_comet_warning":     has_comet_warning,
        "has_lunar_eclipse":     has_lunar_eclipse,
        "has_moonscape":         has_moonscape,
        "unsupported_labels":    unsupported_labels,
        "wish_text":             f"希望支援：{'、'.join(unsupported_labels)}（原始查詢：{user_query}）",
    }


def run_query(user_query, prefetched_intent=None):
    intent    = normalize_intent(prefetched_intent, user_query) if prefetched_intent else parse_intent(user_query)
    observer  = wgs84.latlon(intent["lat"], intent["lon"])
    date_start = date.fromisoformat(intent["date_start"])
    date_end   = date.fromisoformat(intent["date_end"])
    query_dates = [date_start + timedelta(days=i)
                   for i in range((date_end - date_start).days + 1)]
    moon_info = get_moon_info(observer, query_dates)
    dark_windows_by_date = {m["date"]: m["dark_windows"] for m in moon_info}
    matched_targets = match_targets(intent.get("targets", []))
    unmatched_targets = find_unmatched_targets(intent.get("targets", []), matched_targets)
    wind_profile = determine_wind_profile(intent, matched_targets)
    is_galaxy_query = any(t.get("type") == "galaxy" for t in matched_targets)
    all_windows = []
    for target in matched_targets:
        all_windows.extend(
            compute_target_windows(observer, target, query_dates, dark_windows_by_date)
        )
    showers = [s for d in query_dates for s in check_meteor_shower(d)]
    unsupported_info = check_unsupported(user_query, intent)
    cci_profile = determine_cci_profile(intent, matched_targets, showers, unsupported_info)
    weather     = check_weather_multi(intent["lat"], intent["lon"], query_dates)
    seeing_data = get_7timer_seeing(intent["lat"], intent["lon"], query_dates)
    data_quality = summarize_data_quality(
        intent, query_dates, weather, seeing_data, matched_targets, unmatched_targets
    )
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
            "wind_speed_kmh": wx.get("wind_speed_kmh", -1),
            "wind_beaufort":  wx.get("wind_beaufort", -1),
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

    cci_by_date = {}
    showers_by_date = {}
    for d in query_dates:
        showers_by_date[d] = check_meteor_shower(d)
    for m in moon_info:
        d = m["date"]
        wins_for_date = [w for w in all_windows if w["datetime_tst"].date() == d]
        cci_by_date[d] = compute_cci_for_date(
            weather.get(d, {}),
            m,
            seeing_data.get(d, {}),
            wins_for_date,
            wind_profile,
            cci_profile=cci_profile,
            extra_data={"showers": showers_by_date.get(d, [])},
        )

    return {
        "intent":      intent,
        "good_windows": good[:10],
        "all_windows":  all_windows,
        "moon_info":   moon_info,
        "showers":     showers,
        "cci_profile": cci_profile,
        "unsupported_info": unsupported_info,
        "mw_composition_by_date":    mw_composition_by_date,
        "is_galaxy_query":           is_galaxy_query,
        "all_windows_out_of_range":  all_windows_out_of_range,
        "avg_cloud_cover":           avg_cloud_cover,
        "avg_visibility_km":         avg_visibility_km,
        "avg_seeing":                avg_seeing,
        "avg_transparency":          avg_transparency,
        "data_quality":              data_quality,
        "cci_by_date":               cci_by_date,
        "matched_targets":           matched_targets,
    }




# ── 全台最佳地點排名（Phase 3A #4） ───────────────────────────

def is_best_location_query(text):
    if any(sep in text for sep in ["vs", "VS", "Vs", "還是", "比較", "對比"]):
        return False
    place_question = any(k in text for k in ["哪裡", "哪邊", "哪個地點", "哪個景點", "去哪", "去哪裡", "最佳地點"])
    ranking_intent = any(k in text for k in ["最好", "最佳", "推薦", "適合", "值得"])
    return place_question and (ranking_intent or "拍" in text)

def parse_best_location_dates(text):
    today_tst = datetime.now(timezone(timedelta(hours=8))).date()
    if "後天" in text:
        d = today_tst + timedelta(days=2)
        return d, d
    if "明天" in text:
        d = today_tst + timedelta(days=1)
        return d, d
    if "週末" in text or "周末" in text:
        weekday = today_tst.weekday()  # Mon=0, Sat=5, Sun=6
        if weekday == 6:
            return today_tst, today_tst
        days_until_sat = 5 - weekday
        if days_until_sat < 0:
            days_until_sat += 7
        sat = today_tst + timedelta(days=days_until_sat)
        return sat, sat + timedelta(days=1)
    return today_tst, today_tst

def extract_best_location_targets(text):
    targets = []
    if "銀河" in text:
        targets.append("銀河核心")
    for target in TARGET_LIBRARY:
        if target["name"] in text and target["name"] not in targets:
            targets.append(target["name"])
    for alias, canonical in [
        ("M42", "獵戶座大星雲 M42"),
        ("M8", "礁湖星雲 M8"),
        ("M16", "鷹星雲 M16"),
        ("M31", "仙女座星系 M31"),
        ("NGC2244", "玫瑰星雲 NGC2244"),
        ("NGC2174", "猴頭星雲 NGC2174"),
        ("NGC6302", "昆蟲星雲 NGC6302"),
    ]:
        if alias.lower() in text.lower() and canonical not in targets:
            targets.append(canonical)
    return targets

def build_best_location_intent(text):
    date_start, date_end = parse_best_location_dates(text)
    targets = extract_best_location_targets(text)
    wind_profile = "deep_sky" if any(k in text for k in ["深空", "星雲", "星系"]) else "milky_way"
    region_scope = extract_region_scope(text)
    return {
        "query_type": "A" if targets else "B",
        "compare_mode": False,
        "location_name": f"{region_scope or '全台'}地點排名",
        "lat": None,
        "lon": None,
        "date_start": date_start.isoformat(),
        "date_end": date_end.isoformat(),
        "targets": targets,
        "extra_notes": "best_location_ranking",
        "region_scope": region_scope,
        "wind_profile": wind_profile,
    }

def dark_window_minutes(moon_info_day):
    return sum((de - ds).seconds // 60 for ds, de in moon_info_day.get("dark_windows", []))

def extract_region_scope(text):
    if any(k in text for k in ["離島", "外島", "澎湖", "金門", "馬祖", "綠島", "蘭嶼"]):
        return "離島"
    for scope in ["北部", "中部", "南部", "東部"]:
        if scope in text:
            return scope
    if any(k in text for k in ["北台灣", "北臺灣"]):
        return "北部"
    if any(k in text for k in ["中台灣", "中臺灣"]):
        return "中部"
    if any(k in text for k in ["南台灣", "南臺灣"]):
        return "南部"
    if any(k in text for k in ["東台灣", "東臺灣"]):
        return "東部"
    return ""

REGION_SCOPE_COUNTIES = {
    "北部": ["台北", "臺北", "新北", "基隆", "桃園", "新竹", "苗栗", "宜蘭"],
    "中部": ["台中", "臺中", "彰化", "南投", "雲林"],
    "南部": ["嘉義", "台南", "臺南", "高雄", "屏東"],
    "東部": ["花蓮", "台東", "臺東", "宜蘭"],
    "離島": ["澎湖", "金門", "連江", "馬祖", "綠島", "蘭嶼", "七美", "西嶼", "湖西", "莒光", "北竿"],
}

def infer_region_scope_from_coordinates(lat, lon):
    if lon < 119.8 or lat > 25.6 or (lon > 121.8 and lat < 23.5):
        return "離島"
    if lon >= 121.0 and lat < 24.9:
        return "東部"
    if lat >= 24.3:
        return "北部"
    if lat >= 23.5:
        return "中部"
    return "南部"

def location_matches_region_scope(item, region_scope):
    if not region_scope:
        return True
    region = str(item.get("region", ""))
    if any(keyword in region for keyword in REGION_SCOPE_COUNTIES.get(region_scope, [])):
        return True
    try:
        return infer_region_scope_from_coordinates(item["lat"], item["lon"]) == region_scope
    except Exception:
        return False

def is_ranking_location(item):
    # 地點審核制：只有 approved 地點可進最佳地點排名；
    # 用戶提供的 pending 地點僅供該用戶的直接查詢使用
    return item.get("review_status") == "approved"

def ranking_location_items(region_scope=""):
    return [
        (name, item) for name, item in LOCATION_DATA.items()
        if is_ranking_location(item) and location_matches_region_scope(item, region_scope)
    ]

def ranking_location_scope_counts(items):
    user_count = sum(1 for _, item in items if item.get("source") == "user-provided")
    return {
        "total": len(items),
        "user_provided": user_count,
        "approved": len(items) - user_count,
    }

MISSING_SEEING_DAY = {"data_status": "missing", "seeing": -1, "transparency": -1}
MAX_SEEING_TRANSPARENCY_UPLIFT = 11

def rank_location_candidate(name, item, query_dates, matched_targets, wind_profile, include_seeing=False):
    try:
        lat = item["lat"]
        lon = item["lon"]
        observer = wgs84.latlon(lat, lon)
        moon_info = get_moon_info(observer, query_dates)
        dark_windows_by_date = {m["date"]: m["dark_windows"] for m in moon_info}
        all_windows = []
        for target in matched_targets:
            all_windows.extend(
                compute_target_windows(observer, target, query_dates, dark_windows_by_date)
            )
        weather = check_weather_multi(lat, lon, query_dates)
        seeing_data = get_7timer_seeing(lat, lon, query_dates) if include_seeing else {}
        best = None
        for m in moon_info:
            d = m["date"]
            wins_for_date = [w for w in all_windows if w["datetime_tst"].date() == d]
            cci = compute_cci_for_date(
                weather.get(d, {}),
                m,
                seeing_data.get(d, MISSING_SEEING_DAY),
                wins_for_date,
                wind_profile,
            )
            wx = weather.get(d, {})
            sd = seeing_data.get(d, MISSING_SEEING_DAY)
            row = {
                "name": name,
                "region": item.get("region", ""),
                "lat": lat,
                "lon": lon,
                "date": d,
                "score": cci["score"],
                "label": cci["label"],
                "cloud_cover": wx.get("cloud_cover", -1),
                "humidity": wx.get("humidity", -1),
                "visibility_km": wx.get("visibility_km", -1),
                "wind_speed_kmh": wx.get("wind_speed_kmh", -1),
                "wind_beaufort": wx.get("wind_beaufort", -1),
                "dew_risk": wx.get("dew_risk", False),
                "dark_minutes": dark_window_minutes(m),
                "target_visible": bool(wins_for_date),
                "weather_status": wx.get("data_status", "missing"),
                "seeing": sd.get("seeing", -1),
                "transparency": sd.get("transparency", -1),
                "ranking_precision": "refined" if include_seeing else "fast",
            }
            if best is None or (row["score"], row["dark_minutes"]) > (best["score"], best["dark_minutes"]):
                best = row
        return best
    except Exception as e:
        print(f"[最佳地點排名錯誤] {name}: {type(e).__name__}: {e}", flush=True)
        return None

def run_best_location_ranking(base_intent, limit=6):
    date_start = date.fromisoformat(base_intent["date_start"])
    date_end = date.fromisoformat(base_intent["date_end"])
    query_dates = [date_start + timedelta(days=i) for i in range((date_end - date_start).days + 1)]
    matched_targets = match_targets(base_intent.get("targets", []))
    wind_profile = determine_wind_profile(base_intent, matched_targets)
    candidates = []
    location_items = ranking_location_items(base_intent.get("region_scope", ""))
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(rank_location_candidate, name, item, query_dates, matched_targets, wind_profile)
            for name, item in location_items
        ]
        for future in futures:
            row = future.result()
            if row:
                candidates.append(row)

    candidates.sort(
        key=lambda r: (
            r["score"],
            1 if r.get("weather_status") == "ok" else 0,
            r["dark_minutes"],
            -1 * (r["cloud_cover"] if r["cloud_cover"] >= 0 else 999),
        ),
        reverse=True,
    )
    fast_cutoff = candidates[min(limit, len(candidates)) - 1]["score"] if candidates else 0
    refine_names = {
        row["name"]
        for idx, row in enumerate(candidates)
        if idx < limit * 3 or row["score"] + MAX_SEEING_TRANSPARENCY_UPLIFT >= fast_cutoff
    }
    refined_by_name = {}
    if refine_names:
        items_by_name = dict(location_items)
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [
                pool.submit(
                    rank_location_candidate,
                    name,
                    items_by_name[name],
                    query_dates,
                    matched_targets,
                    wind_profile,
                    True,
                )
                for name in refine_names
            ]
            for future in futures:
                row = future.result()
                if row:
                    refined_by_name[row["name"]] = row
    candidates = [refined_by_name.get(row["name"], row) for row in candidates]
    candidates.sort(
        key=lambda r: (
            r["score"],
            1 if r.get("weather_status") == "ok" else 0,
            r["dark_minutes"],
            -1 * (r["cloud_cover"] if r["cloud_cover"] >= 0 else 999),
        ),
        reverse=True,
    )
    return {
        "intent": base_intent,
        "query_dates": query_dates,
        "targets": [t["name"] for t in matched_targets],
        "ranked": candidates[:limit],
        "candidate_count": len(candidates),
        "scope_counts": ranking_location_scope_counts(location_items),
        "wind_profile": wind_profile,
        "refined_count": len(refined_by_name),
    }

def format_duration_minutes(total_min):
    h, m = divmod(max(total_min, 0), 60)
    return f"{h}h{m:02d}m"

def generate_best_location_reply(ranking):
    intent = ranking["intent"]
    ranked = ranking["ranked"]
    target_text = "、".join(intent.get("targets") or []) or "開放探索"
    date_text = intent["date_start"] if intent["date_start"] == intent["date_end"] else f"{intent['date_start']}～{intent['date_end']}"
    if not ranked:
        return (
            "【最佳地點排行】\n"
            f"日期：{date_text}\n"
            f"題材：{target_text}\n\n"
            "目前沒有足夠資料產生地點排名，建議改查單一地點或稍後再試。"
        )

    lines = [
        "【最佳地點排行】",
        f"日期：{date_text}",
        f"題材：{target_text}",
        f"區域：{intent.get('region_scope') or '全區'}",
        (
            f"資料範圍：{ranking['scope_counts']['total']} 個地點"
            f"（production approved {ranking['scope_counts']['approved']}、"
            f"自定義 {ranking['scope_counts']['user_provided']}）"
        ),
        "",
    ]
    for idx, row in enumerate(ranked, 1):
        icon = re.match(r"^\S+", row["label"]).group()
        cloud = f"{row['cloud_cover']}%" if row["cloud_cover"] >= 0 else "N/A"
        vis = f"{row['visibility_km']}km" if row["visibility_km"] >= 0 else "N/A"
        wind = f"{row['wind_speed_kmh']}km/h・{row['wind_beaufort']}級" if row["wind_speed_kmh"] >= 0 else "N/A"
        seeing = f"視寧 {row['seeing']}/8・透明 {row['transparency']}/8" if row.get("seeing", -1) > 0 else "視寧/透明未精算"
        dew = "結露風險" if row["dew_risk"] else "結露低"
        visible = "目標可見" if row["target_visible"] else "目標窗口弱"
        lines.append(
            f"{idx}. {row['name']}（{row['region']}）{icon} {row['score']}%｜"
            f"{row['date'].strftime('%m/%d')}｜雲量 {cloud}・暗空 {format_duration_minutes(row['dark_minutes'])}・"
            f"風 {wind}・{seeing}・能見度 {vis}・{dew}・{visible}"
        )
    best = ranked[0]
    if best["score"] < 40:
        lines.extend([
            "",
            "➡️ 結論：全台條件都偏弱，不建議硬衝；可改期或改拍月景、城市夜景。"
        ])
    else:
        lines.extend([
            "",
            f"➡️ 最佳：{best['name']}，信心度 {best['score']}%。出發前仍建議確認即時雲圖與現地道路狀況。"
        ])
    wind_note = "銀河容忍上限 3 級風" if ranking.get("wind_profile") == "milky_way" else "深空容忍上限 2 級風"
    lines.append(
        f"註：排名先用快速 CCI 全區篩選，再對可能進榜的 {ranking.get('refined_count', 0)} 個地點補 7Timer 精排"
        f"（{wind_note}）。"
    )
    return "\n".join(lines)


# ── 回覆生成 ───────────────────────────────────────────────────

def _format_time(dt):
    if dt is None:
        return "N/A"
    return dt.strftime("%H:%M")

def generate_reply(result):
    intent           = result["intent"]
    good             = result["good_windows"]
    all_wins         = result.get("all_windows", [])
    moon_info        = result["moon_info"]
    showers          = result["showers"]
    mw_comp          = result["mw_composition_by_date"]
    data_quality     = result.get("data_quality", {})
    cci_by_date      = result.get("cci_by_date", {})
    cci_profile      = result.get("cci_profile", "default")
    unsupported_info = result.get("unsupported_info", {})
    matched_targets  = result.get("matched_targets", [])

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
        "風速":    f"{w['wind_speed_kmh']} km/h（{w['wind_beaufort']}級風）" if w.get('wind_speed_kmh', -1) >= 0 else "N/A",
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

    if weather_status in ("out_of_range", "unknown"):
        candidate_context = "（氣象資料不足，以下僅為天文窗口供參考）"
    elif weather_fallback:
        candidate_context = "（天氣不佳，以下為天文窗口供參考）"
    else:
        candidate_context = ""

    data_quality_text = json.dumps(data_quality, ensure_ascii=False)

    # ── A1：未命中目標固定回覆格式 ──────────────────────────────
    unmatched_targets = (
        data_quality.get("celestial_positions", {}).get("unmatched_targets", [])
    )
    if unmatched_targets:
        unmatched_lines = []
        for tgt in unmatched_targets:
            unmatched_lines.append(
                f"⚠️ **{tgt}** — 本系統尚無此天體的座標資料，無法計算方位與觀測窗口。\n"
                f"  您可以：\n"
                f"  1. 嘗試輸入 NGC/IC 編號或常見英文名稱\n"
                f"  2. 於 Stellarium 或 theskylive.com 查詢該天體的即時位置"
            )
        unmatched_block = "\n\n".join(unmatched_lines)
        unmatched_instruction = f"""
【未命中目標（必須使用以下固定格式回覆，不可更動措辭）】
對以下每一個未命中目標，逐一照字輸出：
{unmatched_block}
輸出完上述固定段落後，才可繼續其他分析。"""
    else:
        unmatched_instruction = ""

    # ── 設備適配提示（深空目標才顯示）──────────────────────────
    TRACKING_LABEL = {"no":"不需要", "optional":"可選（有更好）", "recommended":"建議有", "required":"必須有"}
    DIFFICULTY_LABEL = {1:"⭐ 入門", 2:"⭐⭐ 初階", 3:"⭐⭐⭐ 中階", 4:"⭐⭐⭐⭐ 進階"}
    equip_hints = []
    for t in matched_targets:
        if t.get("min_focal_mm"):
            track = TRACKING_LABEL.get(t.get("tracking_required",""), t.get("tracking_required",""))
            diff  = DIFFICULTY_LABEL.get(t.get("difficulty", 1), "")
            equip_hints.append(
                f"  • {t['name']}：最短焦距 {t['min_focal_mm']}mm，赤道儀 {track}，難度 {diff}"
            )
    equip_context = ("設備適配（深空題材）：\n" + "\n".join(equip_hints)) if equip_hints else ""

    # ── 收集 profile_notes 去重 ───────────────────────────────
    all_profile_notes = []
    for cci in cci_by_date.values():
        for note in cci.get("profile_notes", []):
            if note not in all_profile_notes:
                all_profile_notes.append(note)

    # ── 題材特殊說明（放入 system prompt） ────────────────────
    if cci_profile == "meteor":
        subject_instruction = """
【流星雨題材特殊說明】
- 本次 CCI 以月面照度為主要干擾因子（月越亮越扣分），非暗空窗口決定
- ZHR 為歷史靜態值，實際流量可能因彗星塵雲分布而不同，不可保證
- 【裝備提醒】必須說明：廣角鏡（14–35mm）為主力，不需赤道儀，固定腳架即可
- 若 ZHR ≥ 100，可加強「值得等待」的說法；ZHR < 30 要提醒期望值不高"""
    elif cci_profile == "moonscape":
        subject_instruction = """
【月景題材特殊說明】
- 本次 CCI 以月光強度為加分項，月越亮 CCI 越高（與深空攝影邏輯相反）
- 【結論】應說明月景出勤可行性；不需提暗空窗口
- 【裝備提醒】中長焦（50–200mm）配合前景地貌效果最佳；無需赤道儀"""
    elif cci_profile == "lunar_eclipse":
        subject_instruction = """
【月蝕題材特殊說明】
- 本次 CCI 不考慮暗空窗口需求（月蝕時月亮本身是主角）
- 透明度是最關鍵因子（月蝕顏色清晰度取決於大氣透明度）
- 【重要】本系統不計算月蝕時間；月蝕食相時刻請查詢台北天文館（tam.gov.taipei）或 Stellarium
- 若 CCI 條件佳，說明天況適合觀賞月蝕，但必須補上「請另行確認月蝕時間」的提示"""
    elif cci_profile == "comet_layer1":
        subject_instruction = """
【彗星題材特殊說明（第一層）】
- 本評估僅提供天況 CCI，彗星方位角不可信（靜態座標已過期）
- 【推薦時刻】區塊：可提供氣象窗口，但必須標注「彗星方位請另行查詢 Stellarium 或 JPL Horizons」
- 禁止在回覆中提供彗星方位角或仰角作為觀測依據"""
    else:
        subject_instruction = ""

    system = f"""你是專業天文攝影顧問，熟悉台灣各地拍攝環境。繁體中文，親切專業。

【硬性資料原則：不可猜測】
- 你只能根據輸入資料作答；沒有提供的資料不可自行推論、補齊、猜測或用常識填空。
- 若資料狀態是 missing、partial、N/A、-1、空陣列或無資料，必須明確說「目前沒有資料」或「資料不足」，不可說成好/壞/可拍。
- 氣象預報只能依 Open-Meteo 資料；Open-Meteo missing 時，不可評論雲量、濕度、能見度、天氣好壞。
- 視寧度/透明度只能依 7Timer 資料；7Timer missing 時，不可評論視寧度或透明度好壞。
- 天體位置只能依 Skyfield 與內建標的資料庫。若 data_quality.celestial_positions 有 unmatched_targets，必須使用下方【未命中目標】區塊的固定格式回覆，不可創造座標或觀測時刻。
- 若資料不足，仍可提供「已知的天文資料」與「需要補哪些資料」，但結論必須標示限制。

【重要】氣象條件是第一優先判斷：
{weather_instruction}

【反樂觀守則】（優先於所有格式指引，不可因「完整分析」而放寬）
- CCI < 40 的日期：該日結論第一句必須是「不建議出勤」或「不值得出勤」，禁止用任何正面語氣帶過
- CCI 40–59 的日期：禁止出現「仍有機會」「值得一試」「把握機會」「運氣好的話」「也許」等模糊鼓勵語氣
- 下方「必須點出的風險」清單中的每一條都必須在回覆正文明確出現，禁止合併、省略或用正面說法抵消
- 若某因子得分 ≤ 15，該因子是出勤障礙，必須以明確的否定或警告措辭說明，不可用「偏高」「稍差」輕描淡寫
- 只能使用 CCI 定義的五個 icon（✅ 🟢 ⚠️ 🟠 ❌），禁止使用 ⛔ 🚫 🔴 或其他未定義符號

回覆格式（每區塊標題用【】，依氣象狀態調整詳細程度）：

【結論】
- 直接採用下方「出勤信心指數（CCI）」中計算好的 icon 和分數，不可自行更改數值
- 若查詢跨多天：每天一行
  格式：「MM/DD {{CCI_icon}} 信心度 XX%｜{{最重要的 1~2 個因子摘要}}」
  例：「06/20 ✅ 信心度 78%｜雲量22%・暗空4.5h」
  最後一行：「➡️ 最佳：MM/DD，[一句話原因]」
- 若查詢單天：CCI icon + 分數，加最佳時刻和最重要一個條件說明
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
  - 風速：最大風速與蒲福風級；銀河最高容忍 3 級風，深空最高容忍 2 級風
  - 結露風險：溫度/露點差，是否需要加熱帶
  - 若有視寧度與透明度（7Timer，1=最佳 8=最差），簡要評估對星點清晰度的影響

【裝備提醒】針對地點高度、溫度、交通特性給出具體建議
  曝光建議（天文條件合適時提供）：
  - 快門：500 法則（500 ÷ 焦距 = 最長曝光秒數，有赤道儀可延長至 2～4 倍）
  - ISO：新月期建議 1600～3200；眉月／下弦月建議 800～1600；明顯月光時降至 400～800
  - 光圈：盡量全開（f/1.4～f/2.8）以收集最多星光；f/4 以上星點更銳利但需提高 ISO 補償

若有流星雨加【流星雨加碼】

{subject_instruction}

核心原則：
- 天文數據（仰角、方位角、月出月落）來自精確計算，如實呈現
- 氣象判斷只根據提供的數據，不自行假設
- 天況不佳時主動建議替代方案（改期、換地點、轉攻其他題材）
- 總長不超過 500 字

{unmatched_instruction}"""

    cci_list = []
    risk_flags = []
    factor_labels = {
        "cloud":        "雲量",
        "dark_window":  "暗空窗口",
        "seeing":       "視寧度",
        "transparency": "透明度",
        "target":       "目標可見性",
        "dew":          "結露風險",
        "wind":         "風速",
    }
    for m in moon_info:
        d = m["date"]
        cci = cci_by_date.get(d, {})
        if not cci:
            continue
        bd = cci.get("breakdown", {})
        date_str = d.strftime("%m/%d")
        cci_list.append({
            "日期": d.isoformat(),
            "信心度": f"{cci['score']}%",
            "標籤": cci["label"],
            "雲量": bd.get("cloud", {}).get("raw", "N/A"),
            "暗空窗口": bd.get("dark_window", {}).get("raw", "N/A"),
            "視寧度": bd.get("seeing", {}).get("raw", "N/A"),
            "透明度": bd.get("transparency", {}).get("raw", "N/A"),
            "目標可見性": bd.get("target", {}).get("raw", "N/A"),
            "結露風險": bd.get("dew", {}).get("raw", "N/A"),
            "風速": bd.get("wind", {}).get("raw", "N/A"),
            "資料完整性": cci.get("completeness", "unknown"),
        })
        if cci["score"] < 40:
            risk_flags.append(
                f"【{date_str}】整體 CCI={cci['score']}%（{cci['label']}）"
                f"—結論必須以「不建議/不值得出勤」開頭"
            )
        for key, label in factor_labels.items():
            factor = bd.get(key, {})
            if factor.get("score", 100) <= 15:
                risk_flags.append(
                    f"【{date_str}】{label}出勤障礙：{factor.get('raw', '?')}"
                    f"（得分 {factor.get('score')}/100）—必須明確說明此點不利出勤"
                )
    cci_str = json.dumps(cci_list, ensure_ascii=False, indent=2) if cci_list else "無 CCI 資料"
    risk_text = "\n".join(f"- {f}" for f in risk_flags) if risk_flags else "（本次查詢無高風險項目）"
    profile_note_text = "\n".join(all_profile_notes) if all_profile_notes else ""

    reply_text = call_openrouter(
        system,
        (
            f"查詢類型：{'指定標的' if intent['query_type']=='A' else '開放探索'}\n"
            f"CCI 計算模式：{cci_profile}\n"
            f"地點：{intent['location_name']}\n"
            f"日期：{intent['date_start']} ～ {intent['date_end']}\n"
            f"氣象狀態：{weather_status}\n"
            f"夜間平均雲量：{avg_cloud}%\n"
            f"夜間平均能見度：{f'{avg_visibility_km} km' if avg_visibility_km >= 0 else 'N/A'}\n"
            f"夜間平均視寧度（7Timer）：{f'{avg_seeing}/8（1=最佳）' if avg_seeing > 0 else 'N/A'}\n"
            f"夜間平均大氣透明度（7Timer）：{f'{avg_transparency}/8（1=最佳）' if avg_transparency > 0 else 'N/A'}\n\n"
            f"資料品質與缺資料紀錄：\n{data_quality_text}\n\n"
            + (f"{equip_context}\n\n" if equip_context else "")
            + (f"題材注意事項：\n{profile_note_text}\n\n" if profile_note_text else "")
            + f"候選時刻{candidate_context}：\n{ws if windows_for_llm else '無天文觀測窗口'}\n\n"
            f"月相與暗空窗口：\n{ms}\n\n"
            + (f"銀河構圖資訊：\n{mw_str}\n\n" if mw_str is not None else "")
            + f"出勤信心指數（CCI）：\n{cci_str}\n\n"
            + f"必須點出的風險：\n{risk_text}\n\n"
            + f"流星雨：{ss}"
        ),
        max_tokens=1000,
    )
    return enforce_no_go_language(reply_text, cci_by_date)


def enforce_no_go_language(reply_text, cci_by_date):
    """紅藍軍程式層防線：CCI < 40 的日期，回覆必須明確出現 No-Go 用語。
    LLM prompt 已有相同要求，此處為最後保證，防止 LLM 語氣軟化（不可被 prompt 繞過）。
    """
    if not reply_text or not cci_by_date:
        return reply_text
    low_dates = sorted(d for d, cci in cci_by_date.items() if cci.get("score", 100) < 40)
    if not low_dates:
        return reply_text
    if "不建議" in reply_text or "不值得" in reply_text:
        return reply_text
    dates_str = "、".join(f"{d.month:02d}/{d.day:02d}" for d in low_dates)
    return (
        f"❌ 出勤判定：{dates_str} 信心度低於 40%，不建議出勤。\n"
        f"（系統加註：以下分析僅供參考，請以此判定為準）\n\n"
        f"{reply_text}"
    )


def generate_comparison_reply(result_a, result_b):
    """兩地點 CCI 並排比較，回傳穩定、不可被 LLM 改寫的中文回覆。"""
    intent_a = result_a["intent"]
    intent_b = result_b["intent"]
    moon_a   = result_a["moon_info"]
    cci_a    = result_a.get("cci_by_date", {})
    cci_b    = result_b.get("cci_by_date", {})
    name_a   = intent_a["location_name"]
    name_b   = intent_b["location_name"]

    lines = ["【比較結論】"]
    best = None
    for m in moon_a:
        d  = m["date"]
        ca = cci_a.get(d, {})
        cb = cci_b.get(d, {})
        if not ca or not cb:
            continue
        score_a = ca["score"]
        score_b = cb["score"]
        icon_a  = re.match(r"^\S+", ca["label"]).group()
        icon_b  = re.match(r"^\S+", cb["label"]).group()
        diff = score_a - score_b

        if score_a < 40 and score_b < 40:
            verdict = "兩地都不建議，建議改期或改拍題材"
        elif diff == 0:
            verdict = "完全同分，選交通較便利者"
        elif diff > 0:
            verdict = f"{name_a} {'略優' if diff < 10 else '較佳'}（+{diff}%）"
        else:
            verdict = f"{name_b} {'略優' if abs(diff) < 10 else '較佳'}（+{abs(diff)}%）"

        lines.append(
            f"{d.month:02d}/{d.day:02d}  {name_a} {icon_a} {score_a}%  vs  "
            f"{name_b} {icon_b} {score_b}%  → {verdict}"
        )

        winner_name, winner_score = (name_a, score_a) if score_a >= score_b else (name_b, score_b)
        if best is None or winner_score > best["score"]:
            best = {"date": d, "name": winner_name, "score": winner_score, "diff": abs(diff)}

    if not best:
        return "【比較結論】\n目前缺少可比較的 CCI 資料，請換一天或重新查詢。"

    if best["score"] < 40:
        lines.append("➡️ 最佳：兩地條件都偏差，建議改期或改拍月景、城市夜景等題材。")
    else:
        qualifier = "，但差距小，仍建議出發前再確認即時雲量" if best["diff"] < 10 else ""
        lines.append(
            f"➡️ 最佳：{best['date'].month:02d}/{best['date'].day:02d} "
            f"{best['name']}，信心度 {best['score']}%{qualifier}。"
        )

    return "\n".join(lines)


# ── LINE Bot 狀態管理 ─────────────────────────────────────────
# user_state:                  {user_id: "waiting_wish" | "waiting_location_coordinates"}
# user_last_query:             {user_id: "上次查詢文字"}
# user_wish_text:              {user_id: "自動許願文字"}
# user_pending_location_query: {user_id: {"text": 原查詢, "intent": 解析結果}}

user_state                  = {}
user_last_query             = {}
user_wish_text              = {}
user_pending_location_query = {}

# 查詢處理執行緒池：取代裸 threading.Thread，限制同時處理的查詢數，
# 避免流量突增時無上限開執行緒
MESSAGE_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="query")

def submit_background_query(*args):
    """把 process_and_reply 丟進共用執行緒池執行。"""
    MESSAGE_EXECUTOR.submit(process_and_reply, *args)


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


def process_and_reply(user_id, text, mark_as_read_token="", prefetched_intent=None, reply_prefix=""):
    """
    背景執行緒：執行天文計算後以 push_message 回傳結果。
    reply_token 30 秒過期，長時間計算須改用 push_message。
    """
    username = get_display_name(user_id)
    try:
        if is_best_location_query(text):
            intent_for_check = build_best_location_intent(text)
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
                ), "unsupported notice for best location")
                mark_message_as_read(mark_as_read_token)
                return

            ranking = run_best_location_ranking(intent_for_check)
            reply = generate_best_location_reply(ranking)
            user_last_query[user_id] = text
            log_query(username, user_id, text, intent_for_check, {
                "policy": "best_location_ranking",
                "location": {
                    "status": "ranking",
                    "candidate_count": ranking.get("candidate_count", 0),
                },
                "seeing": {
                    "source": "7Timer",
                    "status": "neutralized_for_fast_ranking",
                },
            })
            safe_push_message(user_id, TextSendMessage(
                text=reply,
                quick_reply=make_feedback_quick_reply()
            ), "best location ranking reply")
            mark_message_as_read(mark_as_read_token)
            print("[回覆] 最佳地點排名完成", flush=True)
            return

        intent_for_check = normalize_intent(prefetched_intent, text) if prefetched_intent else parse_intent(text)
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

        if intent_for_check.get("compare_mode"):
            text_loc_a, text_loc_b = extract_compare_locations_from_text(text)
            if text_loc_a and text_loc_b:
                locations = [{"name": text_loc_a}, {"name": text_loc_b}]
            else:
                locations = intent_for_check.get("locations") or []
            if len(locations) < 2:
                safe_push_message(user_id, TextSendMessage(
                    text="⚠️ 比較模式需要兩個地點，請重新輸入，例如：「合歡山 vs 阿里山 這週末銀河」"
                ), "compare mode location count error")
                mark_message_as_read(mark_as_read_token)
                return
            try:
                name_a, lat_a, lon_a = resolve_compare_location(locations[0])
                name_b, lat_b, lon_b = resolve_compare_location(locations[1])
            except LocationResolutionError as loc_err:
                safe_push_message(user_id, TextSendMessage(
                    text=f"⚠️ {loc_err}\n\n比較模式目前只支援已審核地點，請單獨查詢或補充座標後再試。"
                ), "compare location resolution error")
                mark_message_as_read(mark_as_read_token)
                return
            if name_a == name_b:
                safe_push_message(user_id, TextSendMessage(
                    text=f"⚠️ 兩個地點都解析成「{name_a}」，無法進行比較。\n請確認格式，例如：「鳶峰 vs 日月潭 今晚銀河」"
                ), "compare same location error")
                mark_message_as_read(mark_as_read_token)
                return
            base = {k: v for k, v in intent_for_check.items()
                    if k not in ("locations", "compare_mode", "location_name", "lat", "lon")}
            intent_a = {**base, "location_name": name_a, "lat": lat_a, "lon": lon_a}
            intent_b = {**base, "location_name": name_b, "lat": lat_b, "lon": lon_b}
            with ThreadPoolExecutor(max_workers=2) as pool:
                future_a = pool.submit(run_query, name_a, intent_a)
                future_b = pool.submit(run_query, name_b, intent_b)
                result_a = future_a.result()
                result_b = future_b.result()
            reply = generate_comparison_reply(result_a, result_b)
            user_last_query[user_id] = text
            log_query(username, user_id, text, intent_a, {})
            safe_push_message(user_id, TextSendMessage(
                text=reply,
                quick_reply=make_feedback_quick_reply()
            ), "compare reply")
            mark_message_as_read(mark_as_read_token)
            print("[回覆] 比較模式完成", flush=True)
            return

        result = run_query(text, prefetched_intent=intent_for_check)
        reply  = generate_reply(result)
        reply = f"{format_location_resolution(result['intent'], text)}\n\n{reply}"
        if reply_prefix:
            reply = f"{reply_prefix}\n\n{reply}"
        user_last_query[user_id] = text
        log_query(username, user_id, text, result["intent"], result.get("data_quality"))

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

    except LocationResolutionError as e:
        requested_location = e.location_name or extract_location_hint(text) or text
        try:
            coordinate_intent = apply_inline_coordinates(e.intent, text, requested_location)
        except ValueError:
            coordinate_intent = None
        if coordinate_intent:
            scope = check_unsupported(text, coordinate_intent)
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
                ), "unsupported notice after coordinate fallback")
                mark_message_as_read(mark_as_read_token)
                return

            result = run_query(text, prefetched_intent=coordinate_intent)
            reply = generate_reply(result)
            reply = f"{format_location_resolution(result['intent'], text)}\n\n{reply}"
            user_last_query[user_id] = text
            log_query(username, user_id, text, result["intent"], result.get("data_quality"))
            safe_push_message(user_id, TextSendMessage(
                text=reply,
                quick_reply=make_feedback_quick_reply()
            ), "query reply after coordinate fallback")
            mark_message_as_read(mark_as_read_token)
            print("[回覆] 座標 fallback 完成", flush=True)
            return

        wish_saved = log_wish(
            username,
            user_id,
            text,
            f"地點資料庫新增：{requested_location}（原始查詢：{text}）",
            "地點許願（自動）",
        )
        user_state[user_id] = "waiting_location_coordinates"
        user_pending_location_query[user_id] = {
            "text": text,
            "intent": e.intent,
            "location_name": requested_location,
        }
        log_query(username, user_id, text, e.intent or {}, {
            "policy": "no_guessing_without_evidence",
            "location": {
                "status": "missing",
                "requested_location": requested_location,
                "reason": str(e),
                "action": "added_to_location_wishlist_and_asked_user_for_coordinates",
            },
        })
        prompt = location_coordinate_prompt(requested_location)
        if not wish_saved:
            prompt += "\n\n⚠️ 地點許願池暫時寫入失敗，但我仍會等待你補座標。"
        safe_push_message(user_id, TextSendMessage(text=prompt), "location coordinate prompt")
        mark_message_as_read(mark_as_read_token)
        print(f"[地點待補座標] {requested_location}: {text}", flush=True)

    except IntentParseError as e:
        log_unhandled_exception("parse_intent", e)
        safe_push_message(user_id, TextSendMessage(
            text=(
                "⚠️ 我沒能看懂這個查詢，請換個說法再試一次。\n"
                "例如：「6/20 合歡山 銀河」或「這個週末 阿里山 有什麼可以拍？」"
            )
        ), "intent parse error reply")
        mark_message_as_read(mark_as_read_token)
        print(f"[意圖解析失敗] {text}", flush=True)

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

    # 等待使用者補座標
    if user_state.get(user_id) == "waiting_location_coordinates":
        pending = user_pending_location_query.get(user_id)
        if text in ["取消", "cancel", "Cancel", "CANCEL"]:
            user_state.pop(user_id, None)
            user_pending_location_query.pop(user_id, None)
            safe_reply_message(event.reply_token, TextSendMessage(text="好的，已取消剛剛的地點補座標流程。"), "cancel location prompt")
            mark_message_as_read(mark_as_read_token)
            return
        try:
            coordinates = extract_user_coordinates(text)
        except ValueError as e:
            safe_reply_message(event.reply_token, TextSendMessage(
                text=(
                    f"{e}\n\n"
                    "請重新提供全球合法座標：緯度 -90～90、經度 -180～180。\n"
                    "例如：座標：23.124, 121.216"
                )
            ), "invalid coordinate reply")
            mark_message_as_read(mark_as_read_token)
            return
        if not coordinates and is_likely_new_query(text):
            user_state.pop(user_id, None)
            user_pending_location_query.pop(user_id, None)
            print(f"[地點補座標] 收到新查詢，取消上一筆 pending：{text}", flush=True)
        elif not coordinates:
            safe_reply_message(event.reply_token, TextSendMessage(
                text=(
                    "我還是讀不到經緯度。\n\n"
                    "請回覆例如：\n"
                    "座標：23.124, 121.216\n"
                    "或：北緯 23.124 東經 121.216\n\n"
                    "若不想繼續，請回覆「取消」。"
                )
            ), "missing coordinate reply")
            mark_message_as_read(mark_as_read_token)
            return

        if not coordinates:
            pass
        elif not pending:
            user_state.pop(user_id, None)
            safe_reply_message(event.reply_token, TextSendMessage(
                text="找不到上一筆待補座標查詢，請重新輸入完整問題。"
            ), "missing pending location query")
            mark_message_as_read(mark_as_read_token)
            return
        elif coordinates:
            lat, lon = coordinates
            intent = dict(pending.get("intent") or {})
            intent["lat"] = lat
            intent["lon"] = lon
            intent["location_name"] = pending.get("location_name") or intent.get("location_name") or "自訂座標"
            # 用戶提供座標 → 存入自定義地點，下次直接解析
            save_custom_location(intent["location_name"], lat, lon, original_query=pending.get("text", ""))
            user_state.pop(user_id, None)
            user_pending_location_query.pop(user_id, None)

            warning = ""
            if not is_in_taiwan_loose_range(lat, lon):
                warning = "⚠️ 這組座標看起來不在台灣常用範圍內，我仍可計算，但請確認座標是否正確。"
            safe_reply_message(event.reply_token, TextSendMessage(
                text=(
                    f"收到座標：{lat:.6f}, {lon:.6f}\n"
                    + (f"{warning}\n" if warning else "")
                    + "🔭 我會接續剛剛的查詢開始計算。"
                )
            ), "coordinate accepted reply")
            mark_message_as_read(mark_as_read_token)
            submit_background_query(user_id, pending["text"], mark_as_read_token, intent, warning)
            return

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

    # 15天景點氣象評估：等待用戶輸入地點
    if user_state.get(user_id) == "waiting_weather_location":
        if text in ["取消", "cancel", "Cancel", "CANCEL"]:
            user_state.pop(user_id, None)
            safe_reply_message(event.reply_token, TextSendMessage(text="好的，已取消景點氣象評估。"), "cancel weather 15d")
            mark_message_as_read(mark_as_read_token)
            return
        # 直接把用戶輸入（景點 + 可選日期）送進主流程
        user_state.pop(user_id, None)
        mark_message_as_read(mark_as_read_token)
        safe_reply_message(event.reply_token, TextSendMessage(
            text=f"📅 正在查詢 {text} 的氣象條件，請稍候（約 30~60 秒）⏳"
        ), "weather 15d loading")
        submit_background_query(user_id, text, mark_as_read_token)
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

    # 服務選單
    if text in ["/menu", "menu", "選單", "服務", "功能"]:
        safe_reply_message(event.reply_token, TextSendMessage(
            text="🔭 請選擇服務：",
            quick_reply=QuickReply(items=[
                QuickReplyButton(action=PostbackAction(
                    label="📅 15天景點氣象評估",
                    data="menu_weather_15d",
                    displayText="15天景點氣象評估",
                )),
                QuickReplyButton(action=PostbackAction(
                    label="❓ 使用說明",
                    data="menu_help",
                    displayText="使用說明",
                )),
            ])
        ), "menu reply")
        mark_message_as_read(mark_as_read_token)
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
    submit_background_query(user_id, text, mark_as_read_token)


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
    elif data == "menu_weather_15d":
        user_state[user_id] = "waiting_weather_location"
        safe_reply_message(event.reply_token, TextSendMessage(
            text=(
                "📅 15天景點氣象評估\n\n"
                "請輸入景點名稱，可加上日期或區間（15天內），例如：\n"
                "・合歡山（預設查詢未來數天）\n"
                "・墾丁 6月20日\n"
                "・阿里山 6月18日到6月22日\n\n"
                "我會評估每晚雲量、能見度與結露風險 🌤"
            )
        ), "weather 15d prompt")
    elif data == "menu_help":
        safe_reply_message(event.reply_token, TextSendMessage(
            text=(
                "🔭 天文攝影查詢 Bot\n\n"
                "直接用自然語言問我，例如：\n"
                "・4月15日 合歡山 銀河\n"
                "・這個週末 阿里山 有什麼可以拍？\n"
                "・5月1日到3日 墾丁 天蠍座\n\n"
                "或輸入「選單」隨時叫出服務選單 🌌"
            )
        ), "menu help reply")


# ── 主程式 ────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 LINE Bot 啟動中（port {port}）...", flush=True)
    app.run(host="0.0.0.0", port=port)
