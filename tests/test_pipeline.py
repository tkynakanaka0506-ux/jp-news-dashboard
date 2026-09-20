#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ニュースサイト生成の主要ロジックのテスト。

  python3 -m unittest discover -s tests -v

ネットワークには接続せず、RSS取得部分はローカルのXML文字列に差し替えて検証する。
"""
import io
import json
import re
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from newssite import analyze, impact as impact_mod, render, rss, stocks as stocks_mod, theme_trends  # noqa: E402
from newssite.config import JST  # noqa: E402


class _FrozenDatetime(datetime):
    """rss.collect()が参照するdatetime.now()を固定するテスト用サブクラス。

    make_rss()のpubDateはSep 2026にハードコードされており、これをそのまま
    max_age_hours(既定48h)のフィルタに通すには「今」も同じ時点に固定する
    必要がある(そうしないと実行日が進むにつれ記事が古すぎると判定され、
    テストが実行日依存でいつか壊れるタイムボムになる)。
    """
    _frozen_now = datetime(2026, 9, 13, 1, 0, tzinfo=JST)

    @classmethod
    def now(cls, tz=None):
        return cls._frozen_now.astimezone(tz) if tz else cls._frozen_now

RSS_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
{items}
</channel></rss>"""

ITEM_TEMPLATE = """<item>
  <title>{title} - {source}</title>
  <link>{link}</link>
  <pubDate>{pub}</pubDate>
  <source url="https://example.com">{source}</source>
</item>"""


def make_rss(entries):
    items = "\n".join(
        ITEM_TEMPLATE.format(title=t, source=s, link=l, pub="Sat, 13 Sep 2026 00:00:00 GMT")
        for t, s, l in entries
    )
    return RSS_TEMPLATE.format(items=items).encode("utf-8")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


class RssTest(unittest.TestCase):
    def test_fetch_parses_and_strips_source_suffix(self):
        payload = make_rss([("日銀が追加利上げを決定", "日本経済新聞", "https://example.com/a")])
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(payload)):
            items = rss.fetch("dummy", "policy", 2)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "日銀が追加利上げを決定")
        self.assertEqual(items[0]["source"], "日本経済新聞")
        self.assertEqual(items[0]["feed_category"], "policy")
        self.assertIsNotNone(items[0]["published"])

    def test_fetch_returns_empty_on_error(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("boom")):
            self.assertEqual(rss.fetch("dummy"), [])

    def test_collect_merges_duplicate_topics(self):
        payload = make_rss([
            ("日銀が追加利上げを決定 政策金利0.75%へ", "A社", "https://example.com/1"),
            ("日銀が追加利上げを決定 市場は上昇", "B社", "https://example.com/2"),
            ("訪日客が過去最高を更新", "C社", "https://example.com/3"),
        ])
        with mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: FakeResponse(payload)), \
                mock.patch("newssite.rss.datetime", _FrozenDatetime):
            items = rss.collect([("q1", "policy", 2)])
        self.assertEqual(len(items), 2)
        merged = items[0]
        self.assertEqual(len(merged["related"]), 1)
        self.assertEqual(merged["related"][0]["source"], "B社")

    def test_collect_drops_old_articles(self):
        payload = make_rss([("古いニュース", "A社", "https://example.com/old")])
        payload = payload.replace(b"Sat, 13 Sep 2026 00:00:00 GMT", b"Mon, 01 Jan 2001 00:00:00 GMT")
        with mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: FakeResponse(payload)):
            self.assertEqual(rss.collect([("q1", "policy", 1)], max_age_hours=24), [])

    # ---- ⑤ 一次情報/二次情報/市場解説(source_tier) ----------

    def test_fetch_tags_secondary_by_default(self):
        payload = make_rss([("日銀が追加利上げを決定", "日本経済新聞", "https://example.com/a")])
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(payload)):
            items = rss.fetch("dummy")
        self.assertEqual(items[0]["source_tier"], "secondary")

    def test_fetch_tags_commentary_for_known_commentary_outlets(self):
        payload = make_rss([("トヨタの上方修正をどう読むか", "東洋経済オンライン", "https://example.com/a")])
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(payload)):
            items = rss.fetch("dummy")
        self.assertEqual(items[0]["source_tier"], "commentary")

    def test_fetch_direct_always_tags_primary(self):
        rdf = (
            '<?xml version="1.0"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<item><title>省庁の発表</title><link>https://example.com/gov</link></item></rdf:RDF>'
        ).encode("utf-8")
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(rdf)):
            items = rss.fetch_direct("https://example.com/rss", "経済産業省")
        self.assertEqual(items[0]["source_tier"], "primary")


class SourceTierScoreTest(unittest.TestCase):
    """⑤ policy_impact_scoreへの信頼度反映(compute_policy_impact_score)。"""

    def _entry(self):
        return {"beneficiary_tier": "direct", "revenue_horizon": "0-3m", "strength": "大"}

    def test_primary_source_does_not_discount(self):
        base = impact_mod.compute_policy_impact_score(self._entry(), 100, None)
        primary = impact_mod.compute_policy_impact_score(self._entry(), 100, "primary")
        self.assertEqual(primary, base)

    def test_secondary_source_discounts_score(self):
        base = impact_mod.compute_policy_impact_score(self._entry(), 100, None)
        secondary = impact_mod.compute_policy_impact_score(self._entry(), 100, "secondary")
        self.assertLess(secondary, base)
        self.assertEqual(secondary, round(base * 0.85))

    def test_commentary_source_discounts_more_than_secondary(self):
        base = impact_mod.compute_policy_impact_score(self._entry(), 100, None)
        secondary = impact_mod.compute_policy_impact_score(self._entry(), 100, "secondary")
        commentary = impact_mod.compute_policy_impact_score(self._entry(), 100, "commentary")
        self.assertLess(commentary, secondary)
        self.assertEqual(commentary, round(base * 0.65))

    def test_unknown_source_tier_does_not_discount(self):
        """source_tier不明(テスト等)なら従来通り割り引かない(勝手に不信を疑わない)。"""
        base = impact_mod.compute_policy_impact_score(self._entry(), 100, None)
        unknown = impact_mod.compute_policy_impact_score(self._entry(), 100, "something_new")
        self.assertEqual(unknown, base)

    def test_direction_theme_tier_untouched_by_source_tier(self):
        """FACTUAL LAYERには一切影響しない(スコアの数値だけが変わる)。"""
        entry = self._entry()
        snapshot = dict(entry)
        impact_mod.compute_policy_impact_score(entry, 100, "commentary")
        self.assertEqual(entry, snapshot, "compute_policy_impact_scoreがimpact_entryを書き換えてはいけない")


