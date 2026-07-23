# CHANGELOG

## 2026-07-23（Go/No-Go 準確度：CCI 改以觀測目標起落區間的氣象為準）

### 根因

- 原本 `check_weather_multi()` 把整夜壓成固定 20:00–02:00 七小時的平均值，03:00–06:00 完全不採計；7Timer 視寧度亦同
- CCI 的雲量、結露、風速、視寧度、透明度分數全部來自這個固定區間平均，與目標實際起落時間無關；冬春季銀河核心 02:00–05:00 才升起，Go/No-Go 卻是用目標不在天上的時段判斷；台灣山區入夜多雲、凌晨轉晴的天氣型態會被平均值誤判為 No-Go

### 修改

- `weather.py`：Open-Meteo 抓取範圍擴為當日 18:00 → 隔日 06:00（end_date +1 天，超出預報範圍時自動退回原範圍重試）；每日結果新增 `hourly_night` 逐小時資料、`cloud_cover_max`、`min_temp_dew_diff`；新增 `aggregate_weather_interval()`（雲量=區間平均+峰值、結露=區間內最差小時 T−Td、風速=區間最大）與 `aggregate_seeing_interval()`（7Timer 3 小時時點以 [t, t+3h) 與區間交集納入）
- `astro.py`：`_best_target_windows_at_times()` 除最佳時刻外同步記錄每目標當晚可見區間 `window_start_tst` / `window_end_tst`（暗空與 18:00–06:00 fallback 掃描皆適用）
- `cci.py`：新增 `resolve_observation_interval()` 依題材決定判斷區間——default/深空=目標可見區間聯集邊界（無窗口退暗空窗口）、meteor/comet_layer1=暗空窗口邊界、moonscape/lunar_eclipse=月亮在天區間∩夜間（雙段取重疊較長者）；雲量 breakdown 標示聚合區間與峰值，峰值與平均差 ≥30 且峰值 ≥60 時輸出「雲量起伏大」風險旗幟；結露評分改用區間內最差小時 T−Td；區間無法解析時 fallback 整夜平均並明確標註（不猜測原則）
- `main.py`：`run_query()` 與 `rank_location_candidate()`（今晚/週末最佳地點排名）都改用區間聚合結果餵 `compute_cci_for_date`，排名列顯示數值與 CCI 同一口徑；`generate_reply` context 新增每晚觀測區間與逐時雲量趨勢（隔 2 小時取樣），並明確指示 LLM 不可外推未列出的時段

### 驗證

- 新增 `tests/test_interval_aggregation.py` 27 個測試：區間聚合數值正確性（平均/峰值/最差小時 T−Td/最大風速/短區間對齊整點）、無資料與無交集 fallback、各 profile 區間解析、窗口 span、CCI 區間標示與風險旗幟、同晚「整夜平均 60% vs 區間 10%」Go/No-Go 分數翻轉、Open-Meteo 逐小時涵蓋 18:00–06:00
- 完整 pytest：133 passed（106 既有 + 27 新增）；`py_compile` 六模組通過；`git diff --check` 通過
- 尚未經真實 LINE 驗證；部署後需以實際查詢確認回覆中的區間標示與雲量趨勢描述

## 2026-07-14（修復：確認模糊地點後重複要求確認）

- 修正使用者點選「是，使用這個地點」後，再次收到相同地點確認提示、未接續計算的 production bug
- 根因是 `process_and_reply()` 已先正規化確認後的 intent 並消耗一次性 `_confirmed_location`，`run_query()` 隨後又對同一 intent 重複正規化，因原查詢仍含錯字而再次觸發模糊候選
- `run_query()` 現在直接使用呼叫端已正規化的 prefetched intent；只有未提供 intent 的直接呼叫才自行解析，避免重複正規化與確認循環
- 新增 failing-first 回歸測試，確認已正規化的確認 intent 會直接進入計算階段，不再呼叫 `normalize_intent()`
- Render 部署版本 `7cf187a06ec4` health checks 全綠；使用者以真實 LINE 重測後確認「是，使用這個地點」會正確接續使用阿里山完成計算，Phase 3B #4 可結案

## 2026-07-14（Phase 3B #4 強化：安全模糊地點候選）

- 保留正式名稱與 `aliases` 精確命中為第一優先；只有精確比對失敗時才計算字元相似度，避免破壞既有 canonical／別名行為
- 模糊比對只提出單一候選，不直接使用座標；LINE 以「是，使用這個地點／不是，我要補座標」要求使用者確認，確認前不進入天文與氣象計算
- 候選採依字數調整的最低相似度、第一與第二候選差距門檻，並排除「觀景台、車站、燈塔」等過於籠統的短詞；結果不明確時維持原補座標流程
- 使用者確認後才套用 canonical 座標並接續原查詢；拒絕後記入地點許願池並轉入補座標狀態。等待確認狀態沿用 Google Sheets 持久化，可跨 Render 重啟
- 新增八個回歸測試，覆蓋單字錯誤候選、籠統／無關詞拒絕、LLM 擅自正規化防線、未確認不得計算、確認採用 canonical 座標、一次性確認標記防循環，以及拒絕後補座標轉場

## 2026-07-14（Phase 3B #4：自定義地點別名支援）

- Google Sheets「自定義地點」新增第 6 欄「別名」；既有 5 欄工作表會在啟動時自動擴充並補上欄名，新存地點保留空白別名欄供人工維護
- `load_custom_locations()` 解析半形逗號、中文逗號、頓號或換行分隔的別名，去除空白、重複值及與 canonical 名稱相同的值
- 節流重載現在不只撿新增列，也會更新既有 `user-provided` 地點的座標與別名；approved 地點仍不可被自定義 Sheet 同名列覆寫
- 查詢比對沿用既有 `location_search_terms()` 精確別名機制；例如 canonical「南橫摩天」加入「南橫魔天」後，語音轉錄同音字仍會解析回 canonical 地點，不新增模糊或拼音猜測
- 新增四個回歸測試，覆蓋別名解析與 canonical 命中、既有自定義地點別名重載、approved 同名保護，以及新存列包含空白別名欄

