#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rules.json 全体を横断監査する回帰テスト。

このセッションで実際に踏んだ3種類のバグを、二度と黙って再発させないための
機械チェック。個別テーマの疑似見出しテストとは別に、31テーマ全体を横断で
スキャンする。新しいテーマを追加したら python3 -m unittest discover -s tests
を実行するだけで、ここのチェックにも自動的にかかる。

見つかったバグの実例:
  1. defense_budget の造船(間接)ブロックが limit=3 のせいで、防衛タグと
     重複する銘柄で埋まり、本当に見せたい新規の間接銘柄(三井E&S・名村造船所)
     が一度も表示されなかった。
  2. semi_demand の「半導体」が semi_policy の「先端半導体」を、
     defense_budget の「安全保障」が semi_policy の「経済安全保障」を
     それぞれ意図せず誤発火させた。
  3. ukraine_russia が資源・防衛への影響を direction="positive" 固定にして
     いたが、「停戦」のような沈静化ニュースでは方向が逆になるはずだった。
"""
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from newssite import impact as impact_mod, stocks as stocks_mod  # noqa: E402

RULES_PATH = Path(__file__).resolve().parent.parent / "newssite" / "data" / "rules.json"


def _load_raw():
    with open(RULES_PATH, encoding="utf-8") as f:
        return json.load(f)


# --- チェック2(キーワード包含)で許容する既知の組み合わせ ---
# ここに無いのに部分文字列の包含関係が見つかったら、新規テーマ追加による
# 想定外の誤発火の可能性が高いのでテストを失敗させる。安全と確認できたら
# ここに一行追記してコメントで理由を書くこと。
ALLOWED_KEYWORD_OVERLAPS = {
    # (テーマA.keyword, テーマB.keyword): 許容する理由
    ("輸出規制", "レアメタル 輸出規制"): (
        "semi_regulation(negative固定: 半導体製造装置)/rare_earth(negative固定: レアアース"
        "由来部材)。レアメタルの輸出規制は半導体製造にも使われる材料を含みうるため、"
        "両テーマが同時に反応するのは意味が矛盾しない正当な多重ヒット"
    ),
    ("輸出規制", "重要鉱物 輸出規制"): (
        "semi_regulation/rare_earth。上と同じ理由(重要鉱物の輸出規制は半導体材料にも"
        "関わりうる)で多重ヒットを許容する"
    ),
    ("利下げ", "FRB 利下げ"): (
        "rate_cut(positive固定: グロース/不動産)/fed_dovish(positive固定: グロース/半導体・AI、"
        "第9優先改修③STEP1で新設)。fed_dovishはFRBの利下げ観測に特化した専用テーマだが、"
        "「FRBが利下げ」という記事は一般的な利下げ・金融緩和の話でもあるため、rate_cutと"
        "同時にヒットするのは方向(どちらもpositive)が矛盾しない正当な多重ヒット"
    ),
    ("利下げ", "FOMC 利下げ"): "rate_cut/fed_dovish。上と同じ理由(FOMCの利下げも同様)",
    ("利下げ", "米利下げ"): "rate_cut/fed_dovish。上と同じ理由(「米利下げ」表記でも同様)",
    ("ハト派", "パウエル議長 ハト派"): (
        "rate_cut/fed_dovish。パウエル議長のハト派発言は一般的なハト派材料でもあるため、"
        "同時ヒットは方向が矛盾しない正当な多重ヒット"
    ),
    ("データセンター", "データセンター 建設"): "semi_demand/power_demand。別カテゴリの正当な多重ヒットとして確認済み",
    ("データセンター", "データセンター整備"): "semi_demand/ai_policy。別カテゴリの正当な多重ヒットとして確認済み",
    ("半導体", "半導体工場"): (
        "semi_demand/semi_policy。TSMC等の新工場稼働は需要材料としても妥当なため、"
        "他の政策系複合語(国内生産/サプライチェーン/人材育成等)と違いexcludeしない"
    ),
    ("データセンター", "データセンター投資 拡大"): (
        "semi_demand(positive固定)/ai_capex_cycle(watch)。どちらも増額・拡大方向で"
        "意味が一致する多重ヒットなので方向は矛盾しない(減額・延期系はsemi_demand側の"
        "excludeで既に住み分け済み)"
    ),
    ("データセンター", "データセンター投資 増額"): (
        "semi_demand(positive固定)/ai_capex_cycle(watch)。どちらも増額方向で"
        "意味が一致する多重ヒットなので方向は矛盾しない"
    ),
    ("データセンター", "データセンター 建設加速"): (
        "semi_demand(positive固定)/ai_capex_cycle(watch)。「建設加速」は増額方向で"
        "意味が一致するため方向は矛盾しない"
    ),
    ("データセンター", "データセンター 新設稼働"): (
        "semi_demand(positive固定)/ai_capex_cycle(watch)。「新設稼働」は増額方向で"
        "意味が一致するため方向は矛盾しない"
    ),
    ("設備投資 増額", "AI設備投資 増額"): (
        "semi_demand(positive固定)/ai_capex_cycle(watch)。どちらも「増額」で方向が"
        "一致する近縁ワードの多重ヒット"
    ),
    ("データセンター 建設", "データセンター 建設加速"): (
        "power_demand(watch)/ai_capex_cycle(watch)。どちらも建設・拡大方向で"
        "意味が一致するため方向は矛盾しない"
    ),
}

# --- チェック3(自己矛盾する方向固定)で許容する既知のテーマ ---
# 一見「テーマのkeywordsにpositive/negative両方の語彙が混在」に見えるが、
# 実際には対になる別テーマ(oil_up等)とexcludeで正しく住み分けられている、
# または語彙が偶然別の文脈の単語と一致しているだけで方向固定は正しいもの。
ALLOWED_DIRECTION_LOCKED_THEMES = {
    "oil_down": (
        "「OPEC 増産」の「増産」は原油の生産量増加(=価格下落要因)の意味で、"
        "企業業績の増産(positive_words)とは文脈が異なる。"
        "oil_up側とexclude(原油高/原油安)で正しく住み分け済み"
    ),
}


class RuleAuditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = _load_raw()
        cls.themes = cls.raw["themes"]
        cls.master = stocks_mod.load()
        cls.rules = impact_mod.load()

    # --- バグ1: limit が dedup より先に効いて新規の間接銘柄が消える ---
    def test_no_indirect_impact_is_fully_starved_by_limit(self):
        problems = []
        for theme in self.themes:
            impacts = theme.get("impacts", [])
            if len(impacts) < 2:
                continue
            seen_codes = set()
            for idx, rule in enumerate(impacts):
                tags = rule.get("themes", [])
                seen_codes.update(rule.get("codes", []))
                if not tags:
                    continue
                limit = rule.get("limit", 4)
                full_pool = self.master.by_themes(tags, limit=999)
                limited_pool = self.master.by_themes(tags, limit=limit)
                full_new = [s for s in full_pool if s["code"] not in seen_codes]
                limited_new = [s for s in limited_pool if s["code"] not in seen_codes]
                if full_new and not limited_new:
                    problems.append(
                        f"{theme['id']} impact#{idx} tags={tags} limit={limit}: "
                        f"新規{len(full_new)}社存在するのにlimit内は0件"
                        f"(例: {[s['name'] for s in full_new[:3]]})"
                    )
                seen_codes.update(s["code"] for s in limited_pool)
        self.assertEqual([], problems, "\n" + "\n".join(problems))

    # --- バグ2: 新テーマのキーワードが既存テーマの部分文字列になって誤発火する ---
    # keywords(強)とkeywords_medium(中)はどちらも単独でテーマを成立させるので
    # 両方スキャン対象にする。keywords_weakは単独では成立しないためスキャン対象外。
    def test_keyword_substring_overlaps_are_reviewed(self):
        problems = []
        for i, a in enumerate(self.themes):
            for b in self.themes[i + 1:]:
                kws_a = a.get("keywords", []) + a.get("keywords_medium", [])
                kws_b = b.get("keywords", []) + b.get("keywords_medium", [])
                for kw_a in kws_a:
                    for kw_b in kws_b:
                        if kw_a == kw_b:
                            continue
                        short, long_ = (kw_a, kw_b) if len(kw_a) < len(kw_b) else (kw_b, kw_a)
                        if short not in long_ or len(short) < 2:
                            continue
                        owner = a if short in kws_a else b
                        other_kw = kw_b if owner is a else kw_a
                        guarded = any(
                            ex in other_kw or other_kw in ex for ex in owner.get("exclude", [])
                        )
                        if guarded:
                            continue
                        pair = (short, long_)
                        if pair in ALLOWED_KEYWORD_OVERLAPS or (long_, short) in ALLOWED_KEYWORD_OVERLAPS:
                            continue
                        problems.append(
                            f"{a['id']}.'{kw_a}' <-> {b['id']}.'{kw_b}' が未レビューの包含関係"
                            f"(誤発火するなら exclude を追加、意図的なら"
                            f" ALLOWED_KEYWORD_OVERLAPS に追記して理由を書く)"
                        )
        self.assertEqual([], problems, "\n" + "\n".join(problems))

    # --- バグ3: テーマ自身のキーワードが逆方向の語彙を含むのに direction が固定 ---
    def test_no_direction_locked_theme_with_self_contradicting_keyword(self):
        positive_words = set(self.raw.get("positive_words", []))
        negative_words = set(self.raw.get("negative_words", []))
        problems = []
        for theme in self.themes:
            kw_text = " ".join(theme["keywords"] + theme.get("keywords_medium", []))
            has_positive_kw = any(w in kw_text for w in positive_words)
            has_negative_kw = any(w in kw_text for w in negative_words)
            if not (has_positive_kw and has_negative_kw):
                continue
            if theme["id"] in ALLOWED_DIRECTION_LOCKED_THEMES:
                continue
            for idx, rule in enumerate(theme.get("impacts", [])):
                if rule.get("direction") in ("positive", "negative"):
                    problems.append(
                        f"{theme['id']} impact#{idx}: テーマ自身のkeywordsに"
                        f"positive/negative両方の語彙が含まれるのに"
                        f"direction=\"{rule['direction']}\" 固定になっている"
                        f"(watchにしてheadline_sentimentに判定を委ねるべき)"
                    )
        self.assertEqual([], problems, "\n" + "\n".join(problems))

    # --- Phase4: 複数テーマ一致で重要度スコアが水増しされないこと ---
    # 「半導体+AI+電力+データセンター」のように広いニュースが複数テーマに
    # 一致しても、重要度への寄与は sum(weight) ではなく max(weight) であるべき
    # (「テーマ数が多い=重要」という誤評価を避けるため)。
    def test_multi_theme_match_does_not_inflate_importance(self):
        title = "脱炭素に向けた原発再稼働で電力需要が拡大"
        themes = impact_mod.match_themes(title, self.rules)
        self.assertGreaterEqual(
            len(themes), 2, "このテストは2テーマ以上一致する見出しが前提(rules.json変更で一致しなくなった)"
        )
        weights = [t.get("weight", 1) for t in themes]
        stars_multi, _ = impact_mod.score_importance(
            {"title": title, "feed_weight": 1}, themes, self.rules
        )
        top_theme = max(themes, key=lambda t: t.get("weight", 1))
        stars_single, _ = impact_mod.score_importance(
            {"title": title, "feed_weight": 1}, [top_theme], self.rules
        )
        self.assertEqual(
            stars_multi, stars_single,
            f"複数テーマ一致({weights})の重要度が単独最大テーマ一致と一致しない"
            f"(sum型のボーナスが紛れ込んでいる可能性)"
        )


    # --- Phase6: Policy Impact Score が政策実現度に対して単調であること ---
    def test_policy_impact_score_increases_with_maturity(self):
        entry_direct = {"beneficiary_tier": "direct", "revenue_horizon": "3-6m", "strength": "中"}
        low = impact_mod.compute_policy_impact_score(entry_direct, policy_maturity=20)
        high = impact_mod.compute_policy_impact_score(entry_direct, policy_maturity=100)
        self.assertLess(
            low, high,
            "同じtier/horizon/strengthでも、政策実現度が高いほどスコアが高くなるべき"
        )

    def test_policy_impact_score_is_display_only(self):
        # [レイヤー分離の確認] policy_impact_score を計算しても、
        # 元のimpact_entryのdirection/theme/originは書き換わらない
        entry = {
            "code": "1234", "direction": "positive", "theme": "テスト", "origin": "rule",
            "beneficiary_tier": "direct", "revenue_horizon": "0-3m", "strength": "大",
        }
        before = dict(entry)
        impact_mod.compute_policy_impact_score(entry, policy_maturity=50)
        self.assertEqual(before, entry, "スコア計算がFACTUAL LAYERのフィールドを書き換えている")

    # --- バグ4: 「1811円安」のように数値+通貨/指数語が裸のキーワードに誤反応する ---
    # (バックテスト実データ検証で発見。yen_weak/yen_strong で実際に踏んだ)
    RISKY_BARE_WORDS = {"急騰", "急落", "高値", "安値", "上昇", "下落", "反発", "反落", "円安", "円高"}

    def test_no_unguarded_numeric_ambiguous_keyword(self):
        problems = []
        for theme in self.themes:
            for kw in theme.get("keywords", []) + theme.get("keywords_medium", []):
                if " " in kw or kw not in self.RISKY_BARE_WORDS:
                    continue
                # 裸のリスク語が使われている場合、数字+その語を除外する
                # exclude_regex(例: r"\d+円安")を持っているか確認する
                has_guard = any(
                    re.search(r"\\d", pat) and kw in pat
                    for pat in theme.get("exclude_regex", [])
                )
                if not has_guard:
                    problems.append(
                        f"{theme['id']}.'{kw}': 数字+この語(例:「1811{kw}」)が指数の値幅表現である"
                        f"可能性があるのに、exclude_regexで数字プレフィックスを除外していない"
                    )
        self.assertEqual([], problems, "\n" + "\n".join(problems))

    def test_exclude_regex_patterns_compile(self):
        problems = []
        for theme in self.themes:
            for pat in theme.get("exclude_regex", []):
                try:
                    re.compile(pat)
                except re.error as e:
                    problems.append(f"{theme['id']}: exclude_regex '{pat}' がコンパイルできない({e})")
        self.assertEqual([], problems, "\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
