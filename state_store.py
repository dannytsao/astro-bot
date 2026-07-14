"""Google Sheets 持久化：LINE 使用者對話等待狀態。

main.py 的 user_state / user_pending_location_query / user_last_query / user_wish_text
原本是純記憶體 dict，Render 重啟或 redeploy 會清空使用中的地點確認 / 補座標 / 許願 / 15天日曆流程。
這裡把「使用者正在等待回覆」的那一刻寫回 Google Sheets，啟動時載回記憶體，
讓這幾個窄流程可以撐過重啟；一般查詢（不在等待狀態）不受影響，不逐則訊息寫入。
"""

import json
import threading
from datetime import datetime, timedelta, timezone

import gspread

STATE_SHEET_NAME = "使用者狀態"
STATE_HEADERS = ["用戶ID", "狀態", "待補座標查詢JSON", "上次查詢", "許願文字", "更新時間"]

_lock = threading.Lock()
_row_index = {}       # user_id -> 1-based row number
_next_row_number = 2  # 第 1 列是表頭


def init_state_sheet(sh):
    try:
        ws_state = sh.worksheet(STATE_SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws_state = sh.add_worksheet(STATE_SHEET_NAME, rows=2000, cols=len(STATE_HEADERS))
        ws_state.append_row(STATE_HEADERS)
    return ws_state


def hydrate_user_state(ws_state, user_state, user_pending_location_query, user_last_query, user_wish_text):
    """啟動時把 sheet 內容載回記憶體 dict，並建立 row index 供後續 upsert 使用。"""
    global _next_row_number
    with _lock:
        _row_index.clear()
        _next_row_number = 2
        if not ws_state:
            return
        try:
            rows = ws_state.get_all_values()
        except Exception as e:
            print(f"[UserState 錯誤] 讀取失敗：{type(e).__name__}: {e}", flush=True)
            return
        for i, row in enumerate(rows[1:], start=2):
            if not row or not row[0]:
                continue
            user_id = row[0]
            _row_index[user_id] = i
            state        = row[1] if len(row) > 1 else ""
            pending_json = row[2] if len(row) > 2 else ""
            last_query   = row[3] if len(row) > 3 else ""
            wish_text    = row[4] if len(row) > 4 else ""
            if state:
                user_state[user_id] = state
            if pending_json:
                try:
                    user_pending_location_query[user_id] = json.loads(pending_json)
                except (json.JSONDecodeError, TypeError):
                    pass
            if last_query:
                user_last_query[user_id] = last_query
            if wish_text:
                user_wish_text[user_id] = wish_text
        _next_row_number = len(rows) + 1
    restored = len(user_state)
    if restored:
        print(f"[UserState] 已從 Sheets 還原 {restored} 筆等待中狀態", flush=True)


def persist_pending_state(ws_state, user_id, state, pending=None, last_query="", wish_text=""):
    """使用者進入等待回覆的狀態時呼叫，best-effort 寫回 sheet，失敗不影響主流程。"""
    if not ws_state:
        return
    global _next_row_number
    row_values = [
        str(user_id),
        state or "",
        json.dumps(pending, ensure_ascii=False) if pending else "",
        last_query or "",
        wish_text or "",
        datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
    ]
    with _lock:
        row_number = _row_index.get(user_id)
        if not row_number:
            row_number = _next_row_number
            _next_row_number += 1
            _row_index[user_id] = row_number
    try:
        ws_state.update(f"A{row_number}:F{row_number}", [row_values])
    except Exception as e:
        print(f"[UserState 錯誤] 寫入失敗（user={user_id}）：{type(e).__name__}: {e}", flush=True)


def clear_pending_state(ws_state, user_id):
    """等待流程結束（完成或取消）時呼叫；只清空狀態欄位，保留該列供下次重複使用。"""
    if not ws_state:
        return
    with _lock:
        row_number = _row_index.get(user_id)
    if not row_number:
        return
    try:
        ws_state.update(f"B{row_number}:F{row_number}", [["", "", "", "", ""]])
    except Exception as e:
        print(f"[UserState 錯誤] 清除失敗（user={user_id}）：{type(e).__name__}: {e}", flush=True)
