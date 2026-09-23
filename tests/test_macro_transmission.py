#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""マクロ材料の伝播経路(第9優先改修②、ユーザー提案)の回帰テスト。

「FRB→金利→グロース→半導体・AI→日経先物→日本の半導体・値がさ株→日経平均」
という固定の因果の連なりを、rules.jsonのthemes[].transmission_chain(任意項目)
から拾って表示するだけの機能。既存のtheme/direction/impacts判定(FACTUAL LAYER)
には一切影響しない。
"""
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from newssite import analyze, impact as impact_mod, render  # noqa: E402
from newssite.config import JST  # noqa: E402


def _raw_item(id_, title, source="ロイター", source_tier="secondary"):
    return {
        "id": id_, "title": title, "url": f"https://example.com/{id_}",
        "source": source, "source_tier": source_tier, "published": datetime.now(JST),
        "related": [], "feed_category": "policy", "feed_weight": 2, "feed_categories": ["policy"],
    }


class TransmissionChainDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = impact_mod.load()

    def test_fed_hawkish_theme_has_transmission_chain(self):
        theme = next(t for t in self.rules.themes if t["id"] == "fed_hawkish")
        chain = theme.get("transmission_chain")
        self.assertIsInstance(chain, list)
        self.assertEqual(chain[0], "FRB")
        self.assertEqual(chain[-1], "日経平均")
        self.assertIn("グロース", chain)
        self.assertIn("半導体・AI", chain)

    def test_boj_hike_has_no_transmission_chain(self):
        # transmission_chainは任意項目。既存テーマに勝手に追加していないことの確認
        # (①段階で新設したfed_hawkishだけの機能追加であることを固定する)。
        theme = next(t for t in self.rules.themes if t["id"] == "boj_hike")
        self.assertNotIn("transmission_chain", theme)


class TransmissionChainBuildNewsTest(unittest.TestCase):
    def test_fed_hawkish_news_item_gets_transmission_chain(self):
        news = analyze.build_news(
            raw_items=[_raw_item("f1", "FRBが利上げを決定、パウエル議長はタカ派姿勢を強調")],
            use_llm=False, persist_lifecycle=False,
        )
        self.assertEqual(len(news), 1)
        self.assertIn("fed_hawkish", news[0]["theme_ids"])
        self.assertEqual(news[0]["transmission_chain"][0], "FRB")
        self.assertEqual(news[0]["transmission_chain"][-1], "日経平均")

    def test_unrelated_news_item_has_no_transmission_chain(self):
        news = analyze.build_news(
            raw_items=[_raw_item("u1", "訪日客数が過去最高を更新")],
            use_llm=False, persist_lifecycle=False,
        )
        self.assertEqual(len(news), 1)
        self.assertIsNone(news[0]["transmission_chain"])

    def test_boj_hike_news_item_has_no_transmission_chain(self):
        # boj_hikeはtransmission_chainを持たないテーマなので、そのニュースには
        # 経路が付かない(他の一致テーマにも無ければNoneのまま)。
        news = analyze.build_news(
            raw_items=[_raw_item("b1", "日銀が利上げを決定")],
            use_llm=False, persist_lifecycle=False,
        )
        self.assertEqual(len(news), 1)
        self.assertIn("boj_hike", news[0]["theme_ids"])
        self.assertIsNone(news[0]["transmission_chain"])


class TransmissionChainRenderTest(unittest.TestCase):
    def _item(self, **overrides):
        base = {
            "id": "n1", "title": "タイトル", "url": "https://example.com/n1", "source": "ロイター",
            "source_tier": None, "source_tier_label": "", "published_at": "09/23 09:00", "category": "policy",
            "category_label": "金融政策・金利", "category_emoji": "🏦", "importance": 3, "importance_reason": "reason",
            "future_signal": False, "policy_maturity": None, "policy_maturity_label": "",
            "news_novelty": "high", "news_novelty_label": "新規",
            "policy_event_id": None, "policy_event_is_update": None, "policy_event_first_seen": None,
            "policy_event_state": None, "themes": [], "theme_ids": [], "summary": "", "impact_comment": "",
            "impacts": [], "related": [], "transmission_chain": None,
        }
        base.update(overrides)
        return base

    def test_news_card_html_renders_chain_when_present(self):
        html = render.news_card_html(self._item(transmission_chain=["FRB", "金利", "グロース", "日経平均"]), datetime.now(JST))
        self.assertIn("transmission-chain", html)
        self.assertIn("FRB", html)
        self.assertIn("日経平均", html)
        self.assertIn("→", html)

    def test_news_card_html_omits_chain_block_when_absent(self):
        html = render.news_card_html(self._item(transmission_chain=None), datetime.now(JST))
        self.assertNotIn("transmission-chain", html)

    def test_transmission_chain_note_disclaims_prediction(self):
        # CLAUDE.mdの「断定表現を書かない」方針の確認。経路図が「一例」であり
        # 値動きを保証しないことを明示する注記が付いていること。
        html = render.news_card_html(self._item(transmission_chain=["FRB", "日経平均"]), datetime.now(JST))
        self.assertIn("実際の値動きを保証するものではありません", html)


if __name__ == "__main__":
    unittest.main()
