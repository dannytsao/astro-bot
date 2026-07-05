# 地點審核制：pending 用戶地點不得進排名與意圖解析目錄
import main


class TestRankingGate:
    def test_approved_location_ranks(self):
        assert main.is_ranking_location({"review_status": "approved", "source": "legacy-curated"})

    def test_pending_user_location_excluded(self):
        assert not main.is_ranking_location({"review_status": "pending", "source": "user-provided"})

    def test_missing_status_excluded(self):
        assert not main.is_ranking_location({"source": "user-provided"})
        assert not main.is_ranking_location({})


class TestPromptCatalog:
    def test_pending_location_not_in_catalog(self):
        name = "測試用未審核地點"
        main.LOCATION_DATA[name] = {
            "lat": 23.5, "lon": 121.0, "aliases": [],
            "source": "user-provided", "confidence": "user",
            "review_status": "pending",
        }
        main.KNOWN_LOCATIONS[name] = (23.5, 121.0)
        try:
            catalog = main.location_prompt_catalog()
            assert name not in catalog
            assert "合歡山" in catalog
            # 但該用戶自己的查詢仍可解析（KNOWN_LOCATIONS 保留 pending 地點）
            intent = main.normalize_intent(
                {"location_name": name, "lat": None, "lon": None},
                f"{name} 今晚銀河")
            assert intent["lat"] == 23.5
        finally:
            main.LOCATION_DATA.pop(name, None)
            main.KNOWN_LOCATIONS.pop(name, None)

    def test_save_custom_location_marks_pending(self):
        name = "測試用新增地點"
        try:
            main.save_custom_location(name, 23.6, 121.1)
            assert main.LOCATION_DATA[name]["review_status"] == "pending"
            assert not main.is_ranking_location(main.LOCATION_DATA[name])
        finally:
            main.LOCATION_DATA.pop(name, None)
            main.KNOWN_LOCATIONS.pop(name, None)