## 2026-07-14（Phase 3B #2 收尾：等待提示與 production 樣本同步）

- LINE 一般文字查詢與 15 天氣象流程的即時等待提示，皆從過時的「約 30～60 秒」調整為「通常約 10～20 秒，複雜查詢可能較久」
- 補記四筆正式環境樣本：文字開放探索 9.95 秒；單日語音查詢端到端 9.93 秒與 10.65 秒；六日範圍語音查詢端到端 16.61 秒
- 單日文字查詢流程三筆為 9.95、7.51、8.22 秒，平均 8.56 秒；樣本量仍不足以宣稱統計 P90，後續持續以 Render `[耗時]` log 追蹤

## 2026-07-14（Phase 3B #2：開放探索型查詢批次天文計算）

### 根因與修復

- 開放探索型查詢會對 117 個標的逐一呼叫 `compute_target_windows()`，同一時間點因此重複執行 Skyfield 的 observer position、apparent position 與 alt/az 計算。Profiler baseline 為 6,957 組 target-time 計算、7.746 秒，其中 `.at()` 累積 5.375 秒、`.apparent()` 累積 4.796 秒
- 新增 `compute_target_windows_for_targets()`，利用 Skyfield vector `Star` 在每個時間點一次計算全部標的；保留原 `compute_target_windows()` 作為單一標的相容入口
- `run_query()` 與 `rank_location_candidate()` 改用批次入口；暗空窗口、整夜 fallback、最低／最高仰角、輸出順序與原有結果維持一致

### 驗證

- 新增批次與逐標的結果等價測試；完整 pytest：93 passed
- `py_compile` 六個 production 模組與 `git diff --check` 通過
- 117 標的 benchmark：暗空窗口 7.746 秒降至 0.173 秒；整夜 fallback 0.092 秒
- 本地 `run_query()` 使用路徑：開放探索型 `compute_target_windows` 0.13 秒、完整天文／CCI 路徑 0.21 秒；指定銀河查詢 0.11 秒。外部氣象 API 以固定 fixture 取代，因此此數值不包含正式環境網路與 LLM 延遲
- Production 真實 LINE 開放探索查詢「7/17 南橫啞口適合拍星嗎」驗證通過：意圖解析 1.37 秒、117 標的 `compute_target_windows` 0.99 秒、氣象與視寧度 1.06 秒、`run_query` 2.64 秒、`generate_reply` 4.98 秒，完整回覆總計 9.95 秒。Phase 3B #2 功能完成；這是一筆代表性實測，統計上的 P90 仍需累積多筆 production 查詢持續追蹤

## 2026-07-14（開發方向調整：自定義地點別名支援正式列為重要改善方案）

> 本次無功能代碼變更。使用者明確要求把語音輸入測試時發現的「自定義地點別名支援」問題（見上一則 CHANGELOG 條目）列為重要改善方案，已同步至 ROADMAP.md 與 CLAUDE.md。

`ROADMAP.md` Phase 3B 表格新增為 #4（原 #4–#15 依序後移為 #5–#16）：自定義地點目前不支援別名比對，中文同音字（如「摩」「魔」）可能讓語音查詢誤判已存地點為未知。修法方向已明確（擴充既有 `aliases` 機制到自定義地點，非新建模糊比對系統），但**與 #5–#16 的相對優先順序尚未由使用者決定**，暫列在 #4 位置，之後排序時需再確認，不可自行假設它排在其他項目之前。

## 2026-07-14（Phase 3B #3 完成：語音輸入上線並通過真實 LINE 測試）

### 驗證結果

Push 後使用者以真實 LINE 帳號用語音測試兩種情境，皆通過：

- **已知地點 happy path**：語音「今晚合歡山可不可以拍銀河」→ 轉錄 confidence=high，語音處理僅 2.31s，逐字稿正確；正確接續既有查詢流程（意圖解析、`run_query`、CCI 計算、`generate_reply`），總計 7.67s
- **未知地點安全機制**：語音「7月20號南橫魔天可不可以拍銀河」→ 正確觸發既有的補座標流程與許願池自動記錄，未因語音輸入繞過「不猜測」原則。人工確認 LINE 上有正確顯示補座標提示

唯一原本不確定的技術細節（OpenRouter `input_audio` 內容區塊的 `format` 欄位應填 `"mp4"`）第一次上線就正確，未觸發任何格式錯誤

### 發現：中文同音字可能造成語音查詢誤判已存地點為未知（已記錄，非本次處理範圍）

測試過程中，「南橫摩天」被語音辨識為「南橫魔天」（摩／魔同音，皆唸 mó，任何語音辨識系統都會遇到這個模糊性，非本次程式邏輯缺陷）。若使用者之後把「南橫摩天」存進自定義地點，未來語音查詢若又被辨識成「南橫魔天」等同音變體，會因為現有比對邏輯只吃 `aliases` 陣列、而自定義地點目前 `aliases` 寫死為空陣列，導致誤判為未知地點。

正確修法是擴充自定義地點的別名支援（比照 113 個已審核地點既有的 `aliases` 機制），不是新建一套模糊比對系統——`normalize_location_name_text()` 現有的錯字修正只在儲存當下生效，查詢比對路徑（`find_known_location_in_query()`）完全不會經過它。此問題目前尚未實際發生（「南橫摩天」尚未存入資料庫），列為已知待辦，非本次處理範圍。