class PolicyToEarningsStageTest(unittest.TestCase):
    """② 政策→業績距離の厳密化。既存のpolicy_maturityとrevenue_horizonの
    組み合わせだけで判定する(新しいキーワード判定は増やさない)。"""

    def test_none_maturity_returns_none(self):
        self.assertIsNone(impact_mod.policy_to_earnings_stage(None, "0-3m"))

    def test_early_maturity_is_policy_announcement_regardless_of_horizon(self):
        for maturity in (20, 25, 35):  # 検討・議論・審議会
            for horizon in ("0-3m", "1-3y", None):
                self.assertEqual(impact_mod.policy_to_earnings_stage(maturity, horizon), "政策発表")

    def test_mid_maturity_is_bill_stage(self):
        for maturity in (45, 60):  # パブコメ・法案提出
            self.assertEqual(impact_mod.policy_to_earnings_stage(maturity, "0-3m"), "法案化")

    def test_high_maturity_is_budget_secured(self):
        for maturity in (75, 85):  # 法案成立・予算成立
            self.assertEqual(impact_mod.policy_to_earnings_stage(maturity, "0-3m"), "予算確保")

    def test_implemented_maturity_uses_horizon_to_pick_downstream_stage(self):
        self.assertEqual(impact_mod.policy_to_earnings_stage(100, "0-3m"), "受注")
        self.assertEqual(impact_mod.policy_to_earnings_stage(100, "3-6m"), "設備投資")
        self.assertEqual(impact_mod.policy_to_earnings_stage(100, "6-12m"), "売上寄与")
        self.assertEqual(impact_mod.policy_to_earnings_stage(100, "1-3y"), "利益寄与")

    def test_implemented_maturity_without_horizon_falls_back_to_implementation_label(self):
        self.assertEqual(impact_mod.policy_to_earnings_stage(100, None), "施策実施")
        self.assertEqual(impact_mod.policy_to_earnings_stage(100, "unknown-bucket"), "施策実施")


class ImpactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = impact_mod.load()
        cls.master = stocks_mod.load()

    def _item(self, title, **kw):
        base = {"title": title, "feed_weight": 1, "feed_category": "market",
                "related": [], "feed_categories": ["market"]}
        base.update(kw)
        return base

    def test_theme_keyword_and_exclusion(self):
        weak = [t["id"] for t in impact_mod.match_themes("円安が進行、1ドル158円台", self.rules)]
        self.assertIn("yen_weak", weak)
        self.assertNotIn("yen_strong", weak)
        both = [t["id"] for t in impact_mod.match_themes("円安から円高に転換", self.rules)]
        self.assertNotIn("yen_weak", both)

    def test_and_condition_keyword(self):
        # "日銀 利上げ" は AND 条件。両方含む見出しだけ一致する。
        hit = [t["id"] for t in impact_mod.match_themes("日銀が利上げを決定", self.rules)]
        self.assertIn("boj_hike", hit)
        miss = [t["id"] for t in impact_mod.match_themes("日銀総裁が記者会見", self.rules)]
        self.assertNotIn("boj_hike", miss)

    def test_importance_rises_with_keyword_and_coverage(self):
        plain = self._item("東証、小幅続伸で取引を終える")
        big = self._item("日銀が追加利上げを決定、長期金利は上昇",
                         related=[{}, {}], feed_weight=2, feed_categories=["policy", "market"])
        plain_score, _ = impact_mod.score_importance(plain, impact_mod.match_themes(plain["title"], self.rules), self.rules)
        big_score, reason = impact_mod.score_importance(big, impact_mod.match_themes(big["title"], self.rules), self.rules)
        self.assertLess(plain_score, big_score)
        self.assertEqual(big_score, 5)
        self.assertIn("利上げ", reason)

    def test_affected_stocks_cover_both_directions(self):
        item = self._item("日銀が追加利上げを決定、長期金利は上昇")
        themes = impact_mod.match_themes(item["title"], self.rules)
        impacts = impact_mod.affected_stocks(item, themes, self.rules, self.master, max_items=8)
        directions = {i["direction"] for i in impacts}
        self.assertIn("positive", directions)
        self.assertIn("negative", directions)
        banks = [i for i in impacts if i["code"] == "8306"]
        self.assertEqual(banks[0]["direction"], "positive")
        self.assertTrue(all(i["reason"] for i in impacts))

    def test_named_company_becomes_direct_impact(self):
        item = self._item("トヨタ自動車、通期予想を上方修正 過去最高益へ")
        themes = impact_mod.match_themes(item["title"], self.rules)
        impacts = impact_mod.affected_stocks(item, themes, self.rules, self.master)
        self.assertEqual(impacts[0]["code"], "7203")
        self.assertEqual(impacts[0]["origin"], "direct")
        self.assertEqual(impacts[0]["direction"], "positive")

    def test_named_company_negative_headline(self):
        item = self._item("日産自動車、通期予想を下方修正 赤字転落へ")
        impacts = impact_mod.affected_stocks(item, [], self.rules, self.master)
        self.assertEqual(impacts[0]["code"], "7201")
        self.assertEqual(impacts[0]["direction"], "negative")

    def test_all_rule_themes_resolve_to_real_stocks(self):
        """rules.json の themes タグが stocks.json に存在することを保証する。"""
        for theme in self.rules.themes:
            for rule in theme.get("impacts", []):
                targets = self.master.by_themes(rule.get("themes", []))
                codes = [c for c in rule.get("codes", []) if c in self.master.by_code]
                self.assertTrue(
                    targets or codes,
                    f"テーマ {theme['id']} の impacts が1銘柄も解決できません: {rule.get('themes')}",
                )

    def test_all_theme_categories_exist(self):
        for theme in self.rules.themes:
            self.assertIn(theme["category"], self.rules.category_label, theme["id"])

    def test_geopolitics_headline_also_surfaces_safe_haven_gold_as_another_angle(self):
        # ユーザー要望(2026-09-20): コロナ禍で金(ゴールド)の価値が上がったように、
        # 地政学ニュースは「直接の影響銘柄」とは別角度で、安全資産(金)にも
        # 資金が向かいやすいという波及がある。この「別角度」の視点を
        # 地政学系テーマの影響銘柄にも反映する。
        item = self._item("イスラエルとイランの緊張が高まる、軍事衝突の懸念")
        themes = impact_mod.match_themes(item["title"], self.rules)
        self.assertIn("middle_east", [t["id"] for t in themes])
        impacts = impact_mod.affected_stocks(item, themes, self.rules, self.master, max_items=8)
        gold_etf = [i for i in impacts if i["code"] == "1540"]
        self.assertTrue(gold_etf, "地政学リスクの見出しで安全資産(金ETF 1540)が影響銘柄に出ていません")
        self.assertEqual(gold_etf[0]["direction"], "positive")

    def test_rare_earth_export_curb_headline_hits_dependent_and_countermeasure_stocks(self):
        # ユーザー要望(2026-09-20)「レアアースの内容は取得できているか」への
        # 対応。中国のレアアース輸出規制は2026年に日本企業へ実際に影響が
        # 出ている話題(信越化学工業が輸出規制報道で下落、双日・住友金属鉱山は
        # 権益確保・供給網の脱中国依存を進めている、と報じられている)。
        item = self._item("中国、レアアースの対日輸出規制を強化")
        themes = impact_mod.match_themes(item["title"], self.rules)
        self.assertIn("rare_earth", [t["id"] for t in themes])
        impacts = impact_mod.affected_stocks(item, themes, self.rules, self.master, max_items=8)
        by_code = {i["code"]: i for i in impacts}
        self.assertIn("4063", by_code, "レアアース輸出規制で信越化学工業(部材調達への逆風)が出ていません")
        self.assertEqual(by_code["4063"]["direction"], "negative")
        countermeasure = [i for i in impacts if i["code"] in ("2768", "5713")]
        self.assertTrue(countermeasure, "レアアース輸出規制で供給網対応銘柄(双日/住友金属鉱山)が出ていません")
        self.assertEqual(countermeasure[0]["direction"], "positive")

    def test_ukraine_headline_treats_safe_haven_direction_as_watch_not_fixed(self):
        # ukraine_russiaは「停戦」報道もありうるテーマなので、安全資産への
        # 資金シフトも(資源エネルギー・防衛と同様に)方向固定にせず、
        # 見出しの語調(headline_sentiment)に従うwatchにする。
        theme = next(t for t in self.rules.themes if t["id"] == "ukraine_russia")
        safe_haven_rule = next(r for r in theme["impacts"] if r["themes"] == ["安全資産"])
        self.assertEqual(safe_haven_rule["direction"], "watch")


