# 🔭 天文攝影查詢 Bot

台灣天文攝影專用 LINE Bot，結合天文計算、氣象預報與 AI 自然語言回覆。

產品定位不是「只服務銀河攝影」，而是協助台灣攝影者判斷各類天文與天空景象是否值得出勤：

- 深空與星野：銀河、星座、星雲、星系、流星雨、彗星等
- 日月行星運行景象：月出月落、月相、日月方位、行星與月亮接近、行星可見性等
- 天氣與地景條件：雲量、透明度、視寧度、結露、霧、雲海、海岸潮汐等
- 攝影決策：去哪裡、何時拍、風險是什麼、備案地點與器材提醒

---

## 使用方式

掃描 LINE Bot QR Code 加入好友，直接用中文輸入查詢：

```
4月15日 合歡山 銀河
這個週末 阿里山 有什麼可以拍？
5月1日到3日 墾丁 天蠍座
今晚 外澳 適合拍攝嗎？
```

---

## 功能說明 v2

### ✅ 查詢功能

| 功能 | 說明 |
| --- | --- |
| 自然語言查詢 | 直接用中文輸入，不需要特定格式 |
| 指定標的查詢（類型 A） | 指定天體，回傳最佳觀測時刻與條件 |
| 開放探索查詢（類型 B） | 不指定天體，Bot 列出當晚所有可拍標的 |
| 日期區間查詢 | 支援單日、多日、「今晚」「這個週末」等表達方式 |
| 地點解析 | 先查 `data/taiwan_locations.json` 已審核地點；查不到時反問座標，不讓 AI 亂猜 |

### ✅ 標的庫（14 個天體）

| 類型 | 標的 |
| --- | --- |
| 銀河 | 銀河核心 |
| 星座 | 獵戶座、天蠍座、獅子座、仙女座、南十字座 |
| 星雲 | M42、M8、M16、M31、NGC2244、NGC2174、NGC6302 |
| 彗星 | 紫金山-ATLAS（近似座標，不反映每日實際位置） |
| 流星雨 | 象限儀、英仙、雙子、獅子、天琴（極大期 ±3 天提示） |

### ✅ 計算與預報

| 功能 | 說明 |
| --- | --- |
| 天文計算 | Skyfield 每 10 分鐘精確計算仰角與方位角，每天取最佳時刻 |
| 月相分析 | 顯示每日月相及對深空攝影的影響程度 |
| 月出月落 | 計算月出月落時刻與方位角，標示整夜月上/月下情況 |
| 有效暗空窗口 | 以天文薄暮（太陽低於 -18°）為基準，剔除月光時段，計算真實可用拍攝窗口 |
| 銀河構圖方位 | 計算銀河核心方位角與中文方向描述，結合月亮方位評估構圖干擾，提供具體拍攝建議 |
| 氣象預報 | Open-Meteo 提供雲量、濕度、溫度、露點；雲量 ≤40% 視為好天氣 |
| 結露風險 | 溫度與露點差 <1.5°C 時自動提醒（收緊後更符合山區實際起霧門檻） |
| 曝光建議 | 天況合適時給出 500 法則快門、依月相分段 ISO、光圈建議 |

### ✅ Bot 操作

| 功能 | 說明 |
| --- | --- |
| 取消查詢 | 計算中可按「❌ 取消」中止，不繼續消耗 API |
| 用戶評分 | 每次回覆後顯示 👍 👎 按鈕，記錄氣象準確度 |
| 許願池 | 按 💡 可輸入建議，自動記錄至 Google Sheets |
| 多用戶隔離 | 每位用戶對話完全獨立，互相看不到彼此的查詢記錄 |
| 自動重啟 | 遇到網路錯誤自動重啟，不需要手動 Redeploy |



---

## 限制 v2

| 項目 | 說明 |
| --- | --- |
| ~~月出月落未計算~~ | ✅ v2 已完成：計算月出月落時刻與方位，並計算有效暗空窗口 |
| ~~銀河構圖方位未計算~~ | ✅ v2 已完成：計算銀河核心與月亮方位角，提供構圖建議 |
| 彗星座標 | 固定近似值，不反映每日實際位置（待許願池需求 > 20 則後才評估接 JPL Horizons API） |
| 氣象預報範圍 | 僅支援未來 15 天；超出範圍仍顯示天文計算，但無氣象資料 |
| 微氣候修正 | 尚未建立各地點的歷史預報誤差修正模型 |
| 不支援天體 | 行星位置、日食月食 |
| 無地點記憶 | 每次查詢都需要輸入地點 |
| 無主動推播 | 目前只能被動回應，不會主動通知天況好的夜晚 |