## 2026-07-14（Phase 3B #3 Day 2：語音轉錄單元測試，準備部署）

### 新增

- `tests/test_voice_transcription.py`（10 個測試）：`transcribe_voice_query()` 的高/低信心判斷、空逐字稿強制降級、非法信心值防禦性夾值、JSON 格式錯誤重試、兩次皆失敗安全降級、非物件 JSON（如純陣列）觸發重試、音訊 base64 編碼正確性、`MAX_VOICE_AUDIO_BYTES`／`VoiceTranscriptionError` 基本檢查

### 驗證

- `python3 -m pytest tests/ -q`：92 passed（82 既有 + 10 新增，Day 1 的手動驗證腳本已正式轉為 pytest）
- `py_compile` 全模組通過
- 待辦：push 後需請使用者用真實 LINE 語音訊息實測，確認 OpenRouter `input_audio` 的 `format` 欄位（目前寫 `"mp4"`）是否正確——這是唯一沒辦法在本地驗證的環節

## 2026-07-14（Phase 3B #3 Day 1：語音輸入核心邏輯，尚未部署）

### 新增

- **`AudioMessage` webhook handler**（`main.py`）：收到 LINE 語音訊息後立即回覆「🎤 語音辨識中，請稍候...」，丟進既有的 `MESSAGE_EXECUTOR` 背景執行緒池處理，不佔用 reply_token 30 秒時限
- **`transcribe_voice_query()`**：呼叫 OpenRouter 多模態音訊輸入（`input_audio` 內容區塊），沿用現有 `OPENROUTER_API_KEY`／`call_openrouter()`，不需另外申請 API key。要求 LLM 同時回傳逐字稿與自我評估的信心等級（high/medium/low），JSON 格式錯誤時比照 `parse_intent()` 重試一次
  - 信心不足或逐字稿為空 → 一律降級為 low，不送入查詢流程，回覆使用者改用文字輸入（不猜測原則：模型自稱高信心但逐字稿是空的，仍強制視為 low）
  - 兩次嘗試都拿不到合法 JSON → 安全降級回傳 `{transcript:"", confidence:"low"}`，不拋例外中斷流程
- **`process_voice_and_reply()`**：下載語音（`line_bot_api.get_message_content`）→ 大小防呆（>15MB 直接拒絕，對應 Gemini 音訊輸入上限）→ 轉錄 → 信心足夠時接續既有 `process_and_reply()` 查詢流程，用 `reply_prefix` 帶出「🎤 語音辨識結果：「...」」讓使用者能發現辨識錯誤
- `[耗時]` log：語音下載、轉錄呼叫、處理總計，比照今天稍早建立的規範

### 驗證

- `py_compile` 全模組通過；既有 82 個 pytest **全數維持通過，無 regression**——這次改動對既有文字查詢流程零影響（純新增 `AudioMessage` handler，未修改 `TextMessage` handler 任何邏輯）
- 針對 `transcribe_voice_query()` 寫了獨立驗證腳本（mock `call_openrouter`），涵蓋：高/低信心正確判斷、空逐字稿強制降級、非法信心值防禦性夾值、JSON 錯誤重試成功、兩次皆失敗安全降級不拋例外、音訊 base64 編碼正確性——全數通過

### 尚未解決

- OpenRouter `input_audio` 內容區塊的 `format` 欄位該填 `"mp4"`／`"aac"`／`"m4a"` 何者，沒有查到權威文件明確寫死，目前先寫 `"mp4"`，需要 Day 2 用真實語音訊息實測才能確認是否正確
- **本次僅完成本地實作與驗證，故意不 commit/push**——依照跟使用者議定的分階段計畫，語音功能要等 Day 2 補齊單元測試、跑完整 dry-run gate、且使用者用真實 LINE 語音測試過，才會上 production

### 開發方向

使用者明確要求：這次開發過程中不能影響既有用戶使用；若既有功能出問題，需優先處理既有問題，語音功能開發可暫停

> 本次無功能代碼變更。開發方向調整，已同步至 ROADMAP.md 與 CLAUDE.md。

使用者明確決定將「語音輸入支援」（原 Phase 3B #11）提前，優先於地點資料庫擴充、備案地點、多日最佳夜、器材建議、許願池審核流程、雲海/霧判斷、日月行星規劃、15天日曆優化（原 #3–#10）。理由：目前用戶只能用文字輸入查詢，希望開放直接對 LINE Bot 語音查詢；回覆維持現有文字格式，不做語音回覆。

`ROADMAP.md` Phase 3B 表格已重新編號（語音輸入支援移至新的 #3）；`CLAUDE.md` Next Development Priority 同步更新。P90 回覆速度優化（原 #2）本次仍保留在語音輸入之前，因為已有進行中的成果（見上一則 CHANGELOG 條目），且尚有已診斷但暫緩的已知問題（開放探索型查詢 117 標的暴力掃描）。

實作語音輸入前，需先查證（尚未查證，下一步）：
1. LINE 語音訊息（AAC 格式）webhook 實際傳遞機制，是否需另外呼叫 LINE Content API 下載音訊二進位內容
2. Google Gemini API 現行（2026）對 AAC 格式的直接支援程度，是否需要轉檔
3. 是否需要新增 `GEMINI_API_KEY`（目前專案只有 `OPENROUTER_API_KEY`），其定價與免費額度

## 2026-07-14（Phase 3B #2 續：Render 環境變數補齊，OpenRouter 模型改用 Gemini 2.5 Flash）

### 問題描述

