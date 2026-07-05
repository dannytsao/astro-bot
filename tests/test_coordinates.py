# 座標解析（extract_user_coordinates / is_in_taiwan_loose_range）
import pytest
import main


class TestExtractUserCoordinates:
    def test_labeled_format(self):
        assert main.extract_user_coordinates("座標：23.124, 121.216") == (23.124, 121.216)

    def test_chinese_label_format(self):
        assert main.extract_user_coordinates("北緯 23.124 東經 121.216") == (23.124, 121.216)

    def test_lat_lon_english(self):
        assert main.extract_user_coordinates("lat=23.5 lon=121.0") == (23.5, 121.0)

    def test_swapped_lon_lat_auto_corrected(self):
        # 先經度後緯度（台灣範圍判斷）應自動交換
        assert main.extract_user_coordinates("121.216, 23.124") == (23.124, 121.216)

    def test_space_separated(self):
        assert main.extract_user_coordinates("23.124 121.216") == (23.124, 121.216)

    def test_out_of_range_raises(self):
        with pytest.raises(ValueError):
            main.extract_user_coordinates("北緯 95.0 東經 121.0")

    def test_no_coordinates_returns_none(self):
        assert main.extract_user_coordinates("今晚合歡山拍銀河") is None

    def test_date_not_parsed_as_coordinates(self):
        # 回歸保護（commit 99ab4e5）：日期不可被解析成座標
        assert main.extract_user_coordinates("6/20 合歡山 銀河") is None


class TestTaiwanLooseRange:
    def test_inside_taiwan(self):
        assert main.is_in_taiwan_loose_range(23.5, 121.0)

    def test_outside_taiwan(self):
        assert not main.is_in_taiwan_loose_range(35.68, 139.69)  # 東京