---

## 產品路線圖

完整路線圖（Phase 3A → 3B → 4 出勤規劃層 → 5 訂閱與閉環學習）、各階段開發順序與進入下階段的量化指標，以 [ROADMAP.md](./ROADMAP.md) 為唯一來源。

---

## Production 狀態

| 項目 | 說明 |
| --- | --- |
| Render Web Service | `astro-bot-web` |
| Production URL | `https://astro-bot-web-xlny.onrender.com` |
| LINE Webhook | `https://astro-bot-web-xlny.onrender.com/callback` |
| Health Check | `https://astro-bot-web-xlny.onrender.com/healthz` |
| GitHub Repo | `dannytsao/astro-bot` |
| Production Branch | `main` |

部署後必須確認 `/healthz`：

```text
google_sheets_connected: true
openrouter_key_probe: ok fields=data
line_token_probe: ok
```

2026-06-12 的 production 修復經驗已整理於 [LESSONS_LEARNED_2026-06-12.md](./LESSONS_LEARNED_2026-06-12.md)。

---

## 系統資訊

| 項目 | 說明 |
| --- | --- |
| 回應時間 | 每次查詢約 30–60 秒（v2 新增月出月落與暗空窗口計算） |
| API 費用 | 每次查詢約 $0.002 USD；$5 可跑約 2,500 次查詢 |
| 服務平台 | Render Web Service Starter plan |
| 對話記錄 | 管理員可在 Render Log 及 Google Sheets 查看查詢與反饋記錄 |



---

## 地點資料庫

Production 地點資料來自：

`data/taiwan_locations.json`

只有 `review_status: "approved"` 的地點會被 Bot 載入。未審核候選地點不可直接進 production。

若使用者查詢資料庫沒有的地點，Bot 會自動寫入 Google Sheets「用戶反饋」中的地點許願池，並要求使用者補座標。後續需人工審核來源與座標後，再合併進 production 地點資料庫。

可用 Codex skill `taiwan-location-research` 蒐集台灣本島與離島地點候選資料；候選資料仍需人工確認後才可標記為 `approved`。

| 地點 | 緯度 | 經度 |
| --- | --- | --- |
| 日月潭 | 23.865 | 120.917 |
| 合歡山 | 24.167 | 121.283 |
| 外澳 | 24.870 | 121.862 |
| 墾丁 | 21.945 | 120.803 |
| 阿里山 | 23.517 | 120.800 |
| 嘉明湖 | 23.250 | 121.000 |
| 武陵農場 | 24.367 | 121.367 |
| 太平山 | 24.517 | 121.617 |
| 七星山 | 25.167 | 121.533 |
| 清境農場 | 24.083 | 121.167 |
| 奧萬大 | 23.850 | 121.083 |
| 桃源谷 | 25.100 | 121.867 |
| 池上 | 23.124 | 121.216 |



---

## 技術架構

```
LINE 訊息（Webhook → Flask）
    ↓
意圖解析（OpenRouter API）
    ↓
天文計算（Skyfield + de421.bsp）
    ↓ 月出月落、天文薄暮、暗空窗口、銀河構圖方位角
氣象預報（Open-Meteo API）
    ↓
回覆生成（OpenRouter API）
    ↓
反饋記錄（Google Sheets API）
```

部署於 Render（Web Service），Flask 接收 LINE Webhook，24 小時持續運行。

---

## 環境變數

| 變數名稱 | 說明 |
| --- | --- |
| `OPENROUTER_API_KEY` | OpenRouter API 金鑰 |
| `OPENROUTER_MODEL` | OpenRouter runtime 模型；Render 有設定時以環境變數為準，未設定時預設 `anthropic/claude-sonnet-4.5` |
| `OPENROUTER_SITE_URL` | OpenRouter attribution URL，預設 Render service URL |
| `OPENROUTER_APP_NAME` | OpenRouter attribution app name，預設 `astro-bot` |
| `ANTHROPIC_API_KEY` | Legacy fallback；若暫時未建立 `OPENROUTER_API_KEY`，可讀取此變數中的 OpenRouter key |
| `LINE_CHANNEL_SECRET` | LINE Bot Channel Secret（驗證 Webhook 簽章） |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Bot Channel Access Token（發送訊息） |
| `GOOGLE_CREDENTIALS_JSON` | Google 服務帳號 JSON 金鑰（完整內容） |
| `GOOGLE_SPREADSHEET_ID` | Google Sheet 檔案 ID，預設 `1u-IDQPi0g-mFxPDetdV46p90xRgLAQZ3Jz90brLl6-M` |