`render.yaml` 從一開始就宣告 `OPENROUTER_MODEL: google/gemini-2.5-flash`、`OPENROUTER_FALLBACK_MODELS: google/gemini-2.5-flash,openai/gpt-4o-mini`，但實際檢查 Render Dashboard 的 Environment 設定，才發現這兩個變數（以及 `GOOGLE_SPREADSHEET_ID`、`OPENROUTER_SITE_URL`、`OPENROUTER_APP_NAME`）從來沒有真的被加進 Render——production 只設定了 4 個機密類變數（`GOOGLE_CREDENTIALS_JSON`、`LINE_CHANNEL_ACCESS_TOKEN`、`LINE_CHANNEL_SECRET`、`OPENROUTER_API_KEY`）。`render.yaml` 的宣告從未真正生效，`OPENROUTER_MODEL` 因此一直悄悄退回 `main.py` 寫死的預設值 `anthropic/claude-sonnet-4.5`——一個明顯 over-spec、也明顯拖慢 `generate_reply()` 的模型選擇，且完全不是程式邏輯或任何人刻意選的，是設定缺口。

### 處理

- 使用者直接在 Render Dashboard 補上 `OPENROUTER_MODEL=google/gemini-2.5-flash`、`OPENROUTER_FALLBACK_MODELS=google/gemini-2.5-flash,openai/gpt-4o-mini`，手動觸發重新部署
- 純環境變數變更，本次無程式碼異動
- `GOOGLE_SPREADSHEET_ID`、`OPENROUTER_SITE_URL`、`OPENROUTER_APP_NAME` 三個變數目前雖與程式碼預設值巧合相同、暫無實際影響，仍建議之後一併補上，避免日後程式碼預設值變動時 Render 沒跟著變而悄悄跑掉

### 驗證結果

- `/healthz` 確認：`openrouter_model_source` 從 `"default"` 變成 `"OPENROUTER_MODEL"`，`openrouter_model` 顯示 `google/gemini-2.5-flash`
- 真實查詢比對（「7/17 南橫啞口適合拍星嗎」，開放探索型，會觸發已知的 117 標的耗時問題）：`generate_reply` 從 Claude Sonnet 4.5 時期的 18.23s 降到 **4.21s**，降幅 77%，與研究到的「Gemini 2.5 Flash 針對低延遲優化」的公開資料吻合
- 使用者確認回覆品質與格式正常（純文字格式維持、CCI 與風險旗幟未受影響），初步判斷換模型未犧牲準確度；後續累積更多樣本可再驗證
- `compute_target_windows`（117 標的暴力掃描）耗時不受這次變更影響，維持先前 20+ 秒的已知、暫緩處理的問題

### 為什麼選 Gemini 2.5 Flash 而非 OpenRouter 免費模型

有考慮過 OpenRouter 上真正 $0 的 `:free` 模型（例如 DeepSeek R1、Llama 3.3 70B 等），但查到的限制對正式營運的 LINE Bot 風險偏高：免費額度僅每天 50 次請求（除非帳號歷史儲值滿 $10 美元才提升到 1000 次/天）、每分鐘上限 20 次，且免費模型陣容會不定期輪替下架，不符合這個專案重視的「可預期、不猜測」風格。Gemini 2.5 Flash 雖非完全免費，但比 Claude Sonnet 4.5 便宜約 24 倍，且是 `render.yaml` 從一開始就規劃好的方向，風險遠低於賭免費模型的配額穩定性

## 2026-07-14（Phase 3B #2 續：`run_query()` 內部細部耗時記錄）

### 背景

上一版 `[耗時]` log 上線後，取得兩筆真實生產環境數據：「7/17 日月潭銀河機會」run_query 僅 1.49s；「7/17 南橫啞口適合拍星嗎」run_query 高達 22.66s / 25.11s（同一查詢 2 分鐘內重複兩次結果一致，排除節流重新載入是原因——重複時未觸發 `[自定義地點]` 重新載入 log，run_query 仍然慢，證實與這次「自定義地點重新載入」修復無關）。generate_reply 兩次都穩定在 18–20s，暫時排除為變因。

由於 `run_query()` 內天氣/視寧度取得之後的所有計算都是對已取回資料的純運算（CCI 加權、視窗合併等），理論上應在毫秒等級；真正的懷疑對象是天氣查詢之前的 `normalize_intent()`、`get_moon_info()`、`compute_target_windows()`、`check_unsupported()` 這幾步。不用猜的，直接加 log 讓下一次真實查詢自己說話。

### 新增

- `run_query()` 內新增細部 `[耗時]` log：`normalize_intent`、`get_moon_info`、`compute_target_windows`（含比對到的標的數）、`check_unsupported`，搭配既有的「氣象+視寧度並行查詢」共同組成完整的階段耗時鏈

### 驗證

- `python3 -m pytest tests/ -q`：82 passed
- 純新增 log，無邏輯變更，行為不受影響
- **待辦**：push 後需請使用者重新測試「南橫啞口」這類會觸發異常耗時的查詢，取得細部耗時 log 才能定位真正原因

## 2026-07-14（Phase 3B #2 起步：氣象/視寧度平行查詢 + 耗時記錄）

### 改進

- **`run_query()` 平行查詢 Open-Meteo 與 7Timer**：兩者互不依賴（都只需要 lat/lon/query_dates），過去是序列呼叫，改用 `ThreadPoolExecutor(max_workers=2)` 平行送出。與比較模式（`main.py` 既有）用的是同一手法，非新嘗試
- **`rank_location_candidate()` 同步套用**：`include_seeing=True`（最佳地點排名精排階段）時比照平行化；`include_seeing=False`（快速篩選階段）維持原本單一呼叫，不加無謂的 threading 開銷
- **新增 `[耗時]` 系列 log**：`process_and_reply()` 內記錄取得顯示名稱、意圖解析（LLM）、`run_query`（天文計算+氣象+CCI）、`generate_reply`（LLM 生成回覆）、總計耗時；`run_query()` 內另外記錄氣象+視寧度平行查詢本身的耗時。四個成功回覆出口（一般查詢、比較模式、最佳地點排名、座標 fallback）都補上總計耗時 log