class AnalyzeTest(unittest.TestCase):
    def test_stock_ranking_aggregates_direction(self):
        news = [
            {"title": "n1", "url": "u1", "importance": 5,
             "impacts": [{"code": "8306", "name": "三菱UFJ", "sector": "銀行", "direction": "positive", "reason": "r"}]},
            {"title": "n2", "url": "u2", "importance": 3,
             "impacts": [{"code": "8306", "name": "三菱UFJ", "sector": "銀行", "direction": "negative", "reason": "r"}]},
        ]
        rows = analyze.stock_ranking(news)
        self.assertEqual(rows[0]["code"], "8306")
        self.assertEqual(rows[0]["mentions"], 2)
        self.assertEqual(rows[0]["positive"], 1)
        self.assertEqual(rows[0]["negative"], 1)
        self.assertEqual(rows[0]["score"], 5 - 3)

    # build_theme_clusters: ユーザー要望(2026-09-20)「一見異なる内容の
    # ニュースでも、関連する企業・業界・資源・国地域・政策・規制などに
    # 共通点がある場合、自動的に関連付けたい」への対応。見出しの文言が
    # 違っても、既存のtheme判定(theme_ids)が同じなら束ねる。
    def test_build_theme_clusters_groups_news_sharing_a_theme_id(self):
        rules = impact_mod.load()
        news = [
            {"id": "n1", "title": "日銀が追加利上げを決定", "url": "u1", "importance": 5, "source": "A",
             "theme_ids": ["boj_hike"],
             "impacts": [{"code": "8306", "name": "三菱UFJ", "direction": "positive", "theme_id": "boj_hike"}]},
            {"id": "n2", "title": "日銀、利上げ観測強まる 市場は身構え", "url": "u2", "importance": 3, "source": "B",
             "theme_ids": ["boj_hike"],
             "impacts": [{"code": "8411", "name": "みずほ", "direction": "positive", "theme_id": "boj_hike"}]},
            {"id": "n3", "title": "無関係な話題", "url": "u3", "importance": 1, "source": "C",
             "theme_ids": [], "impacts": []},
        ]
        clusters = analyze.build_theme_clusters(news, rules)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["theme_id"], "boj_hike")
        self.assertEqual(len(clusters[0]["members"]), 2)
        self.assertNotIn("n3", clusters[0]["member_ids"])
        codes = {s["code"] for s in clusters[0]["stocks"]}
        self.assertEqual(codes, {"8306", "8411"})

    def test_build_theme_clusters_ignores_themes_with_only_one_member(self):
        # 単独ニュースを「材料」として見せても情報過多になるだけなので、
        # 同じテーマが2件以上そろって初めてクラスターとして出す。
        rules = impact_mod.load()
        news = [{"id": "n1", "title": "t", "url": "u1", "importance": 1, "source": "A",
                 "theme_ids": ["boj_hike"], "impacts": []}]
        self.assertEqual(analyze.build_theme_clusters(news, rules), [])

    def test_build_theme_clusters_allows_one_news_item_in_multiple_clusters(self):
        # ユーザー要望(2026-09-20)「一つのニュースが複数のテーマに影響する
        # 場合にも柔軟に対応してほしい」。Union-Findのような相互排他な
        # 統合はせず、1件のニュースが複数クラスターに重複して入ることを
        # 固定する(実測: レアアース輸出規制の見出しはsemi_regulationと
        # rare_earthの両方にヒットする。ALLOWED_KEYWORD_OVERLAPS参照)。
        rules = impact_mod.load()
        shared = {"id": "n1", "title": "半導体規制とレアアース規制が同時発表", "url": "u1", "importance": 5, "source": "A",
                  "theme_ids": ["semi_regulation", "rare_earth"], "impacts": []}
        other_semi = {"id": "n2", "title": "対中規制強化", "url": "u2", "importance": 3, "source": "B",
                      "theme_ids": ["semi_regulation"], "impacts": []}
        other_rare = {"id": "n3", "title": "レアアース確保策", "url": "u3", "importance": 3, "source": "C",
                      "theme_ids": ["rare_earth"], "impacts": []}
        clusters = analyze.build_theme_clusters([shared, other_semi, other_rare], rules)
        theme_ids = {c["theme_id"] for c in clusters}
        self.assertEqual(theme_ids, {"semi_regulation", "rare_earth"})
        for c in clusters:
            self.assertIn("n1", c["member_ids"], f"{c['theme_id']}クラスターにn1が重複所属していません")

    # build_material_clusters: ユーザー要望(2026-09-20)「単純なテーマ一致
    # だけでは見つけられないニュース同士の実質的なつながりも発見したい」
    # への対応。テーマが違っても、企業・業界・資源/サプライチェーンタグが
    # 重なっていれば実質的につながっているとみなす。
    def test_build_material_clusters_finds_company_connection_across_different_themes(self):
        # 4063信越化学工業は、テーマの異なる2件のニュースに登場する
        # (政策系ニュースの間接影響と、レアアース規制の直接影響)。
        # テーマが違うのでbuild_theme_clustersでは束ねられないが、
        # 同じ企業に複数方向から影響しているという実質的なつながりがある。
        master = stocks_mod.load()
        rules = impact_mod.load()
        news = [
            {"id": "n1", "title": "日銀が追加利上げを決定", "url": "u1", "importance": 5, "source": "A",
             "theme_ids": ["boj_hike"],
             "impacts": [{"code": "4063", "name": "信越化学工業", "sector": "化学", "direction": "watch", "theme_id": "boj_hike"}]},
            {"id": "n2", "title": "中国がレアアース輸出を規制", "url": "u2", "importance": 4, "source": "B",
             "theme_ids": ["rare_earth"],
             "impacts": [{"code": "4063", "name": "信越化学工業", "sector": "化学", "direction": "negative", "theme_id": "rare_earth"}]},
        ]
        clusters = analyze.build_material_clusters(news, rules, master)
        company = [c for c in clusters if c["connection_type"] == "company"]
        self.assertTrue(company, "同じ企業(信越化学工業)によるクラスターが見つかりません")
        self.assertEqual(set(company[0]["member_ids"]), {"n1", "n2"})

    def test_build_material_clusters_finds_sector_connection_across_different_companies(self):
        # 4063信越化学工業(化学)と4005住友化学(化学)は別企業・別テーマだが、
        # 同じ業種(化学)に波及している。
        master = stocks_mod.load()
        rules = impact_mod.load()
        news = [
            {"id": "n1", "title": "t1", "url": "u1", "importance": 3, "source": "A", "theme_ids": [],
             "impacts": [{"code": "4063", "name": "信越化学工業", "sector": "化学", "direction": "watch", "theme_id": None}]},
            {"id": "n2", "title": "t2", "url": "u2", "importance": 3, "source": "B", "theme_ids": [],
             "impacts": [{"code": "4005", "name": "住友化学", "sector": "化学", "direction": "watch", "theme_id": None}]},
        ]
        clusters = analyze.build_material_clusters(news, rules, master)
        sector = [c for c in clusters if c["connection_type"] == "sector"]
        self.assertTrue(sector, "同じ業界(化学)によるクラスターが見つかりません")
        self.assertEqual(set(sector[0]["member_ids"]), {"n1", "n2"})

    def test_build_material_clusters_finds_supply_chain_tag_connection(self):
        # 4063信越化学工業(sector:化学)と3436 SUMCO(sector:金属製品)は
        # 業種は別だが、どちらもstocks.json上「半導体材料」タグを持つ。
        # サプライチェーン上の実質的なつながりとして拾う。
        master = stocks_mod.load()
        rules = impact_mod.load()
        news = [
            {"id": "n1", "title": "t1", "url": "u1", "importance": 3, "source": "A", "theme_ids": [],
             "impacts": [{"code": "4063", "name": "信越化学工業", "sector": "化学", "direction": "watch", "theme_id": None}]},
            {"id": "n2", "title": "t2", "url": "u2", "importance": 3, "source": "B", "theme_ids": [],
             "impacts": [{"code": "3436", "name": "SUMCO", "sector": "金属製品", "direction": "watch", "theme_id": None}]},
        ]
        clusters = analyze.build_material_clusters(news, rules, master)
        supply = [c for c in clusters if c["connection_type"] == "supply_chain" and c["label"].startswith("半導体材料")]
        self.assertTrue(supply, "半導体材料タグによるサプライチェーンのつながりが見つかりません")
        self.assertEqual(set(supply[0]["member_ids"]), {"n1", "n2"})
        sector = [c for c in clusters if c["connection_type"] == "sector"]
        self.assertFalse(sector, "業種が異なる(化学/金属製品)のに業界クラスターが出ています")

    def test_build_material_clusters_excludes_non_substantive_tags(self):
        # 「主力」「円安メリット」は財務特性ラベルであって資源・サプライ
        # チェーン上の実質的なつながりではないため、これだけを理由に
        # クラスターを作らない(NON_SUBSTANTIVE_STOCK_TAGS)。
        master = stocks_mod.load()
        rules = impact_mod.load()
        # 7203トヨタ自動車と4063信越化学工業はどちらも「主力」タグを
        # 持つが、業種・他タグに共通点は無い。
        news = [
            {"id": "n1", "title": "t1", "url": "u1", "importance": 3, "source": "A", "theme_ids": [],
             "impacts": [{"code": "7203", "name": "トヨタ自動車", "sector": "自動車", "direction": "watch", "theme_id": None}]},
            {"id": "n2", "title": "t2", "url": "u2", "importance": 3, "source": "B", "theme_ids": [],
             "impacts": [{"code": "4063", "name": "信越化学工業", "sector": "化学", "direction": "watch", "theme_id": None}]},
        ]
        clusters = analyze.build_material_clusters(news, rules, master)
        labels = [c["label"] for c in clusters]
        self.assertFalse(any("主力" in label for label in labels), f"「主力」タグでクラスターが作られています: {labels}")

    def test_build_material_clusters_does_not_duplicate_an_identical_theme_cluster(self):
        # 企業クラスターの構成員集合が既存のテーマクラスターと完全に
        # 同じなら、同じ2件を2回見せることになるため出さない。
        master = stocks_mod.load()
        rules = impact_mod.load()
        news = [
            {"id": "n1", "title": "日銀が追加利上げを決定", "url": "u1", "importance": 5, "source": "A",
             "theme_ids": ["boj_hike"],
             "impacts": [{"code": "8306", "name": "三菱UFJ", "sector": "銀行", "direction": "positive", "theme_id": "boj_hike"}]},
            {"id": "n2", "title": "日銀、利上げ観測強まる", "url": "u2", "importance": 3, "source": "B",
             "theme_ids": ["boj_hike"],
             "impacts": [{"code": "8306", "name": "三菱UFJ", "sector": "銀行", "direction": "positive", "theme_id": "boj_hike"}]},
        ]
        clusters = analyze.build_material_clusters(news, rules, master)
        member_sets = [frozenset(c["member_ids"]) for c in clusters]
        self.assertEqual(
            len(member_sets), len(set(member_sets)),
            "同じ構成員集合のクラスターが重複して出ています",
        )
        self.assertEqual([c["connection_type"] for c in clusters], ["theme"])

    def test_market_snapshot_reads_existing_data_json(self, ):
        import tempfile
        payload = {"nikkei225": {"value": "41,000.00", "change_pct": 1.2, "asof": "15:00"},
                   "fx": {"value": "", "change_pct": None},
                   "us_market": {"sp500": {"value": "6,000", "change_pct": -0.4}}}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(payload, f)
            path = f.name
        rows = analyze.market_snapshot(path)
        labels = [r["label"] for r in rows]
        self.assertIn("日経平均", labels)
        self.assertIn("S&P500", labels)
        self.assertNotIn("ドル円", labels)  # 値が空のものは出さない

    def test_market_snapshot_missing_file(self):
        self.assertEqual(analyze.market_snapshot("/nonexistent/data.json"), [])


