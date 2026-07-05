# Hermas Agent 開發規範

本專案之後以 Hermas Agent 作為開發協作流程的主名稱。若本地或雲端環境尚未安裝特定 Hermas Agent CLI / plugin，仍需依照本文件的工作流程執行。

## Repo

- GitHub repo: `dannytsao/astro-bot`
- Production branch: `main`
- Production service: Render Web Service `astro-bot-web`
- Production URL: `https://astro-bot-web-xlny.onrender.com`
- Health check: `https://astro-bot-web-xlny.onrender.com/healthz`
- Runtime entrypoint: `main.py`
- Render import fallback: `app.py`
- Legacy backup: `_archive/main_telegram.py`

## 工作原則

1. 每次變更前先確認目前 repo、branch、dirty worktree。
2. 只修改與需求直接相關的檔案。
3. Production code 走 OpenRouter chat completions；優先使用 `OPENROUTER_API_KEY`，並暫時支援 `ANTHROPIC_API_KEY` 作為 legacy fallback。
4. 不混入 OpenRouter 或其他暫存中的實驗變更，除非使用者明確要求。
5. `查詢記錄` 與 `用戶反饋` 是不同用途，不可為了修一邊而破壞另一邊格式。
6. 所有 production code 變更完成後，一律先 dry run，再 commit/push/deploy。
7. 無真憑實據不猜測：氣象、視寧度、透明度、天體位置、地點座標若沒有可靠資料，Bot 必須明確說「沒有資料」或反問補充，不可讓 LLM 自行推論或替換成其他資料。

## Dry Run Gate

部署前至少執行：

```bash
PYTHONPYCACHEPREFIX=/tmp/astro-bot-pycache python3 -m py_compile main.py targets.py astro.py weather.py cci.py
python3 -m pytest tests/ -q
git diff --check
git status --short --branch
```

pytest 為必跑項目；任何測試失敗不得 commit/push/deploy。首次執行 pytest 會自動下載 `de421.bsp`（約 17MB，已列入 `.gitignore`）。

若變更影響 LINE webhook、Google Sheets、Render 環境變數或外部 API，還要補充對應檢查：

- 檢查相關函式呼叫路徑。
- 檢查錯誤處理與 retry 行為。
- 檢查是否會寫入正確的 Google Sheet tab。
- 確認不會產生重複或空白資料列。

Dry run 沒有問題後，才能：

```bash
git add <changed files>
git commit -m "<clear message>"
git pull --rebase origin main
git push origin main
```

## Render Deploy Gate

Push 後若 Render auto deploy 啟動，需確認 live service：

```bash
curl -sS https://astro-bot-web-xlny.onrender.com/healthz
```

至少要確認：

- `ok: true`
- `google_sheets_connected: true`
- `openrouter_key_probe` 顯示 `ok`
- `line_token_probe: ok`
- `version` 符合剛推上的 commit

LINE Developers 的 webhook 必須指向：

```text
https://astro-bot-web-xlny.onrender.com/callback
```

Webhook Verify 成功只代表 LINE 能打到 `/callback`，不代表 Bot 一定能回覆。若 LINE 有收到 `[收到]` log 但沒有回覆，優先檢查 `/healthz` 的 `line_token_probe` 與 Render Web Service 的 `LINE_CHANNEL_ACCESS_TOKEN`。

## Google Sheets 規格

Spreadsheet ID:

```text
1u-IDQPi0g-mFxPDetdV46p90xRgLAQZ3Jz90brLl6-M
```

### 查詢記錄

用途：記錄 LINE 使用者查詢內容與解析結果。

現行格式可以保留，不要任意調整既有欄位。缺資料與資料可信度需寫入：

| 欄位 | 內容 |
| --- | --- |
| I | 資料品質摘要 |
| J | 資料品質JSON |

資料品質需至少能表達：

- 氣象資料是否來自 Open-Meteo，是否 missing/partial。
- 視寧度/透明度是否來自 7Timer，是否 missing/partial。
- 天體位置是否有內建資料；找不到標的不可創造位置。
- 地點座標解析失敗時，需記錄 requested location 與是否已反問使用者補座標。

## 地點資料庫

Production 地點資料位於：

```text
data/taiwan_locations.json
```

規則：

- 只有 `review_status: "approved"` 的地點會被 Bot 載入。
- 未審核或來源不明的候選地點必須保持 `needs_review`，不可直接用於 production。
- 新地點需包含 `lat`、`lon`、`aliases`、`region`、`source`、`confidence`、`review_status`。
- 新增或修改地點後需執行：

```bash
python3 ~/.codex/skills/taiwan-location-research/scripts/validate_locations.py data/taiwan_locations.json
```

- 可用 Codex skill `taiwan-location-research` 搜尋台灣本島與離島地點候選座標，但最後標記 `approved` 前仍需人工審核。

### 用戶反饋

用途：只記錄使用者評分與建議事項。

目標欄位：

| 欄位 | 內容 |
| --- | --- |
| A | 日期及時間 |
| B | Line User Name |
| C | 建議事項的內容 |

注意：