### 為什麼沒有「合併兩次 LLM 呼叫」

ROADMAP 原本建議的方向包含合併 `parse_intent()` 與 `generate_reply()`。實際看程式碼後判斷不適合：`generate_reply()` 的 prompt 是由 Python 算好的 CCI、氣象、觀測窗口組成，而這些計算全部依賴 `parse_intent()` 解析出的地點/日期。真的合併成一次 LLM 呼叫，等於要 LLM 自己猜氣象/CCI，直接違反「不猜測」原則。這次先做零準確度風險的部分（平行化 + 記錄耗時），LLM 呼叫數量的問題留到有真實生產環境的耗時數據後再評估（例如 `parse_intent()` 的 system prompt 每次都內嵌全部約 113 個地點清單，是否用較小/較快模型專跑這一步更合理）

### 驗證

- `python3 -m pytest tests/ -q`：82 passed
- 本地以 mock 過的 `check_weather_multi`/`get_7timer_seeing`（各加 0.3s 延遲）驗證 `run_query()`：平行查詢耗時記錄為 0.30s（等於單一呼叫延遲，而非兩者相加的 0.6s，證實真的平行執行）；且 CCI breakdown 內雲量、視寧度、透明度三個數值都正確反映兩個來源的 mock 資料，沒有資料錯置
- **待辦**：push 後需觀察 Render 正式環境的 `[耗時]` log，取得真實查詢的各步驟耗時分布，才能判斷下一步優化重點（是 LLM 呼叫、氣象 API、還是 Skyfield 天文計算）

## 2026-07-14（修復：`init_sheets()` 啟動時必定 NameError，Google Sheets 連線從未真正在啟動時成功過）

### 問題描述

使用者在 Render 部署 log 中直接看到：

```
⚠️ Google Sheets 連線失敗：NameError: name 'init_state_sheet' is not defined
```

### 根本原因

`init_sheets()`（定義於 `main.py:252`）內部呼叫 `init_state_sheet(sh)`，而模組層級的 `ws_query, ws_feedback, ws_locations, ws_state = init_sheets()`（`main.py:286`）在 module 由上到下載入時會立刻執行。但 `from state_store import init_state_sheet, ...` 原本放在 `main.py:449`，遠在第 286 行之後才執行。Python 對函式內自由變數是在「呼叫當下」才去 module 的 global 命名空間查找，不是在函式定義當下決定——第 286 行呼叫 `init_sheets()` 時，`init_state_sheet` 這個名字根本還沒被 import 進來，因此每次 process 啟動必定拋出 `NameError`，啟動當下 Google Sheets 連線 100% 失敗。

**這個 bug 從 `cef861f`（今天稍早的 User State 持久化 commit）就存在**，並非本次「自定義地點重新載入」修復引入的新問題（已直接比對 `git show cef861f:main.py` 確認，非猜測）。

**為什麼稍早的 `/healthz` 檢查顯示 `google_sheets_connected:true`，掩蓋了這個問題：** `log_query()` / `log_feedback()` 內建「`ws_query` 為空時嘗試重新呼叫 `init_sheets()`」的重連邏輯（本身是今天稍早修好的功能）。啟動當下的呼叫必定失敗，但只要在第一次真正的查詢進來、觸發 `log_query()`/`log_feedback()` 的重連嘗試時，module 已經完整載入完畢（第 449 行的 import 早就執行過了），這次重連就會成功——`/healthz` 檢查到的其實是「查詢觸發重連後」的補救結果，不是「啟動當下」的真實狀態。這也解釋了這次實測「南橫啞口」失敗的真正原因：地點查找發生在重連補救之前，當下 `ws_locations` 仍是 `None`，自定義地點重新載入邏輯直接 no-op。

### 修復

- 把 `from state_store import init_state_sheet, hydrate_user_state, persist_pending_state, clear_pending_state` 從 `main.py:449` 移到檔案最前面（`import gspread` / `Credentials` 之後），確保在任何 module 層級程式碼執行之前就已經 import 完成
- 確認 `targets` / `astro` / `weather` / `cci` 四個模組沒有同樣的問題（它們只在稍後定義的函式內使用，該行第 16–286 行之間沒有任何模組層級程式碼引用到它們）

### 驗證

- 本地重新 import `main`（無真實 Google 憑證的情況下）：錯誤訊息從 `NameError: name 'init_state_sheet' is not defined` 變成預期中的 `RuntimeError: GOOGLE_CREDENTIALS_JSON is not configured`，證實 NameError 已消除
- `python3 -m pytest tests/ -q`：82 passed
- **待辦**：push 後需請使用者確認 Render 部署 log 不再出現這行 NameError，且 `/healthz` 在「剛啟動、還沒有任何查詢進來」的狀態下就顯示 `google_sheets_connected:true`（不是靠查詢觸發重連補救）

## 2026-07-14（Phase 3B #1：User State 持久化儲存）

### 新增

- **`state_store.py`**：把 `user_state` / `user_pending_location_query` / `user_last_query` / `user_wish_text` 的「等待中」狀態持久化到 Google Sheets 新分頁「使用者狀態」，解決 2026-06-10 架構檢討列為最高優先的技術債——Render 重啟或 redeploy 會靜默清空使用中的補座標／許願／15天氣象日曆流程
  - 只在使用者進入 `waiting_location_coordinates` / `waiting_wish` / `waiting_weather_location` 三個等待狀態的轉場時寫入，一般查詢不逐則觸發 Sheets 寫入，避免拖慢回覆速度
  - `hydrate_user_state()`：啟動時把 sheet 內容讀回記憶體 dict，並建立 row index cache
  - `persist_pending_state()` / `clear_pending_state()`：best-effort upsert／清空，Sheets API 失敗不中斷主流程；同一用戶重複觸發等待流程時重用同一列，避免 sheet 無上限成長
  - `main.py` 對應的 8 個狀態轉場點（3 個設定點、8 個清除分支）已接上持久化呼叫