class RenderTest(unittest.TestCase):
    def setUp(self):
        from newssite.sample import sample_data
        self.data = sample_data()
        self.html = render.build_html(self.data)

    def test_html_contains_news_and_impacts(self):
        self.assertIn("重要ニュース × 影響銘柄", self.html)
        self.assertIn("日銀、追加利上げを決定", self.html)
        self.assertIn("影響が出うる銘柄", self.html)
        self.assertIn("data-code=\"8306\"", self.html)
        self.assertIn("finance.yahoo.co.jp/quote/8306.T", self.html)

    def test_html_contains_theme_clusters_section(self):
        # ユーザー要望(2026-09-20)「情報同士のつながりが見える情報分析
        # ボードにしてほしい」。sample_dataにはrare_earthテーマで2件の
        # ニュースが重なるよう仕込んである(newssite/sample.py参照)ので、
        # デスクトップ・モバイルどちらにもクラスター名が出ることを固定する。
        self.assertIn("つながっている材料", self.html)
        self.assertIn("レアアース・重要鉱物の輸出規制", self.html)
        self.assertIn("双日", self.html)

    def test_html_emergence_badge_tooltip_discloses_actor_types_and_growth_history(self):
        # ユーザー要望(2026-09-20)「本当に異なる情報源・出来事からテーマ
        # が広がっているのかを正確に把握したい」「なぜテーマが検出された
        # のか説明可能にする」。ツールチップに情報源の種類の内訳・地域・
        # 成長の経過(milestones)が実際に出ることを固定する(ブラック
        # ボックス化しない)。
        self.assertIn("emergence-badge", self.html)
        self.assertIn("独立イベント", self.html)
        self.assertIn("情報源の種類", self.html)
        self.assertIn("成長の経過", self.html)
        self.assertIn("将来重要になると予言するものではなく", self.html)
        # 実測バグ再発防止: analyze.pyのフィールド名(emergence_signal_labels)
        # とrender.pyの参照名(旧: emergence_signals)がズレて、検出シグナル
        # の行が常に空文字になっていた。「検出シグナル: 」の直後に必ず
        # 何か(全角読点区切りのラベル)が来ることを固定する。
        match = re.search(r"検出シグナル: ([^\n]+)", self.html)
        self.assertIsNotNone(match, "検出シグナル行が見つかりません")
        self.assertTrue(match.group(1).strip(), "検出シグナルの中身が空です(analyze.pyとrender.pyのフィールド名不一致の再発防止)")

    def test_html_escapes_dangerous_text(self):
        data = dict(self.data)
        data["news"] = [dict(self.data["news"][0], title='<script>alert(1)</script>', summary='"><img>')]
        html_text = render.build_html(data)
        self.assertNotIn("<script>alert(1)</script>", html_text)
        self.assertIn("&lt;script&gt;", html_text)

    def test_empty_news_renders_placeholder(self):
        data = dict(self.data, news=[], stock_ranking=[], counts={"news": 0, "high_importance": 0, "stocks": 0})
        html_text = render.build_html(data)
        self.assertIn("ニュースを取得できませんでした", html_text)

    def test_stars(self):
        self.assertEqual(render.stars(5), "★★★★★")
        self.assertEqual(render.stars(2), "★★☆☆☆")
        self.assertEqual(render.stars(0), "★☆☆☆☆")


