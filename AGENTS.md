# 🔭 astro-bot 專案說明（AGENTS.md）

> 本文件供 Codex / Cowork 讀取，提供專案完整背景與工作指引。

> 開發流程以 `HERMAS_AGENT.md` 為主規範。任何 production 變更都要先 dry run，確認無誤後才 commit/push/deploy。

> 產品路線圖與優先順序以 `ROADMAP.md` 為主；本文件只保留 agent 需要的摘要與工作指引。

---

## 專案概述

**astro-bot** 是台灣天文攝影專用 LINE Bot，結合天文計算、氣象預報與 AI 自然語言回覆。

- **Repo**：`dannytsao/astro-bot`
- **部署**：Render（Web Service，Flask Webhook，24 小時持續運行）
- **語言**：Python 3
- **主要檔案**：`main.py`（Flask/LINE webhook、意圖解析、地點資料、回覆組裝）；輔助模組 `targets.py`（標的目錄）、`astro.py`（天文計算）、`weather.py`（氣象 API + 快取）、`cci.py`（信心指數）；測試 `tests/`
- **備份**：`_archive/main_telegram.py`（舊版 Telegram 實作，保留備用）

產品定位不是「只服務銀河攝影」，而是協助台灣攝影者判斷各類天文與天空景象是否值得出勤：

- 深空與星野：銀河、星座、星雲、星系、流星雨、彗星等
- 日月行星運行景象：月出月落、月相、日月方位、行星與月亮接近、行星可見性等
- 天氣與地景條件：雲量、透明度、視寧度、結露、霧、雲海、海岸潮汐等
- 攝影決策：去哪裡、何時拍、風險是什麼、備案地點與器材提醒

新增日月行星題材時必須先接入可靠天文資料來源與計算邏輯；在資料未完成前，仍須維持現有攔截與「不猜測」原則。

---

## 技術架構

```
用戶 LINE 訊息（Webhook → Flask）
    ↓
意圖解析（OpenRouter API - parse_intent）
    ↓
超出範圍攔截（check_unsupported）
    ↓
天文計算（Skyfield + de421.bsp）
  - 月出月落、天文薄暮
  - 有效暗空窗口
  - 銀河核心構圖方位角
    ↓
氣象預報（Open-Meteo API）
    ↓
氣象狀態評估（weather_status）
    ↓
回覆生成（OpenRouter API - generate_reply，動態 system prompt）
    ↓
反饋記錄（Google Sheets API）
```

---

## 環境變數

| 變數名稱 | 說明 |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter API 金鑰 |
| `OPENROUTER_MODEL` | OpenRouter runtime 模型；Render 有設定時以環境變數為準，未設定時預設 `anthropic/claude-sonnet-4.5` |
| `OPENROUTER_SITE_URL` | OpenRouter attribution URL，預設 Render service URL |
| `OPENROUTER_APP_NAME` | OpenRouter attribution app name，預設 `astro-bot` |
| `ANTHROPIC_API_KEY` | Legacy fallback；若暫時未建立 `OPENROUTER_API_KEY`，可讀取此變數中的 OpenRouter key |
| `LINE_CHANNEL_SECRET` | LINE Bot Channel Secret（驗證 Webhook 簽章） |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Bot Channel Access Token（發送訊息） |
| `GOOGLE_CREDENTIALS_JSON` | Google 服務帳號 JSON 金鑰 |
| `GOOGLE_SPREADSHEET_ID` | Google Sheet 檔案 ID，預設 `1u-IDQPi0g-mFxPDetdV46p90xRgLAQZ3Jz90brLl6-M` |

---

## 部署流程

```bash
# 修改程式碼後
git add main.py
git commit -m "說明"
git push
curl $RENDER_DEPLOY_HOOK   # 自動觸發 Render deploy
```

`RENDER_DEPLOY_HOOK` 已設定在 `~/.zshrc`，每次 git push 後 Codex hook 會自動觸發。

---

## 目前版本：Phase 2（已完成）

