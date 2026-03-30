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

ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY")
TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS_JSON")
SPREADSHEET_ID     = "1fYmucd6mB8nlzblJsl44QDerUjx-1cI3Ll9EgO_KPnU"

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
logging.basicConfig(level=logging.ERROR)


def init_sheets():
    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    scopes     = ["https://spreadsheets.google.com/feeds",
                  "https://www.googleapis.com/auth/drive"]
    creds      = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc         = gspread.authorize(creds)
    sh         = gc.open_by_key(SPREADSHEET_ID)

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
    print(f"⚠️  Google Sheets 連線失敗：{e}", flush=True)
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


ts  = load.timescale()
eph = load("de421.bsp")

TARGET_LIBRARY = [
    {"name":"銀河核心",         "ra_hours":17.761,    "dec_degrees":-29.0,  "type":"galaxy",        "min_alt":15,"max_alt":60},
    {"name":"獵戶座",           "ra_hours":84.05/15,  "dec_degrees":-1.20,  "type":"constellation", "min_alt":10,"max_alt":50},
    {"name":"天蠍座",           "ra_hours":16.49,     "dec_degrees":-26.43, "type":"constellation", "min_alt":10,"max_alt":50},
    {"name":"獅子座",           "ra_hours":10.14,     "dec_degrees":11.97,  "type":"constellation", "min_alt":10,"max_alt":70},
    {"name":"仙女座",           "ra_hours":0.712,     "dec_degrees":41.27,  "type":"constellation", "min_alt":10,"max_alt":80},
    {"name":"南十字座",         "ra_hours":12.45,     "dec_degrees":-60.0,  "type":"constellation", "min_alt":5, "max_alt":30},
    {"name":"獵戶座大星雲 M42", "ra_hours":5.588,     "dec_degrees":-5.39,  "type":"nebula",        "min_alt":10,"max_alt":60},
    {"name":"玫瑰星雲 NGC2244", "ra_hours":6.532,     "dec_degrees":4.95,   "type":"nebula",        "min_alt":10,"max_alt":60},
    {"name":"礁湖星雲 M8",      "ra_hours":18.063,    "dec_degrees":-24.38, "type":"nebula",        "min_alt":10,"max_alt":50},
    {"name":"鷹星雲 M16",       "ra_hours":18.313,    "dec_degrees":-13.79, "type":"nebula",        "min_alt":10,"max_alt":60},
    {"name":"猴頭星雲 NGC2174", "ra_hours":6.092,     "dec_degrees":20.30,  "type":"nebula",        "min_alt":10,"max_alt":70},
    {"name":"昆蟲星雲 NGC6302", "ra_hours":17.225,    "dec_degrees":-37.10, "type":"nebula",        "min_alt":8, "max_alt":40},
    {"name":"仙女座星系 M31",   "ra_hours":0.712,     "dec_degrees":41.27,  "type":"nebula",        "min_alt":10,"max_alt":80},
    {"name":"紫金山-ATLAS彗星", "ra_hours":3.20,      "dec_degrees":15.0,   "type":"comet",         "min_alt":10,"max_alt":60},
]

METEOR_SHOWERS = [
    {"name":"象限儀座流星雨","peak_month":1, "peak_day":4,  "zenithal_hourly_rate":120},
    {"name":"英仙座流星雨",  "peak_month":8, "peak_day":12, "zenithal_hourly_rate":100},
    {"name":"雙子座流星雨",  "peak_month":12,"peak_day":14, "zenithal_hourly_rate":150},
    {"name":"獅子座流星雨",  "peak_month":11,"peak_day":17, "zenithal_hourly_rate":15},
    {"name":"天琴座流星雨",  "peak_month":4, "peak_day":22, "zenithal_hourly_rate":18},
]


def get_moon_phase_emoji(p):
    p = p % 1.0
    if p < 0.03 or p > 0.97: return "🌑 新月（最佳拍攝）"
    elif p < 0.22: return "🌒 眉月（尚可）"
    elif p < 0.28: return "🌓 上弦月（有干擾）"
    elif p < 0.47: return "🌔 盈凸月（明顯干擾）"
    elif p < 0.53: return "🌕 滿月（深空不宜）"
    elif p < 0.72: return "🌖 虧凸月（明顯干擾）"
    elif p < 0.78: return "🌗 下弦月（有干擾）"
    else:          return "🌘 殘月（尚可）"


def check_meteor_shower(query_date):
    results = []
    for shower in METEOR_SHOWERS:
        peak = date(query_date.year, shower["peak_month"], shower["peak_day"])
        if abs((query_date - peak).days) <= 3:
            results.append({**shower, "days_to_peak": (peak - query_date).days})
    return results