class DevToolTest(unittest.TestCase):
    """開発用コマンド(dev.py)の検査機能。"""

    def _check(self):
        import argparse
        import contextlib
        import dev
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = dev.cmd_check(argparse.Namespace(verbose=False))
        return code, buf.getvalue()

    def test_check_passes_on_current_data(self):
        code, out = self._check()
        self.assertEqual(code, 0, out)
        self.assertIn("整合性チェックOK", out)

    def test_check_detects_unknown_theme_tag(self):
        import dev
        rules = impact_mod.load()
        rules.themes[0]["impacts"][0]["themes"] = ["存在しないタグ"]
        with mock.patch.object(impact_mod, "load", return_value=rules):
            code, out = self._check()
        self.assertEqual(code, 1)
        self.assertIn("存在しないタグ", out)

    def test_check_detects_duplicate_code(self):
        master = stocks_mod.load()
        master.stocks.append(dict(master.stocks[0]))
        with mock.patch.object(stocks_mod, "load", return_value=master):
            code, out = self._check()
        self.assertEqual(code, 1)
        self.assertIn("重複", out)


class CIConfigTest(unittest.TestCase):
    """CI(GitHub Actions)側の設定ファイルの回帰テスト。

    実測バグ(2026-09-20発見): newssite/data/policy_event_registry.json が
    .github/workflows/update.yml のgit reset --hard→退避復元→git addの
    どこにも含まれておらず、CIの実行のたびに空の登録簿へリセットされて
    いたため、「続報(UPDATE)」ライフサイクル判定が本番で一度も機能して
    いなかった。theme_trend_registry.json(萌芽シグナル)も同じ永続化が
    必要なため、両方が退避・復元・git addの対象に含まれることを固定する。
    """

    def _workflow_text(self):
        path = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "update.yml"
        return path.read_text(encoding="utf-8")

    def test_registries_are_stashed_across_git_reset_hard(self):
        text = self._workflow_text()
        for name in ("policy_event_registry.json", "theme_trend_registry.json"):
            self.assertIn(
                f"newssite/data/{name}", text,
                f"{name} が update.yml のどこにも登場しません(退避・復元されず消える再発防止)",
            )

    def test_registries_are_included_in_git_add(self):
        text = self._workflow_text()
        add_block = text[text.index("git add data.json"):]
        add_block = add_block[:add_block.index("\n\n")]
        for name in ("policy_event_registry.json", "theme_trend_registry.json"):
            self.assertIn(
                name, add_block,
                f"{name} がgit addに含まれていません(コミットされず永続化しない再発防止)",
            )


