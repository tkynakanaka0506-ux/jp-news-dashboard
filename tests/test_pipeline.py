#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ニュースサイト生成の主要ロジックのテスト。

  python3 -m unittest discover -s tests -v

ネットワークには接続せず、RSS取得部分はローカルのXML文字列に差し替えて検証する。
"""
import io
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from newssite import analyze, impact as impact_mod, render, rss, stocks as stocks_mod  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