def compute_target_windows(observer, target, query_dates):
    star = Star(ra_hours=target["ra_hours"], dec_degrees=target["dec_degrees"])
    windows = []
    for d in query_dates:
        for mo in range(0, 10*60, 10):
            dt_tst = datetime(d.year,d.month,d.day,19,0,tzinfo=timezone.utc)+timedelta(minutes=mo)
            dt_utc = dt_tst - timedelta(hours=8)
            t      = ts.from_datetime(dt_utc)
            apparent   = (eph['earth']+observer).at(t).observe(star).apparent()
            alt, az, _ = apparent.altaz()
            if target.get("min_alt",10) <= alt.degrees <= target.get("max_alt",80):
                windows.append({"target_name":target["name"],"target_type":target["type"],
                                 "datetime_tst":dt_tst,
                                 "alt_deg":round(alt.degrees,1),"az_deg":round(az.degrees,1)})
    best = {}
    for w in windows:
        d = w["datetime_tst"].date()
        if d not in best or w["alt_deg"] > best[d]["alt_deg"]:
            best[d] = w
    return list(best.values())


def get_moon_info(observer, query_dates):
    results = []
    for d in query_dates:
        t0 = ts.utc(d.year,d.month,d.day,11)
        mp = almanac.moon_phase(eph,t0)
        results.append({"date":d,
                         "moon_phase_pct":round(float(mp.degrees)/360.0*100,1),
                         "moon_phase_desc":get_moon_phase_emoji(float(mp.degrees)/360.0)})
    return results


def check_weather_multi(lat, lon, query_dates):
    if not query_dates: return {}
    today = date.today()
    max_d = today+timedelta(days=15)
    valid = [d for d in query_dates if today <= d <= max_d]
    fb    = {"cloud_cover":-1,"humidity":-1,"temp_c":-1,"dew_point_c":-1,"dew_risk":False,"good_weather":True}
    if not valid: return {d:fb for d in query_dates}

    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
           f"&hourly=cloud_cover,visibility,relative_humidity_2m,temperature_2m,dew_point_2m"
           f"&start_date={min(valid).isoformat()}&end_date={max(valid).isoformat()}"
           f"&timezone=Asia%2FTaipei")
    raw = requests.get(url,timeout=10).json()
    if "hourly" not in raw: return {d:fb for d in query_dates}

    data = raw["hourly"]
    hi = {}
    for i,t_str in enumerate(data["time"]):
        dt = datetime.fromisoformat(t_str)
        hi[dt] = {"cloud_cover":data["cloud_cover"][i],"humidity":data["relative_humidity_2m"][i],
                  "temp_c":data["temperature_2m"][i],"dew_point_c":data["dew_point_2m"][i]}

    daily = {}
    for d in query_dates:
        if d not in valid: daily[d]=fb; continue
        night = []
        for h in [20,21,22,23,0,1,2]:
            cd = d if h>=20 else d+timedelta(days=1)
            k  = datetime(cd.year,cd.month,cd.day,h)
            if k in hi: night.append(hi[k])
        if night:
            ac = round(sum(x["cloud_cover"] for x in night)/len(night),1)
            ah = round(sum(x["humidity"]    for x in night)/len(night),1)
            at = round(sum(x["temp_c"]       for x in night)/len(night),1)
            ad = round(sum(x["dew_point_c"]  for x in night)/len(night),1)
            daily[d] = {"cloud_cover":ac,"humidity":ah,"temp_c":at,"dew_point_c":ad,
                        "dew_risk":(at-ad)<3.0,"good_weather":ac<=40}
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
    resp = client.messages.create(model="claude-sonnet-4-5",max_tokens=400,system=system,
                                   messages=[{"role":"user","content":user_query}])
    text = re.sub(r"```(?:json)?|```","",resp.content[0].text.strip()).strip()
    return json.loads(text)


def match_targets(target_names):
    if not target_names: return TARGET_LIBRARY
    matched = []
    for name in target_names:
        for t in TARGET_LIBRARY:
            if name.lower() in t["name"].lower() or t["name"].lower() in name.lower():
                if t not in matched: matched.append(t)
    return matched if matched else TARGET_LIBRARY


def run_query(user_query):
    intent      = parse_intent(user_query)
    observer    = wgs84.latlon(intent["lat"],intent["lon"])
    date_start  = date.fromisoformat(intent["date_start"])
    date_end    = date.fromisoformat(intent["date_end"])
    query_dates = [date_start+timedelta(days=i) for i in range((date_end-date_start).days+1)]
    all_windows = []
    for target in match_targets(intent.get("targets",[])):
        all_windows.extend(compute_target_windows(observer,target,query_dates))
    moon_info = get_moon_info(observer,query_dates)
    showers   = [s for d in query_dates for s in check_meteor_shower(d)]
    weather   = check_weather_multi(intent["lat"],intent["lon"],query_dates)
    for w in all_windows:
        wx = weather.get(w["datetime_tst"].date(),{})
        w.update({"cloud_cover":wx.get("cloud_cover",-1),"humidity":wx.get("humidity",-1),
                  "temp_c":wx.get("temp_c",-1),"dew_point_c":wx.get("dew_point_c",-1),
                  "dew_risk":wx.get("dew_risk",False),"good_weather":wx.get("good_weather",False)})
    good = [w for w in all_windows if w.get("good_weather",False)]
    return {"intent":intent,"good_windows":good[:10],"moon_info":moon_info,"showers":showers}