class ThemeTrendsTest(unittest.TestCase):
    """theme_trends.py: 萌芽シグナル検知の回帰テスト。

    ユーザー要望(2026-09-20)「『ビッグニュースになる可能性が高い』を最初から
    予言させる設計にはしない方がいい。代わりに『先行シグナルが何個
    集まっているか』を検出する」に基づく設計を固定する。
    """

    def setUp(self):
        self.rules = impact_mod.load()

    def _item(self, **kw):
        base = {
            "id": "n1", "title": "t", "source": "テスト媒体1", "theme_ids": ["boj_hike"], "impacts": [],
            "related": [], "source_tier": None, "future_signal": False,
            "policy_maturity": None, "category": "policy",
        }
        base.update(kw)
        return base

    def test_detect_signals_reads_only_already_computed_fields(self):
        item = self._item(
            source_tier="primary", future_signal=True,
            impacts=[{"origin": "direct"}], related=[{"title": "x"}],
        )
        signals = theme_trends.detect_signals(item, self.rules)
        self.assertEqual(
            signals,
            {"gov_source", "gov_policy", "corporate", "multi_source"},
        )

    def test_detect_signals_rd_patent_capex_keywords(self):
        rd = theme_trends.detect_signals(self._item(title="新技術の研究開発が進展"), self.rules)
        self.assertIn("rd", rd)
        patent = theme_trends.detect_signals(self._item(title="新方式で特許を取得"), self.rules)
        self.assertIn("patent", patent)
        capex = theme_trends.detect_signals(self._item(title="新工場建設で増産へ"), self.rules)
        self.assertIn("capex", capex)

    def test_detect_signals_foreign_category(self):
        signals = theme_trends.detect_signals(self._item(category="geopolitics"), self.rules)
        self.assertIn("foreign", signals)
        signals_jp = theme_trends.detect_signals(self._item(category="japan"), self.rules)
        self.assertNotIn("foreign", signals_jp)

    def test_emerging_requires_new_theme_and_at_least_two_signals(self):
        registry = {"themes": {}}
        # 1件目: 新規テーマだがシグナルが1種類・情報源も1つだけ→まだemergingにしない
        news = [self._item(id="n1", title="日銀が利上げ", source="経済産業省", source_tier="primary")]
        stages = theme_trends.record_and_classify(registry, news, self.rules, "2026-09-20")
        self.assertIsNone(stages["boj_hike"]["stage"])

        # 2件目: 別の情報源(双日)からcapexシグナルが加わり、
        # シグナル種別2つ・独立した情報源2つがそろう
        news2 = [self._item(id="n2", title="関連企業が新工場建設で増産へ", source="双日")]
        stages2 = theme_trends.record_and_classify(registry, news2, self.rules, "2026-09-20")
        self.assertEqual(stages2["boj_hike"]["stage"], "emerging")
        self.assertEqual(stages2["boj_hike"]["signal_count"], 2)
        self.assertEqual(stages2["boj_hike"]["source_count"], 2)

    def test_watch_stage_for_old_theme_with_three_or_more_signals_from_independent_sources(self):
        # 初出から日数が経ちすぎている(EMERGING_MAX_DAYSを超える)テーマは
        # emergingにはしないが、シグナルが3種類以上・かつ独立した
        # (別の情報源による)裏付けが2件以上そろえばwatchにする。
        registry = {
            "themes": {
                "boj_hike": {"first_seen": "2026-01-01", "occurrences": 1, "events": []},
            }
        }
        news = [
            self._item(id="n1", title="関連企業が新工場建設で増産へ", source="経済産業省", source_tier="primary"),
            self._item(id="n2", title="日銀の動向を注視", source="双日", future_signal=True, impacts=[{"origin": "direct"}]),
        ]
        stages = theme_trends.record_and_classify(registry, news, self.rules, "2026-09-20")
        self.assertEqual(stages["boj_hike"]["stage"], "watch")
        self.assertGreaterEqual(stages["boj_hike"]["signal_count"], 3)
        self.assertEqual(stages["boj_hike"]["event_count"], 2)
        self.assertEqual(stages["boj_hike"]["source_count"], 2)

    def test_same_source_reporting_twice_does_not_count_as_independent(self):
        # ②独立性の核心: 記事(item_id)が異なっても、情報源(source)が
        # 同じなら(例: 同じ省庁が2回発表)、独立した裏付けとしては1件
        # にしかならない。記事数と独立した情報源の数を混同しない。
        registry = {"themes": {"boj_hike": {"first_seen": "2026-01-01", "occurrences": 1, "events": []}}}
        news = [
            self._item(id="n1", title="関連企業が新工場建設で増産へ", source="経済産業省", source_tier="primary"),
            self._item(id="n2", title="経産省、追加の検討を表明", source="経済産業省",
                       future_signal=True, source_tier="primary"),
        ]
        stages = theme_trends.record_and_classify(registry, news, self.rules, "2026-09-20")
        self.assertEqual(stages["boj_hike"]["event_count"], 2, "記事自体は2件のはず")
        self.assertEqual(stages["boj_hike"]["source_count"], 1, "情報源は同じ省庁1つのはず")
        self.assertIsNone(
            stages["boj_hike"]["stage"],
            "情報源が同じ(経済産業省)なのに独立した複数シグナルとしてwatch/emergingになっています",
        )

    def test_single_article_with_many_signal_words_does_not_count_as_independent(self):
        # ②独立性: 1本の記事が政府一次情報・研究開発・特許・設備投資・
        # 関連企業発表を全部満たしていても、裏付けは1件(item_id1つ)しか
        # 無いので、シグナル種別が3つ以上そろっていてもwatchにはしない。
        registry = {"themes": {"boj_hike": {"first_seen": "2026-01-01", "occurrences": 1, "events": []}}}
        news = [self._item(
            id="n1", title="関連企業が研究開発と特許取得、新工場建設で増産へ",
            source_tier="primary", impacts=[{"origin": "direct"}],
        )]
        stages = theme_trends.record_and_classify(registry, news, self.rules, "2026-09-20")
        self.assertGreaterEqual(stages["boj_hike"]["signal_count"], 3, "シグナル種別自体は複数検出されているはず")
        self.assertEqual(stages["boj_hike"]["event_count"], 1)
        self.assertIsNone(
            stages["boj_hike"]["stage"],
            "1本の記事だけなのに、複数の独立したシグナルが集まったかのようにwatchになっています",
        )

    def test_window_counts_reflect_recent_concentration(self):
        # ③加速: 直近7/30/90日でそれぞれ独立イベント数を分けて出す
        # (単一の伸び率スコアには合成しない)。
        registry = {"themes": {"boj_hike": {"first_seen": "2026-08-01", "occurrences": 1, "events": [
            {"date": "2026-08-05", "item_id": "old1", "source": "経済産業省",
             "signals": ["gov_source"], "actor_types": ["government_jp"], "regions": [], "origin_region": "日本"},
        ]}}}
        news = [self._item(id="new1", title="関連企業が新工場建設で増産へ", source_tier="primary")]
        stages = theme_trends.record_and_classify(registry, news, self.rules, "2026-09-20")
        wc = stages["boj_hike"]["window_counts"]
        self.assertEqual(wc[7], 1)  # 直近7日はnew1のみ
        self.assertEqual(wc[90], 2)  # 90日以内はold1+new1

    def test_regions_track_international_spread(self):
        # ④国際的な広がり: 見出しに含まれる国・地域名から、何か国・地域
        # からシグナルが上がっているかを数える。
        registry = {"themes": {"boj_hike": {"first_seen": "2026-09-20", "occurrences": 1, "events": []}}}
        news = [
            self._item(id="n1", title="経済産業省が方針を検討", source_tier="primary"),
            self._item(id="n2", title="米商務省が新たな輸出規制を発表", source_tier="primary"),
        ]
        stages = theme_trends.record_and_classify(registry, news, self.rules, "2026-09-20")
        self.assertEqual(set(stages["boj_hike"]["regions"]), {"日本", "米国"})
        self.assertEqual(stages["boj_hike"]["region_count"], 2)

    def test_actor_types_expose_which_kind_of_source_not_just_a_count(self):
        # ②'情報源の種類の可視化: 「メディア5件」なのか「政府+企業+研究
        # 機関+海外+メディア」なのかで意味が違う、という指摘への対応。
        # 件数だけでなく、どの種類の主体から発生したかを開示する。
        registry = {"themes": {"boj_hike": {"first_seen": "2026-09-20", "occurrences": 1, "events": []}}}
        news = [
            self._item(id="n1", title="経済産業省が検討", source="経済産業省", source_tier="primary"),
            self._item(id="n2", title="米商務省が発表", source="ロイター"),
            self._item(id="n3", title="関連企業が新工場建設で増産へ", source="双日", impacts=[{"origin": "direct"}]),
            self._item(id="n4", title="東京大学が研究成果を発表", source="日本経済新聞"),
            self._item(id="n5", title="IAEAが声明を発表", source="共同通信"),
        ]
        stages = theme_trends.record_and_classify(registry, news, self.rules, "2026-09-20")
        info = stages["boj_hike"]
        self.assertEqual(
            set(info["actor_type_labels"]),
            {"日本政府・省庁", "海外政府・当局", "関連企業", "研究機関・大学", "国際機関"},
        )

    def test_origin_region_confident_for_named_agency_or_primary_source_otherwise_unknown(self):
        # ④地域情報(発信元): 機関名が見出しに明示されている場合、または
        # GOV_FEED経由(source_tier=primary)の場合だけ発信元を確定する。
        # 単なる国名の言及だけでは、その国が主体か対象か判別できないため
        # 「不明」のままにする(推測で埋めない)。
        jp_gov = self._item(title="経済産業省が方針を決定", source_tier="primary")
        self.assertEqual(theme_trends.detect_origin_region(jp_gov), "日本")

        us_gov = self._item(title="米商務省が新たな輸出規制を発表")
        self.assertEqual(theme_trends.detect_origin_region(us_gov), "米国")

        ambiguous = self._item(title="台湾情勢を巡り中国が反発")  # 主体か対象か不明
        self.assertIsNone(theme_trends.detect_origin_region(ambiguous))

    def test_milestones_persist_after_events_decay(self):
        # ⑤成長履歴: milestonesは、対応するeventがSIGNAL_WINDOW_DAYSの
        # 経過でeventsから間引かれた後も、削除・上書きされずに残り続ける
        # (「最初に検知した時点」を後から検証できるようにするため)。
        old_date = theme_trends._shift_date("2026-09-20", -(theme_trends.SIGNAL_WINDOW_DAYS + 5))
        registry = {
            "themes": {
                "boj_hike": {
                    "first_seen": old_date,
                    "occurrences": 1,
                    "events": [{
                        "date": old_date, "item_id": "old1", "source": "経済産業省",
                        "signals": ["gov_source"], "actor_types": ["government_jp"],
                        "regions": [], "origin_region": "日本",
                    }],
                    "milestones": {"government_jp": old_date},
                },
            }
        }
        news = [self._item(id="n1", title="日銀が追加利上げを検討", source="テスト媒体2", future_signal=True)]
        stages = theme_trends.record_and_classify(registry, news, self.rules, "2026-09-20")
        self.assertEqual(stages["boj_hike"]["event_count"], 1, "古いeventは間引かれ、今回の1件だけが残るはず")
        milestone_types = {m["actor_type_label"] for m in stages["boj_hike"]["milestones"]}
        self.assertIn(
            "日本政府・省庁", milestone_types,
            "eventsから間引かれた後もmilestones(成長履歴)には残り続けるべきです",
        )

    def test_milestones_record_new_actor_type_the_first_time_it_appears(self):
        registry = {"themes": {"boj_hike": {"first_seen": "2026-09-01", "occurrences": 1, "events": []}}}
        news1 = [self._item(id="n1", title="経済産業省が検討", source="経済産業省", source_tier="primary")]
        theme_trends.record_and_classify(registry, news1, self.rules, "2026-09-01")

        news2 = [self._item(id="n2", title="関連企業が新工場建設で増産へ", source="双日",
                             impacts=[{"origin": "direct"}])]
        stages = theme_trends.record_and_classify(registry, news2, self.rules, "2026-09-10")
        milestones = {m["actor_type_label"]: m["date"] for m in stages["boj_hike"]["milestones"]}
        self.assertEqual(milestones.get("日本政府・省庁"), "2026-09-01")
        self.assertEqual(milestones.get("関連企業"), "2026-09-10")

    def test_research_and_international_org_signals(self):
        research = self._item(title="東京大学の研究グループが新技術を開発")
        self.assertIn("research", theme_trends.detect_actor_types(research, self.rules))
        intl = self._item(title="IAEAが新たな安全基準を発表")
        self.assertIn("international_org", theme_trends.detect_actor_types(intl, self.rules))

    def test_first_seen_is_never_overwritten(self):
        # ⑤前段階の保存: 一度記録したfirst_seenは、その後何度呼んでも
        # 上書きしない(後から「何日前に検知していたか」を検証できるように)。
        registry = {"themes": {"boj_hike": {"first_seen": "2026-08-01", "occurrences": 3, "events": []}}}
        news = [self._item(id="n1", title="日銀が動向を注視")]
        stages = theme_trends.record_and_classify(registry, news, self.rules, "2026-09-20")
        self.assertEqual(stages["boj_hike"]["first_seen"], "2026-08-01")

    def test_signals_decay_after_window_without_reinforcement(self):
        # SIGNAL_WINDOW_DAYSを超えて再確認されなかったシグナルは、
        # 「今も生きている根拠」から外れる(過去1回きりの言及で永久に
        # 要監視のままになることを防ぐ)。
        old_date = theme_trends._shift_date("2026-09-20", -(theme_trends.SIGNAL_WINDOW_DAYS + 5))
        registry = {
            "themes": {
                "boj_hike": {
                    "first_seen": "2026-01-01",
                    "occurrences": 5,
                    "events": [
                        {"date": old_date, "item_id": "old1", "source": "経済産業省",
                         "signals": ["gov_source", "gov_policy"], "actor_types": ["government_jp"],
                         "regions": [], "origin_region": "日本"},
                        {"date": old_date, "item_id": "old2", "source": "双日",
                         "signals": ["capex"], "actor_types": ["corporate"], "regions": [], "origin_region": None},
                    ],
                },
            }
        }
        news = [self._item(id="n1", title="日銀が動向を注視", source_tier=None)]
        stages = theme_trends.record_and_classify(registry, news, self.rules, "2026-09-20")
        self.assertEqual(stages["boj_hike"]["signal_count"], 0)
        self.assertIsNone(stages["boj_hike"]["stage"])

    def test_best_stage_for_theme_ids_prefers_emerging_over_watch(self):
        info = {
            "a": {"stage": "watch", "stage_label": "👀 要監視テーマ", "signal_count": 3, "signal_labels": []},
            "b": {"stage": "emerging", "stage_label": "🔎 新興テーマ", "signal_count": 2, "signal_labels": []},
        }
        best = theme_trends.best_stage_for_theme_ids(["a", "b"], info)
        self.assertEqual(best["stage"], "emerging")


if __name__ == "__main__":
    unittest.main()
