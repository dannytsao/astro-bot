# Hermas Agent 開發規範

本專案之後以 Hermas Agent 作為開發協作流程的主名稱。若本地或雲端環境尚未安裝特定 Hermas Agent CLI / plugin，仍需依照本文件的工作流程執行。

## Repo

- GitHub repo: `dannytsao/astro-bot`
- Production branch: `main`
- Production service: Render Web Service
- Runtime entrypoint: `main.py`
- Legacy backup: `main_telegram.py`

## 工作原則

1. 每次變更前先確認目前 repo、branch、dirty worktree。
2. 只修改與需求直接相關的檔案。
3. Render environment API key 已由使用者改為 OpenRouter key；在 production code 完成 OpenRouter endpoint 支援前，不要假設現行 Anthropic SDK 路徑可以正常呼叫。
4. 不混入 OpenRouter 或其他暫存中的實驗變更，除非使用者明確要求。
5. `查詢記錄` 與 `用戶反饋` 是不同用途，不可為了修一邊而破壞另一邊格式。
6. 所有 production code 變更完成後，一律先 dry run，再 commit/push/deploy。

## Dry Run Gate

部署前至少執行：

```bash
PYTHONPYCACHEPREFIX=/tmp/astro-bot-pycache python3 -m py_compile main.py
git diff --check
git status --short --branch
```

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
curl -i --max-time 30 https://astro-bot-l9ae.onrender.com/
```

目前 `/` route 回 `404 Not Found` 是預期狀態；重點是 response headers 顯示 Render/gunicorn 有回應。

## Google Sheets 規格

Spreadsheet ID:

```text
1u-IDQPi0g-mFxPDetdV46p90xRgLAQZ3Jz90brLl6-M
```

### 查詢記錄

用途：記錄 LINE 使用者查詢內容與解析結果。

現行格式可以保留，不要任意調整欄位。

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

## 回覆使用者

完成變更後，回覆需包含：

- 改了什麼。
- dry run 結果。
- 是否已 commit/push。
- 是否已確認 Render health。
- 若有需要使用者手動處理的資料，例如刪除舊 Google Sheet row，要明確說出。
