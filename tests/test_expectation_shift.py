#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「市場期待値シフト」機能 STEP1(第9優先改修③、ユーザー提案)の回帰テスト。

STEP1のスコープは「米国金利見通し」のテーマ基盤の新設のみ。
- fed_dovish(新設): FRB/FOMC/米国政策金利の緩和方向に限定、日銀を除外
- expectation_shift_pairs(新設トップレベルキー): us_rate_outlookペア定義

このファイルの時点ではシフト判定ロジック・永続ログ・expectation_shift
フィールド・LLMは一切実装しない(調査結果の報告どおり)。既存の
rate_cut/fed_hawkishは変更していないことも確認する。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from newssite import impact as impact_mod  # noqa: E402


class FedDovishThemeMatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = impact_mod.load()

    def test_frb_rate_cut_headline_matches_fed_dovish(self):
        hit = [t["id"] for t in impact_mod.match_themes("FRBが利下げを決定、市場は緩和路線を歓迎", self.rules)]
        self.assertIn("fed_dovish", hit)

    def test_fomc_rate_cut_headline_matches_fed_dovish(self):
        hit = [t["id"] for t in impact_mod.match_themes("FOMCが利下げを決定", self.rules)]
        self.assertIn("fed_dovish", hit)

    def test_us_policy_rate_cut_headline_matches_fed_dovish(self):
        hit = [t["id"] for t in impact_mod.match_themes("米政策金利の引き下げを発表、パウエル議長はハト派姿勢", self.rules)]
        self.assertIn("fed_dovish", hit)

    def test_frb_rate_hike_headline_matches_fed_hawkish_not_dovish(self):
        hit = [t["id"] for t in impact_mod.match_themes("FRBが利上げを決定、パウエル議長はタカ派姿勢を強調", self.rules)]
        self.assertIn("fed_hawkish", hit)
        self.assertNotIn("fed_dovish", hit)

    def test_us_policy_rate_hold_headline_matches_fed_hawkish(self):
        hit = [t["id"] for t in impact_mod.match_themes("米政策金利は据え置き、米金利は高止まりの見通し", self.rules)]
        self.assertIn("fed_hawkish", hit)
        self.assertNotIn("fed_dovish", hit)

    def test_boj_headline_matches_neither_fed_theme(self):
        # 日銀関連の見出しはfed_dovish/fed_hawkishどちらにもならない
        # (excludeで日銀を排除しているため。exclude設定の回帰確認)。
        hit_hike = [t["id"] for t in impact_mod.match_themes("日銀が利上げを決定", self.rules)]
        self.assertNotIn("fed_dovish", hit_hike)
        self.assertNotIn("fed_hawkish", hit_hike)
        hit_cut = [t["id"] for t in impact_mod.match_themes("日銀が利下げを検討、緩和的な姿勢", self.rules)]
        self.assertNotIn("fed_dovish", hit_cut)
        self.assertNotIn("fed_hawkish", hit_cut)


class RateCutUnchangedTest(unittest.TestCase):
    """既存rate_cutの挙動がfed_dovish新設によって変わっていないことの確認
    (rate_cut自体は変更していないが、新テーマ追加による意図しない副作用が
    無いことを固定する)。"""

    @classmethod
    def setUpClass(cls):
        cls.rules = impact_mod.load()

    def test_rate_cut_theme_definition_is_unchanged(self):
        theme = next(t for t in self.rules.themes if t["id"] == "rate_cut")
        self.assertEqual(theme["keywords"], ["利下げ", "金融緩和", "緩和的", "ハト派"])
        self.assertEqual(theme["exclude"], [])
        self.assertEqual(len(theme["impacts"]), 2)

    def test_rate_cut_still_matches_boj_dovish_headline(self):
        hit = [t["id"] for t in impact_mod.match_themes("日銀が利下げを検討、緩和的な姿勢", self.rules)]
        self.assertIn("rate_cut", hit)

    def test_rate_cut_still_matches_generic_dovish_headline_alongside_fed_dovish(self):
        # FRBの利下げ記事はrate_cutとfed_dovishの両方にヒットする(想定どおりの
        # 多重ヒット。tests/test_rule_audit.pyのALLOWED_KEYWORD_OVERLAPSで
        # 方向が矛盾しないことを確認済み)。
        hit = [t["id"] for t in impact_mod.match_themes("FRBが利下げを決定", self.rules)]
        self.assertIn("rate_cut", hit)
        self.assertIn("fed_dovish", hit)


class FedHawkishUnchangedTest(unittest.TestCase):
    """既存fed_hawkishの挙動がfed_dovish新設によって変わっていないことの確認。"""

    @classmethod
    def setUpClass(cls):
        cls.rules = impact_mod.load()

    def test_fed_hawkish_theme_definition_is_unchanged(self):
        theme = next(t for t in self.rules.themes if t["id"] == "fed_hawkish")
        self.assertEqual(theme["keywords"], [
            "FRB 利上げ", "FOMC 利上げ", "米利上げ", "米政策金利 据え置き", "米金利 高止まり", "パウエル議長 タカ派",
        ])
        self.assertEqual(theme["exclude"], ["日銀"])


class ExpectationShiftPairsTest(unittest.TestCase):
    """expectation_shift_pairs(新規トップレベルキー)が正しく読み込めることの確認。
    判定ロジックはまだ実装しないため、定義の読み込みだけを確認する。"""

    @classmethod
    def setUpClass(cls):
        cls.rules = impact_mod.load()

    def test_expectation_shift_pairs_is_loaded_on_rules_object(self):
        self.assertTrue(hasattr(self.rules, "expectation_shift_pairs"))
        self.assertIsInstance(self.rules.expectation_shift_pairs, list)

    def test_us_rate_outlook_pair_is_defined(self):
        pairs = {p["id"]: p for p in self.rules.expectation_shift_pairs}
        self.assertIn("us_rate_outlook", pairs)
        pair = pairs["us_rate_outlook"]
        self.assertEqual(pair["dovish_theme"], "fed_dovish")
        self.assertEqual(pair["hawkish_theme"], "fed_hawkish")

    def test_pair_theme_ids_resolve_to_real_themes(self):
        theme_ids = {t["id"] for t in self.rules.themes}
        for pair in self.rules.expectation_shift_pairs:
            self.assertIn(pair["dovish_theme"], theme_ids)
            self.assertIn(pair["hawkish_theme"], theme_ids)

    def test_no_shift_detection_logic_yet(self):
        # STEP1の時点では、ニュース項目にexpectation_shiftフィールドを
        # 追加する判定ロジックはまだ存在しない(analyze.pyに実装が無いこと
        # の確認。STEP2以降の実装対象)。
        import inspect
        from newssite import analyze
        src = inspect.getsource(analyze)
        self.assertNotIn("expectation_shift", src, "STEP1の時点でexpectation_shift判定ロジックが実装されています(スコープ外)")


if __name__ == "__main__":
    unittest.main()