### 修復

- **`init_sheets()` 重連呼叫點解包不一致**：`init_sheets()` 回傳 4 個值（含新增的 `ws_state`），但 `log_query()` / `log_feedback()` 內原本的 3 處 Sheets 斷線重連呼叫只解包 2 個值，會拋 `ValueError` 並被外層 `except Exception` 靜默吞掉——代表 Sheets 斷線後的自動重連路徑過去從未真正生效過。已修正為完整解包並補上對應 `global` 宣告

### 驗證說明

- `py_compile` 全模組通過、`git diff --check` 通過
- pytest：sandbox 網路政策擋下 `ssd.jpl.nasa.gov`（skyfield 首次執行需下載 `de421.bsp`），改從 PyPI 允許清單內的 `skyfield-data` 套件取出 `de421.bsp` 放到 repo 根目錄（gitignored，不進版控）解掉此限制，隨後 `python3 -m pytest tests/ -q` 78 passed
- Push 後 `/healthz` 已確認：`ok: true`、`google_sheets_connected: true`、`line_token_probe: ok`、`openrouter_key_probe: ok`、`version` 與部署 commit 一致
- 「使用者透過 LINE 補座標流程 + 重啟 → pending 狀態正確恢復」這個端對端情境**尚未實測確認**：使用者實際操作習慣是看到未知地點提示後直接手動編輯「自定義地點」Sheet，而不是回覆座標給 Bot，因此還沒有一次走過補座標對話流程本身。`state_store.py` 的邏輯已有獨立驗證腳本與 pytest 覆蓋，但這個特定端對端路徑仍待實測

## 2026-07-14（修復：手動編輯「自定義地點」Sheet 對正在執行的 process 不生效）

### 問題描述

使用者反映：已手動在「自定義地點」Google Sheet 新增地點（例如「南橫啞口」+ 經緯度），但重新查詢「7/17 南橫啞口適合拍星嗎？」時仍被當成未知地點，要求補座標。

### 根本原因

`load_custom_locations()`（`main.py`）過去只在 process 啟動時被呼叫一次（模組載入時的 `load_custom_locations()`），沒有任何定時、per-request 或手動觸發的重新載入機制。使用者手動編輯 Sheet 後，正在執行的 Render process 記憶體中的 `LOCATION_DATA` / `KNOWN_LOCATIONS` 不會更新，只有下次 process 重啟（例如新的 deploy）才會重新讀取 Sheet、看到新增的列。

### 修復

- **新增 `maybe_reload_custom_locations()`**：節流重新載入包裝，最多每 5 分鐘（`CUSTOM_LOCATION_RELOAD_INTERVAL_SECONDS`）重讀一次「自定義地點」Sheet；`load_custom_locations()` 本身已會跳過已存在於 `LOCATION_DATA` 的名稱，重複呼叫是安全的，只會撿到新增的列
  - 掛在 `find_known_location_in_query()`（`normalize_intent()` 內每次查詢地點解析都會呼叫的函式）最前面，讓「手動新增地點後最多等 5 分鐘就會被看到」，不需要等下次 deploy
  - 節流設計與 `weather.py` 既有的 30 分鐘預報快取風格一致：只在查找路徑上偶爾多打一次 Sheets API，不逐則訊息觸發
  - 啟動時的初始載入呼叫點也改用這個節流版本（`maybe_reload_custom_locations()` 取代原本直接呼叫 `load_custom_locations()`），行為不變（本來就是「從未載入」，第一次呼叫必定真的讀取）
- **修正節流哨兵值 bug（實作過程中發現並修正，未流出）**：`_custom_locations_last_loaded` 初始值原訂為 `0.0`，但 `time.monotonic()` 的起點是實作定義的（常是開機時間），process 剛啟動、系統開機不久時可能回傳小於節流間隔的值，導致「從未載入過」被誤判為「還在節流視窗內」，第一次呼叫反而不會真的重讀 Sheet。改用 `float("-inf")` 作為哨兵值，保證第一次呼叫必定觸發真正的載入

### 測試

- 新增 `tests/test_custom_location_reload.py`（4 個測試）：手動新增地點會被撿到、節流視窗內不重複打 API、節流視窗過後會再讀一次、`find_known_location_in_query()` 會觸發重新載入
- `python3 -m pytest tests/ -q`：82 passed（78 + 新增 4 個）
- 以使用者回報的實際地點名稱（南橫啞口）與查詢文字重現整個流程，確認修復前會失敗、修復後可正確解析座標

## 2026-07-05（Phase 3 前置重構：模組拆分 + 可靠性強化 + 測試安全網）

### 重構

- **main.py 模組拆分**（3,384 行 → 2,491 行 + 4 個模組）：
  - `targets.py`：TARGET_LIBRARY（117 標的）、METEOR_SHOWERS、MILKY_WAY_CORE（靜態天文目錄）
  - `astro.py`：Skyfield 初始化、薄暮、月出月落、暗空窗口、銀河構圖、目標觀測窗口（純天文計算）
  - `weather.py`：Open-Meteo、7Timer、風級換算、預報快取
  - `cci.py`：出勤信心指數純計算（5 個 profile）
  - `main.py` 保留 Flask/LINE webhook、意圖解析、地點資料、回覆組裝、最佳地點排名；runtime entrypoint 不變
