# 紅藍軍程式層防線（enforce_no_go_language）
from datetime import date

import main


def _cci(score):
    return {"score": score, "label": "x"}


class TestEnforceNoGo:
    def test_low_cci_without_no_go_gets_prefix(self):
        reply = "今晚條件普通，仍有機會拍到銀河。"
        out = main.enforce_no_go_language(reply, {date(2026, 7, 5): _cci(25)})
        assert out.startswith("❌ 出勤判定")
        assert "07/05" in out
        assert "不建議出勤" in out
        assert reply in out  # 原始回覆保留在後

    def test_low_cci_with_no_go_untouched(self):
        reply = "07/05 ❌ 信心度 25%，不建議出勤。"
        out = main.enforce_no_go_language(reply, {date(2026, 7, 5): _cci(25)})
        assert out == reply

    def test_high_cci_untouched(self):
        reply = "今晚條件絕佳！"
        out = main.enforce_no_go_language(reply, {date(2026, 7, 5): _cci(85)})
        assert out == reply

    def test_mixed_dates_only_low_listed(self):
        reply = "多日分析如下。"
        cci = {date(2026, 7, 5): _cci(25), date(2026, 7, 6): _cci(80)}
        out = main.enforce_no_go_language(reply, cci)
        assert "07/05" in out
        assert "07/06" not in out.split("\n")[0]

    def test_empty_inputs(self):
        assert main.enforce_no_go_language("", {}) == ""
        assert main.enforce_no_go_language("回覆", {}) == "回覆"
        assert main.enforce_no_go_language(None, {date(2026, 7, 5): _cci(10)}) is None
