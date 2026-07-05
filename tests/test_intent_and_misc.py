# 意圖正規化、最佳地點查詢判斷、區域範圍、雜項換算
from datetime import date

import pytest
import main


class TestNormalizeIntent:
    def test_known_location_fills_coordinates(self):
        intent = {"location_name": "合歡山", "lat": None, "lon": None}
        result = main.normalize_intent(intent, "這週末合歡山拍銀河")
        assert result["location_name"] == "合歡山"
        assert result["lat"] == pytest.approx(24.167, abs=0.01)
        assert result["lon"] == pytest.approx(121.283, abs=0.01)

    def test_inline_coordinates_override(self):
        intent = {"location_name": "某山", "lat": None, "lon": None}
        result = main.normalize_intent(intent, "座標：23.5, 121.0 今晚銀河")
        assert result["lat"] == pytest.approx(23.5)
        assert result["lon"] == pytest.approx(121.0)

    def test_unknown_location_raises(self):
        intent = {"location_name": "火星基地", "lat": None, "lon": None}
        with pytest.raises(main.LocationResolutionError):
            main.normalize_intent(intent, "火星基地拍銀河")

    def test_non_dict_raises(self):
        with pytest.raises(RuntimeError):
            main.normalize_intent("not a dict", "查詢")

    def test_llm_substituted_location_rejected(self):
        # LLM 回傳與使用者輸入不符的地名時必須拒絕（不猜測原則）
        intent = {"location_name": "合歡山", "lat": 24.167, "lon": 121.283}
        with pytest.raises(main.LocationResolutionError):
            main.normalize_intent(intent, "神祕小徑拍銀河")


class TestBestLocationQuery:
    def test_positive(self):
        assert main.is_best_location_query("今晚哪裡最適合拍銀河")
        assert main.is_best_location_query("週末去哪裡拍星星最好")

    def test_compare_query_excluded(self):
        assert not main.is_best_location_query("合歡山 vs 阿里山 哪裡比較好")
        assert not main.is_best_location_query("合歡山還是阿里山好")

    def test_plain_query_excluded(self):
        assert not main.is_best_location_query("今晚合歡山拍銀河")


class TestRegionScope:
    def test_explicit_region(self):
        assert main.extract_region_scope("北部哪裡拍銀河最好") == "北部"
        assert main.extract_region_scope("南台灣最佳地點") == "南部"
        assert main.extract_region_scope("澎湖拍星") == "離島"
        assert main.extract_region_scope("今晚哪裡最好") == ""

    def test_infer_from_coordinates(self):
        assert main.infer_region_scope_from_coordinates(25.0, 121.5) == "北部"   # 陽明山一帶
        assert main.infer_region_scope_from_coordinates(23.6, 120.9) == "中部"
        assert main.infer_region_scope_from_coordinates(21.945, 120.803) == "南部"  # 墾丁
        assert main.infer_region_scope_from_coordinates(24.167, 121.283) == "東部"  # 合歡山（純座標推斷偏東部，實際靠 region 欄位歸類）
        assert main.infer_region_scope_from_coordinates(23.5, 119.5) == "離島"   # 澎湖


class TestConversions:
    def test_wind_beaufort(self):
        assert main.wind_kmh_to_beaufort(0) == 0
        assert main.wind_kmh_to_beaufort(10) == 2
        assert main.wind_kmh_to_beaufort(30) == 5
        assert main.wind_kmh_to_beaufort(130) == 12
        assert main.wind_kmh_to_beaufort(None) == -1
        assert main.wind_kmh_to_beaufort(-5) == -1

    def test_az_to_direction(self):
        assert main.az_to_direction(0) == "正北"
        assert main.az_to_direction(90) == "正東"
        assert main.az_to_direction(180) == "正南"
        assert main.az_to_direction(270) == "正西"
        assert main.az_to_direction(359) == "正北"

    def test_moon_phase_emoji(self):
        assert "新月" in main.get_moon_phase_emoji(0.0)
        assert "滿月" in main.get_moon_phase_emoji(0.5)
        assert "新月" in main.get_moon_phase_emoji(0.99)


class TestMeteorShower:
    def test_perseids_near_peak(self):
        # 英仙座流星雨極大期約 8/13（TARGET/METEOR 資料表）
        peaks = [(s["peak_month"], s["peak_day"], s["name"]) for s in main.METEOR_SHOWERS]
        assert peaks, "METEOR_SHOWERS 不可為空"
        month, day, name = peaks[0]
        results = main.check_meteor_shower(date(2026, month, day))
        assert any(r["name"] == name for r in results)
        assert results[0]["days_to_peak"] == 0

    def test_far_from_peak_empty(self):
        # 選一個距離所有極大期都超過 3 天的日期
        candidate = date(2026, 3, 1)
        far = all(
            abs((candidate - date(2026, s["peak_month"], s["peak_day"])).days) > 3
            for s in main.METEOR_SHOWERS
        )
        if far:
            assert main.check_meteor_shower(candidate) == []