- **`_archive/main_telegram.py`**：舊版 Telegram 實作移入 `_archive/`，避免與 production 代碼混淆
- 新增 `.gitignore`（`__pycache__/`、`de421.bsp`、`.claude/` 等）

### 新增（測試安全網）

- **pytest 測試套件 `tests/`（70 個測試）**：涵蓋標的匹配（含 m2/m20、m1/m10/m100 邊界回歸）、座標解析（含日期誤判回歸）、暗空窗口、CCI 全 profile、意圖正規化、地點審核 gate、No-Go 防線、預報快取
- 執行方式：`python3 -m pytest tests/ -q`（已加入 dry-run gate）

### 可靠性修復

- **`parse_intent` 重試 + 友善降級**：LLM 回傳非法 JSON 時自動重試一次（temperature=0）；再失敗拋 `IntentParseError`，回覆使用者「我沒能看懂這個查詢」並附範例，不再露出原始 JSONDecodeError
- **紅藍軍程式層防線 `enforce_no_go_language()`**：CCI < 40 的日期若 LLM 回覆缺少「不建議/不值得」用語，程式自動在回覆最前面加註 ❌ 出勤判定；No-Go 保證不再只靠 prompt
- **地點審核制補強**：用戶提供座標的自定義地點改為 `review_status: "pending"`——仍可用於該用戶的直接查詢，但不進最佳地點排名、不進意圖解析地點目錄；補審核後（taiwan_locations.json 標記 approved）才全面生效。既有 Sheets 自定義地點載入時同樣標記 pending

### 效能

- **預報 API 快取**：Open-Meteo 與 7Timer 回應以 (座標, 日期組) 為 key 快取 30 分鐘；只快取成功結果，API 錯誤不快取。最佳地點排名與重複查詢不再重打相同 API
- **查詢執行緒池**：`MESSAGE_EXECUTOR`（max_workers=8）取代每則訊息裸開 `threading.Thread`，流量突增時有上限保護

### 雜項修正

- `UNSUPPORTED_KEYWORDS` 中「凌日」重複定義（planet 與 solar_eclipse），移除重複、保留 solar_eclipse 分類（維持既有生效行為）
- `save_custom_location` 內區域變數 `ts` 改名 `ts_str`，避免遮蔽 skyfield timescale

### 回覆格式修復（LINE 用戶體驗，實測後補）

- **回覆截斷修復**：`generate_reply` max_tokens 1000 → 1600；實測「屏東車城國小銀河」回覆在【氣象分析】中途被截斷
- **LINE 純文字保證**：prompt 新增【輸出格式：LINE 純文字訊息】硬性規定（禁止 #、**、---、markdown 表格）；並新增 `strip_markdown_for_line()` 程式層清除 LLM 漏出的 markdown 語法（LINE 不渲染 markdown，會顯示為雜訊字元）

### 流程改善

- Dry Run Gate 更新：py_compile 涵蓋全部 5 個模組 + 必跑 pytest（見 `HERMAS_AGENT.md`）

---

## 2026-07-05（文件同步：確認 Phase 3A 完成、Phase 3B 啟動前整理）

> 本次無功能代碼變更。以下為文件狀態核對與開發方向確認，已同步至 ROADMAP.md 與 CLAUDE.md。

### 開發方向確認（ROADMAP）

- **確認 Phase 3A 已完成**：`ROADMAP.md` 離開指標表格中三項 hard gate（CCI、地點比較、多題材 CCI 框架）皆已 ✅；`CLAUDE.md` 先前的「Next Development Priority」段落仍寫 Phase 3A 為下一步，屬過時內容，已修正為 Phase 3B
- **Phase 3B 新增技術債項目（順序 1，原順序 1–14 依序後移為 2–15）**：User State 持久化儲存。2026-06-10 架構檢討已將此列為最高優先、建議 Phase 3 前完成，但至今未處理；`main.py` 的 `user_state`／`user_pending_location_query`／`user_last_query`／`user_wish_text` 仍是記憶體 dict，Render 重啟或 redeploy 會清空使用中的對話狀態

### 文件更新

- `CLAUDE.md`：重寫「Next Development Priority」，反映 Phase 3A 完成狀態與 Phase 3B 現況順序，並修正 Meteoblue / JPL Horizons 已排入 Phase 3B 排程（非「立即核心工作」也非「永久不做」）的敘述
- `ROADMAP.md`：Phase 3B 表格新增 User State 持久化項目
- `HERMAS_AGENT.md`：收工作業新增「文件同步檢查」步驟，見下方流程改善記錄

### 流程改善

- 新增規範：收工作業必須主動核對 ROADMAP / CHANGELOG / CLAUDE.md / README / SUBJECT_SCOPE 是否反映當天實際開發內容與方向調整，不可仰賴使用者主動要求才更新（詳見 `HERMAS_AGENT.md` 流程改善記錄）

---

## 2026-06-21（方案 B+A1+A2：完整 Messier 目錄 + 未命中目標回覆改善）

### 新增（方案 B）

- **TARGET_LIBRARY 擴充至完整 Messier 目錄（M1–M110）**：
  - 新增約 90 個天體（排除已收錄 M8/M16/M31/M42/M44；跳過 M40 雙星、M73 星群、M102 爭議）
  - 涵蓋冬季（M1、M35–M38、M41、M45–M50 等）、春季（M51、M63–M66、M81–M109 等）、夏季（M4–M25 範圍、M56–M57 等）、秋季（M2、M33、M74–M77 等）所有 Messier 天體
  - 每個條目完整填寫 RA/Dec、type、min_alt/max_alt、min_focal_mm、tracking_required、difficulty、aliases（含英文名稱與中文別名）
  - 南天低仰角天體（Dec < -30°）調整 max_alt 為實際可達天頂角

