# 標的匹配（_target_matches / match_targets / find_unmatched_targets）
# 回歸保護：m2 不可誤中 m20；m1 不可誤中 m10/m100（commit 905a858）
import main


def _target_by_name(name):
    for t in main.TARGET_LIBRARY:
        if t["name"] == name:
            return t
    raise AssertionError(f"TARGET_LIBRARY 中找不到 {name}")


class TestTargetMatches:
    def test_exact_name(self):
        t = _target_by_name("銀河核心")
        assert main._target_matches("銀河核心", t)

    def test_alias(self):
        t = _target_by_name("銀河核心")
        assert main._target_matches("milky way", t)
        assert main._target_matches("銀河", t)

    def test_m_number_boundary_m2_not_m20(self):
        # m2 查詢不可匹配到含 m20 alias 的標的
        fake_m20 = {"name": "三裂星雲 M20", "aliases": ["m20"]}
        fake_m2 = {"name": "球狀星團 M2", "aliases": ["m2"]}
        assert not main._target_matches("m2", fake_m20)
        assert main._target_matches("m2", fake_m2)
        assert main._target_matches("m20", fake_m20)
        assert not main._target_matches("m20", fake_m2)

    def test_m_number_boundary_m1_not_m10_m100(self):
        fake_m1 = {"name": "蟹狀星雲 M1", "aliases": ["m1"]}
        fake_m10 = {"name": "球狀星團 M10", "aliases": ["m10"]}
        fake_m100 = {"name": "星系 M100", "aliases": ["m100"]}
        assert main._target_matches("m1", fake_m1)
        assert not main._target_matches("m1", fake_m10)
        assert not main._target_matches("m1", fake_m100)
        assert not main._target_matches("m100", fake_m1)

    def test_substring_both_directions(self):
        t = _target_by_name("獵戶座")
        assert main._target_matches("獵戶", t)          # query 是 alias
        assert main._target_matches("獵戶座流星", t)     # alias 是 query 的子字串


class TestMatchTargets:
    def test_empty_returns_full_library(self):
        assert main.match_targets([]) == main.TARGET_LIBRARY
        assert main.match_targets(None) == main.TARGET_LIBRARY

    def test_known_target(self):
        matched = main.match_targets(["銀河"])
        assert any(t["name"] == "銀河核心" for t in matched)

    def test_unmatched_detection(self):
        matched = main.match_targets(["銀河", "土星環"])
        unmatched = main.find_unmatched_targets(["銀河", "土星環"], matched)
        assert "土星環" in unmatched
        assert "銀河" not in unmatched