### 新增功能
- 月出月落時刻與方位角計算
- 天文薄暮（太陽 < -18°）計算
- 有效暗空窗口（剔除月光時段）
- 銀河核心構圖方位角＋月亮干擾評估
- 回覆新增【月亮窗口】和【銀河構圖方位】區塊
- 氣象優先邏輯（直接用雲量判斷 weather_status）
- 動態 system prompt（依氣象狀態調整回覆深度）
- 超出範圍攔截：行星、日食月食、未知彗星（IAU 命名格式）
- 超出15天預報範圍提示

### 氣象狀態四種情境
| 狀態 | 條件 | LLM 行為 |
|---|---|---|
| `out_of_range` | 超出15天預報 | 結論只談天文，明確標註氣象未知 |
| `bad` | 雲量 > 80% | 結論直接說不適合，其他區塊簡化 |
| `unstable` | 雲量 40~80% | 結論標註不穩定，建議當天再確認 |
| `good` | 雲量 ≤ 40% | 正常完整分析 |

---

## 已知限制

| 項目 | 說明 |
|---|---|
| 彗星座標 | 紫金山-ATLAS 使用固定近似座標，不反映每日實際位置 |
| 氣象來源單一 | 僅 Open-Meteo，缺乏透明度、視寧度、雲層分層 |
| 不支援天體 | 行星位置、日食月食（會攔截並知會用戶） |
| 無地點記憶 | 每次查詢都需輸入地點 |
| 無主動推播 | 只能被動回應 |

---

## 不支援的查詢（自動攔截）

### 完全不支援（攔截＋知會＋許願按鈕）
- 行星：水星、金星、火星、木星、土星、天王星、海王星、冥王星、大距、衝、合月、凌日
- 日食月食：日食、月食、日蝕、月蝕、全食、偏食、環食、食既、生光
- 未知彗星：IAU 命名格式（C/YYYY、P/YYYY 等）

### 支援但有警告
- 已知彗星（紫金山-ATLAS）：正常回答但附上「座標為近似值」警告

---

## 標的資料庫（14 個天體）

| 類型 | 標的 |
|---|---|
| 銀河 | 銀河核心 |
| 星座 | 獵戶座、天蠍座、獅子座、仙女座、南十字座 |
| 星雲 | M42、M8、M16、M31、NGC2244、NGC2174、NGC6302 |
| 彗星 | 紫金山-ATLAS（近似座標） |
| 流星雨 | 象限儀、英仙、雙子、獅子、天琴（極大期 ±3 天提示） |

---

## 地點資料庫

Production 地點資料位於 `data/taiwan_locations.json`。只有 `review_status: "approved"` 的地點會被 Bot 載入；下表是早期核心地點範例，不代表完整資料庫。

| 地點 | 緯度 | 經度 |
|---|---|---|
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

---

## 產品路線圖

Phase 3A → 3B → 4（出勤規劃層）→ 5（訂閱與閉環學習）的完整規劃、各項功能開發順序與進入下階段的量化指標，以根目錄 [ROADMAP.md](./ROADMAP.md) 為唯一來源。

舊版 Phase 3C / 舊 Phase 4（個人化訂閱）/ 舊 Phase 5（進階感知）計劃已存入 [`_archive/agents_phase_plan_v2.md`](./_archive/agents_phase_plan_v2.md)。

---

## 工作原則

1. **天文計算準確性不可妥協**：仰角、方位角、月出月落時刻由 Skyfield 計算，LLM 只負責詮釋
2. **氣象優先**：氣象條件是第一道篩選，壞天氣直接結論，不浪費用戶時間看天文分析
3. **攔截順序**：`parse_intent` → `check_unsupported` → `run_query`，不支援的查詢不進入計算
4. **`match_targets` 找不到時回傳空陣列**，不 fallback 整個標的庫（避免卡死）
5. **批次修 Bug**：收集完所有問題再一起修，避免反覆 deploy

---

## 常用測試案例

```
# 正常查詢
今晚合歡山銀河
這個週末阿里山有什麼可以拍？
5月1日到3日墾丁天蠍座

# 超出範圍（應攔截）
今年台灣地區下一個月全食是甚麼時候
4/4 清晨台北觀測水星西大距
C/2026 A1 MAPS 台灣地區甚麼時候可觀測

# 邊界情境
6月1日合歡山銀河（超出15天預報範圍）
今晚阿里山紫金山彗星（彗星近似座標警告）
```

## Imported Claude Cowork project instructions