- 使用者按下「加入許願池」但尚未輸入內容時，不應寫入資料列。
- 使用者輸入 `建議：...`、`許願：...` 時，C 欄應盡量保留純建議內容。
- 不要再寫入 `用戶ID`、`查詢內容`、`評分`、`類型`、`許願內容` 等舊版多欄格式。

## 文件管理流程

專案文件各有職責，任何變動必須寫入正確的文件。不可只改代碼不更新文件，也不可只更新文件不記錄原因。

### 文件分工

| 文件 | 職責 | 何時更新 |
| --- | --- | --- |
| `CHANGELOG.md` | 功能代碼變動記錄 + 開發方向調整記錄 | 每次 commit 前；無代碼變更但有方向調整時也需記錄並標注「無代碼變更」 |
| `ROADMAP.md` | 產品開發方向、需求優先順序、Phase 規劃 | 需求新增、範圍調整、優先順序變更、Phase exit gate 修改時 |
| `SUBJECT_SCOPE.md` | 各攝影題材 CCI 支援範圍，in/out scope 判斷依據、應對方式、開放條件 | 題材範圍決策變動、新題材加入或排除、資料來源策略調整時 |
| `README.md` | 用戶面功能說明、目前不支援項目、即將推出功能 | 功能上線後、不支援項目新增或移除時；確保與 CHANGELOG 同步 |
| `HERMAS_AGENT.md` | 開發工作流程、文件管理規範、流程改善記錄 | 開發流程有缺口被發現、新規範確立時 |
| `CLAUDE.md` | 各文件的唯一來源索引 | 新增或移除重要文件時 |

### 更新原則

- **功能代碼變動**：CHANGELOG + README（如有用戶面影響）
- **開發方向或需求變動**：ROADMAP + CHANGELOG（標注方向變動）
- **題材範圍決策**：SUBJECT_SCOPE.md + ROADMAP（如影響 Phase 規劃）+ CHANGELOG
- **流程改善**：HERMAS_AGENT.md + CHANGELOG（標注流程變動）
- 同一個 commit 若同時涉及多類變動，各文件都要更新，不可遺漏

### SUBJECT_SCOPE.md 使用規範

- 所有攝影題材的 CCI 支援範圍決策以 `SUBJECT_SCOPE.md` 為唯一來源
- 新增題材、調整 in/out scope、變更資料來源策略，必須先更新此文件再進行開發
- ROADMAP.md 中 Phase 的題材說明若與 SUBJECT_SCOPE.md 衝突，以 SUBJECT_SCOPE.md 為準

---

## 流程改善

### 何時觸發流程回顧

以下情況發生時，需立即更新 `HERMAS_AGENT.md`：

- 開發過程中發現規範缺口（例如：agent 不知道某文件的存在或用途）
- 使用者需要重複提醒同一件事超過一次
- 新工具、新資料來源、新文件被加入專案
- Phase 轉換時發現現有流程不適用

### 如何更新流程

1. 確認缺口的具體描述（哪個步驟、哪個文件、哪個規範）
2. 決定補充位置（現有區塊內補充，或新增區塊）
3. 更新 `HERMAS_AGENT.md`
4. 在 `CHANGELOG.md` 記錄「流程改善：新增 XXX 規範」
5. 若影響 CLAUDE.md 的索引，一併更新

### 流程改善記錄

| 日期 | 缺口描述 | 補充內容 |
| --- | --- | --- |
| 2026-06-21 | 無文件管理流程規範，agent 不知道各文件職責分工與更新時機 | 新增「文件管理流程」與「流程改善」區塊 |
| 2026-07-05 | 無自動化測試，匹配類 bug 反覆出現且 dry-run gate 只做 py_compile | main.py 拆分為 5 個模組；新增 `tests/` pytest 套件並列為 dry-run gate 必跑項目 |

---

## 回覆使用者

完成變更後，回覆需包含：

- 改了什麼。
- dry run 結果。
- 是否已 commit/push。
- 是否已確認 Render health。
- 若有需要使用者手動處理的資料，例如刪除舊 Google Sheet row，要明確說出。

## 開工作業

當使用者說「開工」、「動工」、「start the day」或等同意思時，需先確認目前狀態，再回覆可接續的工作摘要。

開工前至少檢查：

```bash
git status --short --branch
curl -sS https://astro-bot-web-xlny.onrender.com/healthz
```

開工回覆需包含：

- Current branch/status
- Production version/health
- Last known goal 或目前 Phase
- 是否有 uncommitted changes
- Suggested next action

## 收工作業

當使用者說「call it a day」、「收工」或等同意思時，需先確認目前狀態，再用下列格式回覆每日開發紀錄。

收工前至少檢查：

```bash
git status --short --branch
curl -sS https://astro-bot-web-xlny.onrender.com/healthz
```

每日開發紀錄格式：

```markdown
# Daily Development Note - YYYY-MM-DD

## Today Completed
- 

## Code Changed
- 

## Tests / Validation
- 

## Issues Found
- 

## Decisions Made
- 

## Next Actions
1. 
2. 
3. 

## Restart Prompt for AI
We are working on:
Current goal:
Today we completed:
Next task:
Important constraints:
Known issues:
```