def generate_reply(result):
    intent=result["intent"]; good=result["good_windows"]
    moon_info=result["moon_info"]; showers=result["showers"]
    ws = json.dumps([{"標的":w["target_name"],
        "日期時間":w["datetime_tst"].strftime("%m/%d %H:%M TST"),
        "仰角":f"{w['alt_deg']}°","方位角":f"{w['az_deg']}°",
        "雲量":f"{w['cloud_cover']}%" if w['cloud_cover']>=0 else "預報範圍外",
        "濕度":f"{w['humidity']}%" if w['humidity']>=0 else "N/A",
        "溫度":f"{w['temp_c']}°C" if w['temp_c']>=-50 else "N/A",
        "結露風險":w["dew_risk"]} for w in good],ensure_ascii=False,indent=2)
    ms = json.dumps([{"日期":m["date"].isoformat(),"月相":m["moon_phase_desc"]} for m in moon_info],ensure_ascii=False)
    ss = json.dumps([{"流星雨":s["name"],"距極大期":f"{s['days_to_peak']:+d}天","ZHR":s["zenithal_hourly_rate"]} for s in showers],ensure_ascii=False) if showers else "無"
    system = """你是專業天文攝影顧問，熟悉台灣各地拍攝環境。繁體中文，親切專業。
回覆格式：
【結論】最佳選擇一句話
【推薦時刻】top 3，開放探索依標的分組
【月相影響】對深空攝影的影響
【氣象分析】雲量/結露風險
【裝備提醒】針對地點特性
若有流星雨加【流星雨加碼】
總長不超過 380 字。"""
    resp = client.messages.create(model="claude-sonnet-4-5",max_tokens=800,system=system,
        messages=[{"role":"user","content":
            f"查詢類型：{'指定標的' if intent['query_type']=='A' else '開放探索'}\n"
            f"地點：{intent['location_name']}\n日期：{intent['date_start']} ～ {intent['date_end']}\n"
            f"候選時刻：\n{ws if good else '無符合條件的時刻'}\n月相：{ms}\n流星雨：{ss}"}])
    return resp.content[0].text


# ── 對話狀態 ──────────────────────────────────────────────────
WAITING_WISH    = 1
user_last_query = {}


def make_feedback_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👍 氣象準確",   callback_data="rate_good"),
         InlineKeyboardButton("👎 氣象不準",   callback_data="rate_bad")],
        [InlineKeyboardButton("💡 許願 / 建議", callback_data="wish")],
    ])


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text     = update.message.text.strip()
    username = update.effective_user.first_name or "朋友"
    user_id  = update.effective_user.id
    chat_id  = update.effective_chat.id
    print(f"[收到] {username}: {text}", flush=True)

    if text in ["/start","/help","help","說明"]:
        await update.message.reply_text(
            "🔭 *天文攝影查詢 Bot*\n\n直接用自然語言問我，例如：\n"
            "• `4月15日 合歡山 銀河`\n• `這個週末 阿里山 有什麼可以拍？`\n"
            "• `5月1日到3日 墾丁 天蠍座`\n\n我會幫你計算最佳觀測時刻、月相和氣象條件 🌌",
            parse_mode="Markdown")
        return ConversationHandler.END

    thinking_msg = await update.message.reply_text("🔭 計算中，請稍候...")
    try:
        result = run_query(text)
        reply  = generate_reply(result)
        user_last_query[chat_id] = text
        log_query(username, user_id, text, result["intent"])
        await thinking_msg.delete()
        await update.message.reply_text(reply, parse_mode="Markdown",
                                        reply_markup=make_feedback_keyboard())
        print("[回覆] 完成", flush=True)
    except Exception as e:
        await thinking_msg.delete()
        await update.message.reply_text(f"⚠️ 發生錯誤，請重新嘗試。\n\n`{type(e).__name__}: {e}`",
                                        parse_mode="Markdown")
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

    if data == "rate_good":
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


async def main():
    request = HTTPXRequest(connection_pool_size=8,
                           read_timeout=30, write_timeout=30,
                           connect_timeout=30, pool_timeout=30)
    app = Application.builder().token(TELEGRAM_TOKEN).request(request).build()

    conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message),
            MessageHandler(filters.COMMAND, handle_message),
            CallbackQueryHandler(handle_callback),
        ],
        states={WAITING_WISH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_wish)]},
        fallbacks=[MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)],
    )
    app.add_handler(conv)

    print("🚀 Bot 啟動中...", flush=True)
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES,
                                        drop_pending_updates=True)
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
    asyncio.run(main())
