# CHANGELOG

## 2026-06-21（文件與開發方向更新）

> 本次無功能代碼變更。以下為產品範圍釐清與開發方向調整，已同步至 ROADMAP.md 與 SUBJECT_SCOPE.md。

### 開發方向變動（ROADMAP）

- **Phase 3A exit gate 修正**：P90 < 15 秒移出 Phase 3A exit gate，確認為 Phase 3B #1 開發項目；3A exit gate 僅保留功能完成度（CCI、地點比較、多題材 CCI 框架）
- **Phase 3A #5 範圍確認**：in scope 為流星雨、月景、月蝕、彗星第一層（氣象 CCI 不含方位角）及深空題材設備適配標籤；雲海、日落日出、懸日、日月行星排除本季；日蝕至 2032-11-03 前不開發
- **Phase 3B 新增三項需求**：#12 Meteoblue 視寧度資料評估、#13 IMO Live ZHR 流星雨即時修正、#14 南十字座地點限制強化（`southern_horizon_clear` 欄位）
- **Phase 3B #11 彗星座標整合**：MPC + JPL Horizons 每日凌晨 2 點快取，快取失效降級第一層
- **彗星從「待驗證後才做」移至 Phase 3B #11**：開發條件明確，不再列為待驗證
- **Meteoblue 從「待驗證」升級為 Phase 3B #12 主動評估項目**

### 新增文件

- 新建 `SUBJECT_SCOPE.md`：各攝影題材 CCI 支援範圍評估，含 in/out scope 原因、應對方式、開放條件、設備適配性、地點相依性限制

### 文件更新

- `README.md`：功能說明同步至 Phase 3A 完成狀態（CCI、地點比較、最佳地點排名）；新增「即將推出」區塊；「限制」重構為「目前不支援」並補齊各題材排除原因
- `ROADMAP.md`：Phase 3A #5 說明更新、exit gate 修正、Phase 3B 新增 #12–#14、Phase 4B #2 器材記憶說明補充

### 流程改善

- `HERMAS_AGENT.md` 新增「文件管理流程」區塊：定義各文件職責分工（CHANGELOG / ROADMAP / SUBJECT_SCOPE.md / README.md / HERMAS_AGENT.md）、更新時機、衝突處理原則
- `HERMAS_AGENT.md` 新增「流程改善」區塊：定義何時觸發流程回顧、如何更新規範、並建立流程改善記錄表

---

## 2026-06-21（今晚 / 週末最佳地點 MVP）

### 新增

- **最佳地點排名（Phase 3A #4 MVP）**：支援「今晚哪裡最好拍銀河」「這週末去哪裡拍」等全台地點排名查詢
  - 新增 `is_best_location_query()`：在單一地點解析前攔截「哪裡 / 去哪裡 / 最佳地點」類查詢，避免被誤判為缺少地點
  - 新增 `run_best_location_ranking()`：對 production approved 地點與使用者曾提供座標的自定義地點平行計算 CCI，回覆 Top 6
  - 回覆包含 CCI、日期、雲量、暗空窗口、能見度、結露風險與目標可見性
  - 為控制回覆時間，MVP 全台排名暫不逐地呼叫 7Timer；視寧度 / 透明度以中性值處理，精查仍建議查單一地點

### 改進

- **CCI 納入風速**：Open-Meteo 新增夜間最大風速與蒲福風級，CCI 加入風速因子
  - 銀河 / 星野最高容忍 3 級風；深空最高容忍 2 級風
  - 超過容忍風級時，風速因子視為出勤障礙
- **區域排名**：最佳地點查詢若指定北部 / 中部 / 南部 / 東部 / 離島，只在該區域內取前 6 名；未指定則全區前 6 名
- **排名精排**：全區排名先用快速 CCI 篩選，再對可能進榜地點補 7Timer 視寧度 / 透明度精排，避免單點查詢高分地點在全區排名中被中性值低估

## 2026-06-17（地點比較模式）

### 新增

- **地點比較模式（Phase 3A #3）**：支援「合歡山 vs 阿里山 這週末銀河」語氣查詢
  - `parse_intent()` system prompt 新增 compare_mode 偵測（「vs」「還是」「比較」「哪裡比較好」等語氣）
  - `resolve_compare_location()` 輔助函式：從審核地點 DB 解析比較地點座標
  - `generate_comparison_reply(result_a, result_b)`：兩地點 CCI 並排比較 + LLM 生成建議
  - `process_and_reply()` 新增 compare_mode 分支：ThreadPoolExecutor 並行跑兩次 run_query
  - 差距 < 10% 自動標注「條件相近」；兩地均不適合時明確說改期
  - 若比較地點不在審核 DB，顯示明確提示而非進入座標補充流程

## 2026-06-17（反樂觀守則 + 風險旗幟）

### 改進

- **反樂觀守則**：system prompt 新增強制輸出規則
  - CCI < 40：該日結論第一句必須是「不建議/不值得出勤」
  - CCI 40–59：禁止出現「仍有機會」「值得一試」等模糊鼓勵語氣
  - 所有高風險因子（得分 ≤ 15）必須在回覆中明確點名，不可合併省略
- **風險旗幟（Risk Flags）**：`generate_reply()` 從 CCI breakdown 自動提取得分 ≤ 15 的因子，以「必須點出的風險」清單形式傳入 LLM user content，LLM 不得略過

## 2026-06-17（出勤信心指數 CCI）

### 新增

- **CCI（出勤信心指數）**：純 Python 計算，每晚 0–100 分，不依賴 LLM。
  - 六個加權因子：雲量 35%、有效暗空窗口 25%、視寧度 15%、透明度 10%、目標可見性 10%、結露風險 5%
  - 標籤：✅ 強烈推薦（≥80）、🟢 值得出勤（≥60）、⚠️ 謹慎考慮（≥40）、🟠 不建議（≥20）、❌ 不值得出勤（<20）
  - 資料缺失時安全降級：氣象缺失→雲量得 0，7Timer 缺失→視寧度/透明度給中性 50 分
- `compute_cci_for_date(weather_day, moon_info_day, seeing_day, windows_for_date)` 新函式
- `run_query()` 返回值新增 `cci_by_date` dict（以 date 為 key）
- `generate_reply()` system prompt【結論】格式更新，要求 LLM 直接使用 CCI icon 和分數
- CCI 明細以 JSON 形式傳入 LLM user content，讓回覆包含「信心度 XX%」

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
