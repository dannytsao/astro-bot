# 測試環境設定：在 import main 之前注入假環境變數，避免 LINE/Sheets/OpenRouter 初始化失敗。
# 執行方式（repo 根目錄）：python3 -m pytest tests/ -q
# 注意：skyfield 首次執行會下載 de421.bsp 到 repo 根目錄（約 17MB，已列入 .gitignore）。
import os
import sys
from pathlib import Path

os.environ.setdefault("LINE_CHANNEL_SECRET", "test-dummy-secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test-dummy-token")

# 確保 repo 根目錄在 sys.path，讓 `import main` 成功
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