### 改善（方案 A1）

- **`generate_reply()` 注入未命中目標固定格式**：
  - 當 `data_quality.celestial_positions.unmatched_targets` 非空時，自動在 system prompt 注入固定回覆段落
  - 格式：`⚠️ [目標名稱] — 本系統尚無此天體的座標資料，無法計算方位與觀測窗口。` + 兩個行動建議
  - LLM 被指示逐一照字輸出，不可自行修改措辭

### 改善（方案 A2）

- **未命中目標自動寫入 Google Sheets 用戶反饋**：
  - `log_query()` 中新增檢查；發現 `unmatched_targets` 時自動呼叫 `ws_feedback.append_row()`
  - 格式：`【未命中標的】目標名1, 目標名2（查詢：原始文字）`，供開發者追蹤哪些天體需要加入資料庫

---

## 2026-06-21（修復：夏季低仰角標的「無觀測窗口」誤報）

### 問題描述

查詢「今晚小坪頂拍鬼宿星團」回傳「整夜仰角過低或已落下，無法拍攝」。
但實際上 M44 在 21:18 落下（天文薄暮約 20:20 結束），有效黃金觀測時段約 40–50 分鐘。

### 根本原因

1. `compute_dark_sky_window()` 的 `MIN_WINDOW_MIN = 30`：若月亮在天文薄暮後短時間內升起，導致早晚間暗空片段 < 30 分鐘而被丟棄。
2. `compute_target_windows()` 只掃描 `dark_windows` 內的時間點。當 `day_windows == []`（因上述丟棄）時，原本直接 `continue` 跳過整天，導致 M44 的 20:20–21:18 黃金時段完全消失。

### 修復

- **`compute_target_windows()` 新增備用掃描**：
  - 主要掃描（暗空窗口內，`in_dark_window=True`）維持不變。
  - 若主要掃描找不到任何窗口（包含 `dark_windows==[]` 或目標在暗空期間仰角不足），自動觸發備用掃描：18:00–06:00 全夜掃描，標記 `in_dark_window=False`。
  - 這樣 LLM 會看到「20:20 仰角12°（非暗空窗口內）」而不是「無觀測窗口」，可正確告知使用者夏季晚落目標的黃金時段。
- 移除了原本的 `if day_windows is not None and len(day_windows) == 0: continue` 硬跳過邏輯。

---

## 2026-06-21（M44 新增 + 別名系統）

### 新增

- **鬼宿星團 M44（蜂巢星團）**：加入 TARGET_LIBRARY。RA 08h 40.4m / Dec +19°59'，type=cluster，min_focal_mm=50，建議有赤道儀，難度⭐（入門）。
  - 別名：m44、beehive、praesepe、蜂巢星團、積尸氣、巨蟹座星團
  - 修復測試失敗：查詢「鬼宿星團」前回傳「目前沒有位置資料」

### 改進

- **TARGET_LIBRARY 別名系統**：所有天體加入 `aliases` 欄位（英文名、梅西爾編號、NGC 編號、常見別名）
- **`_target_matches()` 輔助函式**：雙向 substring 同時比對正式名稱與所有 aliases
- **`match_targets()` / `find_unmatched_targets()`**：改用 `_target_matches()`，LLM 回傳任何已知別名均可正確解析
- **`determine_wind_profile()`**：加入 `cluster` type 對應 deep_sky 風速限制

---

## 2026-06-21（Phase 3A #5：多題材 CCI 框架）

### 新增

- **多題材 CCI 框架（Phase 3A #5）**：`compute_cci_for_date()` 新增 `cci_profile` 參數，支援以下四種題材模式：
  - `meteor`（流星雨）：以月面照度為主要干擾因子，ZHR 加成目標可見性；風速容忍度放寬至 4 級（廣角無追蹤）
  - `moonscape`（月景）：月光強度反轉為加分項；暗空窗口權重大幅降低；透明度優先
  - `lunar_eclipse`（月蝕）：移除暗空窗口需求；透明度權重提升至 17%；月亮仰角決定可見性；附台北天文館查詢提示
  - `comet_layer1`（彗星第一層）：天況 CCI 同深空；目標可見性固定中性 50 分（靜態座標不準確）；回覆強制附座標免責聲明
- **`determine_cci_profile()`**：根據 intent、matched_targets、showers、unsupported_info 自動選擇 CCI profile
- **`_moon_illumination()`**：從 moon_phase_pct 計算月面照度比例（0=新月, 1=滿月）
- **設備適配標籤**：TARGET_LIBRARY 每個天體加入 `min_focal_mm`、`tracking_required`（no/optional/recommended/required）、`difficulty`（1–4）
- **設備提示注入回覆**：深空題材查詢時，`generate_reply()` 自動彙整各標的設備需求，傳入 LLM `【裝備提醒】` 區塊
- **`profile_notes` 機制**：CCI 計算時依 profile 附加說明（如「月蝕時間請查台北天文館」），匯整後傳入 LLM context

### 改進

- **月蝕從 hard-block 移除**：`UNSUPPORTED_KEYWORDS` 移除月蝕/月食相關條目，改由 `cci_profile=lunar_eclipse` 提供天況評估 + 軟性提示
- **`check_unsupported()` 新增**：`has_lunar_eclipse` 與 `has_moonscape` 偵測旗標
- **run_query 回傳值新增**：`cci_profile`、`unsupported_info`、`matched_targets`（帶設備欄位）
- **`generate_reply()` 新增**：題材特殊說明（`subject_instruction`）注入 system prompt；設備適配與 profile_notes 注入 user content

---

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
