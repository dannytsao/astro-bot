# 自定義地點節流重新載入（main.maybe_reload_custom_locations）
# 背景：load_custom_locations() 原本只在 process 啟動時跑一次；使用者直接手動編輯
# 「自定義地點」Sheet 不會被正在執行的 process 看到，要等到下次重啟才生效。
import main


class FakeLocationsWorksheet:
    def __init__(self, rows):
        self.rows = rows  # 不含表頭列
        self.call_count = 0

    def get_all_values(self):
        self.call_count += 1
        return [["地點名稱", "緯度", "經度", "新增時間", "原始查詢"]] + self.rows


class TestMaybeReloadCustomLocations:
    def setup_method(self, _):
        main._custom_locations_last_loaded = float("-inf")

    def teardown_method(self, _):
        main._custom_locations_last_loaded = float("-inf")

    def test_picks_up_manually_added_row(self, monkeypatch):
        name = "測試手動新增地點"
        fake_ws = FakeLocationsWorksheet([[name, "23.111", "121.222", "2026-07-14 10:00", ""]])
        monkeypatch.setattr(main, "ws_locations", fake_ws)
        try:
            assert name not in main.LOCATION_DATA
            main.maybe_reload_custom_locations()
            assert main.LOCATION_DATA[name]["lat"] == 23.111
            assert main.LOCATION_DATA[name]["lon"] == 121.222
            assert main.KNOWN_LOCATIONS[name] == (23.111, 121.222)
        finally:
            main.LOCATION_DATA.pop(name, None)
            main.KNOWN_LOCATIONS.pop(name, None)

    def test_throttled_within_interval(self, monkeypatch):
        fake_ws = FakeLocationsWorksheet([])
        monkeypatch.setattr(main, "ws_locations", fake_ws)
        main.maybe_reload_custom_locations()
        main.maybe_reload_custom_locations()
        main.maybe_reload_custom_locations()
        assert fake_ws.call_count == 1  # 節流期間內只真的讀一次 Sheet

    def test_reloads_again_after_interval_elapsed(self, monkeypatch):
        fake_ws = FakeLocationsWorksheet([])
        monkeypatch.setattr(main, "ws_locations", fake_ws)
        main.maybe_reload_custom_locations()
        real_monotonic = main.time.monotonic
        monkeypatch.setattr(
            main.time, "monotonic",
            lambda: real_monotonic() + main.CUSTOM_LOCATION_RELOAD_INTERVAL_SECONDS + 1,
        )
        main.maybe_reload_custom_locations()
        assert fake_ws.call_count == 2

    def test_find_known_location_in_query_triggers_reload(self, monkeypatch):
        name = "測試查找觸發重載地點"
        fake_ws = FakeLocationsWorksheet([[name, "24.5", "121.6", "2026-07-14 10:00", ""]])
        monkeypatch.setattr(main, "ws_locations", fake_ws)
        main._custom_locations_last_loaded = main.time.monotonic()  # 模擬剛重載過，節流視窗內
        try:
            assert name not in main.LOCATION_DATA
            # 節流視窗內：手動新增到 sheet 的地點還看不到，這是修這個 bug 前的行為
            assert main.find_known_location_in_query(f"7/17 {name} 適合拍星?") == ""

            main._custom_locations_last_loaded = float("-inf")  # 模擬節流視窗已過
            assert main.find_known_location_in_query(f"7/17 {name} 適合拍星?") == name
        finally:
            main.LOCATION_DATA.pop(name, None)
            main.KNOWN_LOCATIONS.pop(name, None)
