# CHANGELOG

## 2026-06-12

### Production 修復

- 建立 Render Web Service `astro-bot-web`，讓 LINE Webhook 有正式 HTTP endpoint。
- 新增 `app.py` 作為 Render 匯入 fallback，避免 autodetect 使用 `gunicorn app:app` 時找不到 Flask app。
- 將 LINE Developers webhook 指向 `https://astro-bot-web-xlny.onrender.com/callback`。
- 修正 LINE access token 過期時造成 `/callback` 500 的問題；LINE API 401 會被清楚記錄，不再讓 webhook 崩潰。
- `/healthz` 新增 Google Sheets、OpenRouter、LINE access token 與 deployed version 診斷。
- 地點解析失敗時改為反問使用者補座標；支援 `座標：23.124, 121.216`、`北緯 23.124 東經 121.216`、`lat/lon` 格式。
- 補座標會做全球合法範圍硬檢查；台灣寬鬆範圍只提示 warning，不阻擋計算。
- 防止地點解析幻覺替換：若使用者輸入「飛行場」等籠統地點，Bot 不可自動改用「合歡山」等其他內建地點，需反問補座標。
- 建立「無真憑實據不猜測」資料原則：氣象、視寧度、天體位置缺資料時必須明說無資料，不可由 LLM 推測。
- `查詢記錄` 新增資料品質摘要與 JSON，記錄氣象/視寧度/天體位置/地點解析的缺資料狀態。

### Lessons Learned

- Webhook Verify 成功只代表 LINE 可連到 callback，不代表 Bot 可以成功回覆。
- Render Worker 不能作為 LINE Webhook endpoint；production LINE Bot 必須跑在 Web Service。
- 新增 `LESSONS_LEARNED_2026-06-12.md`，記錄今天完整排查流程、根因與下次部署檢查清單。

## 2026-06-10

### 修正

- **結露臨界值收緊**：T−Td 閾值從 3.0°C 改為 1.5°C，更符合台灣山區實際起霧門檻，減少漏報結露風險的情況
- **未知 weather_status 安全處理**：新增 `elif weather_status == "unknown":` 分支，避免未預期狀態導致 `mw_str` 未初始化造成的執行錯誤

### 改進

- **銀河構圖區塊動態化**：新增 `is_galaxy_query` 旗標，銀河核心方位角計算與回覆區塊只在查詢含銀河標的時啟用，非銀河查詢不再顯示無關構圖資訊，減少回覆噪音
- **曝光建議加入 system prompt**：
  - 快門：500 法則（500 ÷ 焦距，有赤道儀可延長 2～4 倍）
  - ISO：依月相分段（新月 1600～3200；眉月/下弦 800～1600；明顯月光 400～800）
  - 光圈：最大光圈（f/1.4～2.8）或 f/4 以上兼顧銳利度
