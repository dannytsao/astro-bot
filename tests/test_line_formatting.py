# LINE 純文字輸出（strip_markdown_for_line）
import main


class TestStripMarkdown:
    def test_headings_removed(self):
        text = "# 大標題\n## 小標題\n內文"
        assert main.strip_markdown_for_line(text) == "大標題\n小標題\n內文"

    def test_bold_unwrapped(self):
        assert main.strip_markdown_for_line("**07/05 最佳時刻**") == "07/05 最佳時刻"
        assert main.strip_markdown_for_line("結論：**不建議出勤**，原因如下") == "結論：不建議出勤，原因如下"

    def test_horizontal_rules_removed(self):
        text = "【結論】\n---\n內容\n***\n結尾"
        out = main.strip_markdown_for_line(text)
        assert "---" not in out
        assert "***" not in out
        assert "內容" in out

    def test_excess_blank_lines_collapsed(self):
        out = main.strip_markdown_for_line("A\n\n\n\n\nB")
        assert out == "A\n\nB"

    def test_plain_text_untouched(self):
        text = "【結論】\n07/05 🟢 信心度 70%｜雲量12%・暗空2h09m\n・視寧度差\n・結露風險高"
        assert main.strip_markdown_for_line(text) == text

    def test_negative_temperature_not_mangled(self):
        # 行內的 - 與數學負號不可被誤刪（只刪整行分隔線）
        text = "溫度 -5°C，溫差 T-Td=0.8°C"
        assert main.strip_markdown_for_line(text) == text

    def test_bullet_dash_lines_kept(self):
        text = "- 快門：20 秒\n- ISO：3200"
        assert main.strip_markdown_for_line(text) == text

    def test_empty_input(self):
        assert main.strip_markdown_for_line("") == ""
        assert main.strip_markdown_for_line(None) is None
