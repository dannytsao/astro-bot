# CHANGELOG

## 2026-06-14（服務選單）

### 新增

- Quick Reply 服務選單：輸入「選單」、「功能」、`/menu` 即可叫出
- 📅 **15天景點氣象評估**：選擇後輸入景點名稱，Bot 自動評估未來 15 天每晚氣象條件
- ❓ **使用說明**：快捷顯示查詢範例與操作說明
- `handle_postback` 新增 `menu_weather_15d` 與 `menu_help` 處理
- `handle_message` 新增 `waiting_weather_location` 狀態，接收景點後背景計算並回覆

## 2026-06-14

### 改進

- 導入使用者提供的 100 個台灣星空攝影景點，production 地點資料庫擴充為 113 筆 approved 地點。
- 每次查詢回覆開頭新增「地點解析」區塊，固定顯示使用者輸入、解析地點、經緯度、Google Maps 連結、資料來源與信心等級。
- 移除 `高雄中之關步道（南橫）` 的廣義 alias `南橫`，避免「南橫埡口」被錯誤解析成中之關步道。
- 地點解析失敗時會明確告知地點不在資料庫，並自動寫入地點許願池，再請使用者補座標。

### 後續待辦

- 設計「地點許願池」處理流程：定期審核 Google Sheets 中的地點許願，補來源、座標、別名、可及性與安全注意事項，再升級進 `data/taiwan_locations.json`。
- 將許願池分級：高頻查詢地點優先、座標明確者優先、疑似模糊地名需先反問或拆成具體景點。

## 2026-06-13

### 改進

- 將 production 地點資料從 `main.py` 常數移到 `data/taiwan_locations.json`，只載入 `review_status: "approved"` 的地點。
- 地點解析支援 JSON aliases，未來擴充地點不需修改 Python code。
- 建立本機 Codex skill `taiwan-location-research`，用於搜尋台灣本島與離島地點候選座標、記錄來源與產出待審 JSON。
- 新增地點 JSON validator，檢查座標範圍、必填欄位、confidence 與 review status。
- 修正等待補座標狀態卡住的問題：若使用者改輸入新的正常查詢，Bot 會取消上一筆 pending 並處理新查詢。

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
- 修正內建地點 regression：使用者文字中直接出現合歡山、墾丁、阿里山等內建地點時，必須優先使用內建座標，不受 LLM 防幻覺規則誤擋。

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
