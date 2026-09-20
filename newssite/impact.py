#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""見出しテキストから「重要度・カテゴリ・影響銘柄」を判定するルールエンジン。

LLM を使わずここだけで完結して動くのが前提(APIキーが無くてもサイトは成立する)。
判定の中身を変えたいときは data/rules.json を編集する。
"""
import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
RULES_PATH = DATA_DIR / "rules.json"

DIRECTION_LABEL = {
    "positive": "追い風",
    "negative": "逆風",
    "watch": "注目",
}

STRENGTH_ORDER = {"大": 3, "中": 2, "小": 1}


class Rules:
    def __init__(self, raw):
        self.raw = raw
        self.themes = raw.get("themes", [])
        self.categories = raw.get("categories", [])
        self.category_label = {c["id"]: c["label"] for c in self.categories}
        self.category_emoji = {c["id"]: c.get("emoji", "📰") for c in self.categories}
        self.importance_keywords = raw.get("importance_keywords", [])
        self.noise_keywords = raw.get("noise_keywords", [])
        self.positive_words = raw.get("positive_words", [])
        self.negative_words = raw.get("negative_words", [])
        self.future_signal_keywords = raw.get("future_signal_keywords", [])
        self.policy_maturity_stages = raw.get("policy_maturity_stages", [])
        # 萌芽シグナル検知(theme_trends.py)用。テーマに依存しない汎用キーワード。
        self.rd_signal_keywords = raw.get("rd_signal_keywords", [])
        self.patent_signal_keywords = raw.get("patent_signal_keywords", [])
        self.capex_signal_keywords = raw.get("capex_signal_keywords", [])
        self.research_signal_keywords = raw.get("research_signal_keywords", [])
        self.international_org_signal_keywords = raw.get("international_org_signal_keywords", [])
        # 「政府・政策が市場を動かす力」(government_policy、既定値)と
        # 「巨大テックの設備投資が需要を動かす力」(corporate_capex)は別物、
        # というユーザー方針(2026-09-13)。テーマ側にintelligence_layerが
        # 無ければ従来通りgovernment_policy(政策)として扱う(後方互換)。
        self.theme_layer = {
            t["id"]: t.get("intelligence_layer", "government_policy") for t in self.themes
        }


def load(path=RULES_PATH):
    with open(path, encoding="utf-8") as f:
        return Rules(json.load(f))


def _keyword_hit(text, keyword):
    """キーワードは半角スペース区切りで AND 条件として扱う。"""
    parts = [p for p in keyword.split(" ") if p]
    return all(p in text for p in parts)


def match_themes(text, rules):
    """見出しに該当するテーマ定義を返す。

    キーワードは3段階の強度を持つ([FACTUAL LAYER]。テーマ成立の可否そのもの
    に関わるので、weakの扱いだけは絶対に緩めないこと):
      keywords(強一致)      … 単独でテーマ成立
      keywords_medium(中一致) … 単独でテーマ成立するが、強一致より確信度は低い
      keywords_weak(弱一致)   … 単独では絶対にテーマを成立させない
                                (「安全保障」のようにテーマに関係はあるが
                                広すぎる語を置く場所。WPS記事が防衛予算テーマに
                                誤って一致した実例があったため)。

    戻り値の各テーマ dict には、実際に一致した強/中キーワードと、その強度
    (strong/medium)を _matched_keyword / _matched_tier として付与する
    (理由文や将来のスコアリングで「何に・どの確信度で反応したか」を
    使うため)。
    """
    hits = []
    for theme in rules.themes:
        if any(_keyword_hit(text, ex) for ex in theme.get("exclude", [])):
            continue
        # exclude_regex: 単純な部分一致では表現できない除外条件用
        # (例:「1811円安」のような指数の下げ幅表現を、通貨の「円安」と
        # 区別するには「数字が直前にある」という文脈判定が要る)。
        if any(re.search(pat, text) for pat in theme.get("exclude_regex", [])):
            continue
        matched_kw = next(
            (kw for kw in theme.get("keywords", []) if _keyword_hit(text, kw)), None
        )
        tier = "strong"
        if matched_kw is None:
            matched_kw = next(
                (kw for kw in theme.get("keywords_medium", []) if _keyword_hit(text, kw)), None
            )
            tier = "medium"
        if matched_kw is None:
            continue
        hit = dict(theme)
        hit["_matched_keyword"] = matched_kw
        hit["_matched_tier"] = tier
        hits.append(hit)
    return hits


def is_noise(text, rules):
    return any(w in text for w in rules.noise_keywords)


def headline_sentiment(text, rules):
    """見出し単体の方向感。戻り値は positive / negative / watch。"""
    pos = sum(1 for w in rules.positive_words if w in text)
    neg = sum(1 for w in rules.negative_words if w in text)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "watch"


def is_future_signal(text, rules):
    """審議会・検討会・パブコメなど、報道になる前の一次情報らしき見出しか。"""
    return any(w in text for w in rules.future_signal_keywords)


# 萌芽シグナル検知(theme_trends.py)用。「このテーマかどうか」ではなく
# 「研究開発/特許/設備投資という角度の動きが見出しにあったか」だけを見る、
# テーマに依存しない汎用判定。[PRESENTATION LAYER] 表示専用で、
# direction/theme/direct-indirectの判定には一切使わない。
def has_rd_signal(text, rules):
    """研究開発の進展を示す見出しか。"""
    return any(w in text for w in rules.rd_signal_keywords)


def has_patent_signal(text, rules):
    """特許に関する見出しか。"""
    return any(w in text for w in rules.patent_signal_keywords)


def has_capex_signal(text, rules):
    """設備投資・増産・新工場など企業の投資判断を示す見出しか。"""
    return any(w in text for w in rules.capex_signal_keywords)


def has_research_signal(text, rules):
    """大学・研究機関が主体の見出しか。"""
    return any(w in text for w in rules.research_signal_keywords)


def has_international_org_signal(text, rules):
    """IAEA・IEA・国連など国際機関が主体の見出しか。"""
    return any(w in text for w in rules.international_org_signal_keywords)


def score_policy_maturity(text, rules):
    """政策が「検討」から「施策実施」までどの段階にあるかを 0-100 で返す。

    [PRESENTATION LAYER] この値は表示・並び替えにのみ使うこと。
    theme/direction/direct-indirect の判定(FACTUAL LAYER)には一切使わない。
    stages は policy_maturity_stages の順(スコアが高い順)に見て、
    最初にヒットした段階を採用する(複数ヒットしたら一番進んだ段階が勝つ)。
    どの段階のキーワードにも一致しなければ (None, None) を返す。
    """
    for stage in rules.policy_maturity_stages:
        hit = next((kw for kw in stage.get("keywords", []) if _keyword_hit(text, kw)), None)
        if hit:
            return stage["score"], stage["label"]
    return None, None


# Policy Impact Score の重み付け(いずれも0〜1に正規化した値に掛ける)。
# 情報が無い(未指定)項目は「わからない」ではなく「中庸」として0.5系の
# 既定値を使う。個別項目が薄くても他が厚ければ極端に沈まないようにするため。
_TIER_WEIGHT = {"direct": 1.0, "primary": 0.7, "secondary": 0.4, "peripheral": 0.2}
_HORIZON_WEIGHT = {"0-3m": 1.0, "3-6m": 0.8, "6-12m": 0.6, "1-3y": 0.3}
_STRENGTH_WEIGHT = {"大": 1.0, "中": 0.6, "小": 0.3}
_DEFAULT_WEIGHT = 0.5

# ⑤ 一次情報/二次情報/市場解説の信頼度(rss.pyのsource_tier)。
# 「材料そのものの強さ」(4要素の加重平均)とは別軸の「この報道自体を
# どれだけ信頼して良いか」を、掛け算のディスカウント係数として反映する。
# 新しい重み要素として加算する(=既存4要素の重みを再配分する)のではなく、
# 掛け算のペナルティにしているのは、情報源が弱いという理由で「材料は
# 弱いが情報源は一次情報だから加点」のような逆方向の底上げをしないため
# (一次情報であることは信頼度の上限であって、材料の強さの代わりにはならない)。
# source_tier不明(None、テスト等)は従来通りディスカウントしない。
SOURCE_TIER_LABEL = {"primary": "一次情報", "secondary": "二次情報", "commentary": "市場解説"}
_SOURCE_CONFIDENCE_MULT = {"primary": 1.0, "secondary": 0.85, "commentary": 0.65}


def compute_policy_impact_score(impact_entry, policy_maturity, source_tier=None):
    """[PRESENTATION LAYER] 政策実現度×受益距離×時間軸×感応度(strength)の統合スコア(0-100)。

    あくまでランキング・表示用の合成値であり、direction/theme/direct-indirect
    の判定(FACTUAL LAYER)には一切使わない・使われない。
    「市場規模」は実際の時価総額データを持っていないため、代わりに既存の
    strength(大/中/小、各テーマ作成者が見積もった影響度)を感応度の代理指標として使う。
    重み付けは経験則であり、data/rules.json 側の値ではなくここで完結させている
    (テーマ追加のたびに重みを書く手間を避けるため)。
    """
    maturity_norm = (policy_maturity / 100) if policy_maturity is not None else _DEFAULT_WEIGHT
    tier_w = _TIER_WEIGHT.get(impact_entry.get("beneficiary_tier"), _DEFAULT_WEIGHT)
    horizon_w = _HORIZON_WEIGHT.get(impact_entry.get("revenue_horizon"), _DEFAULT_WEIGHT)
    strength_w = _STRENGTH_WEIGHT.get(impact_entry.get("strength"), _DEFAULT_WEIGHT)

    score = 100 * (0.35 * maturity_norm + 0.25 * tier_w + 0.20 * horizon_w + 0.20 * strength_w)
    confidence_mult = _SOURCE_CONFIDENCE_MULT.get(source_tier, 1.0)
    return round(score * confidence_mult)


# ② 政策→業績距離の厳密化(ユーザー指示、必ず守ること):
# 新しいキーワード判定は増やさない。既存のpolicy_maturity(政策側の確度、
# 0-100)とrevenue_horizon(受益企業の業績到達時期、ルール作成者の見積もり)
# の2つを組み合わせるだけで、「政策発表→予算確保→受注→設備投資→売上→利益」
# のどこまで来ていそうかを表す。施策実施前は政策側の進捗だけで決まり
# (revenue_horizonは「実現したら」の見積もりでしかないため、まだ実現前の
# 段階では参照しない)、施策実施後になって初めてrevenue_horizonの近さで
# 受注〜利益のどこにいそうかを推定する。
_HORIZON_STAGE_AFTER_IMPLEMENTATION = {
    "0-3m": "受注", "3-6m": "設備投資", "6-12m": "売上寄与", "1-3y": "利益寄与",
}


def policy_to_earnings_stage(policy_maturity, revenue_horizon):
    """[PRESENTATION LAYER] 表示専用。direction/theme/tier等の判定には使わない。

    policy_maturityがNone(成熟度キーワード不一致)なら判定材料が無いのでNone。
    """
    if policy_maturity is None:
        return None
    if policy_maturity < 45:  # 検討・議論・審議会/検討会
        return "政策発表"
    if policy_maturity < 75:  # パブコメ・法案提出
        return "法案化"
    if policy_maturity < 100:  # 法案成立・予算成立
        return "予算確保"
    return _HORIZON_STAGE_AFTER_IMPLEMENTATION.get(revenue_horizon, "施策実施")


def score_importance(item, matched_themes, rules):
    """重要度(1〜5)とその根拠テキストを返す。"""
    text = item["title"]
    score = float(item.get("feed_weight", 1))
    reasons = []

    for entry in rules.importance_keywords:
        hit = [w for w in entry["words"] if w in text]
        if hit:
            score += entry["score"]
            reasons.append(f"重要語「{hit[0]}」")
            break

    if matched_themes:
        best = max(t.get("weight", 1) for t in matched_themes)
        score += best
        reasons.append(f"影響テーマ「{matched_themes[0]['label']}」に該当")

    related = len(item.get("related", []))
    if related:
        score += min(related, 3)
        reasons.append(f"{related + 1}媒体が報道")

    feed_cats = len(item.get("feed_categories", []))
    if feed_cats > 1:
        score += 1
        reasons.append("複数分野にまたがる話題")

    if is_future_signal(text, rules):
        score += 1
        reasons.append("先行情報(審議会・検討会・パブコメ等)")

    # 5段階に写像する(しきい値は経験則。data/rules.json の weight で調整可)
    if score >= 10:
        stars = 5
    elif score >= 8:
        stars = 4
    elif score >= 6:
        stars = 3
    elif score >= 4:
        stars = 2
    else:
        stars = 1
    return stars, "・".join(reasons) if reasons else "一般的な市況ニュース"


BENEFICIARY_TIER_LABEL = {
    "direct": "直接受益", "primary": "一次波及", "secondary": "二次波及", "peripheral": "周辺波及",
}
REVENUE_HORIZON_LABEL = {
    "0-3m": "0〜3ヶ月", "3-6m": "3〜6ヶ月", "6-12m": "6〜12ヶ月", "1-3y": "1〜3年",
}


def _impact_entry(
    stock, direction, strength, reason, origin, theme_label="",
    beneficiary_tier=None, revenue_horizon=None, matched_keyword=None, theme_id=None,
):
    """[PRESENTATION LAYER] beneficiary_tier/revenue_horizon/matched_keyword は表示用の付加情報。

    未指定(None)なら何も表示されないだけで、direction/theme/origin など
    FACTUAL LAYER の判定には一切影響しない。

    theme_id: どのテーマ由来の影響かを識別する(rules.jsonのthemes[].id)。
    intelligence_layer(政府政策/企業Capex/プラットフォーム規制のどれか)の
    判定にanalyze.py側で使う。当事者(direct)判定にはテーマが無いためNone。
    """
    return {
        "code": stock["code"],
        "name": stock["name"],
        "sector": stock.get("sector", ""),
        "direction": direction,
        "direction_label": DIRECTION_LABEL.get(direction, "注目"),
        "strength": strength,
        "reason": reason,
        "theme": theme_label,
        "theme_id": theme_id,
        "origin": origin,
        "beneficiary_tier": beneficiary_tier,
        "beneficiary_tier_label": BENEFICIARY_TIER_LABEL.get(beneficiary_tier, ""),
        "revenue_horizon": revenue_horizon,
        "revenue_horizon_label": REVENUE_HORIZON_LABEL.get(revenue_horizon, ""),
        "matched_keyword": matched_keyword,
    }


def affected_stocks(item, matched_themes, rules, master, max_items=8):
    """ニュース1件に対する影響銘柄リストを作る。

    1) 見出しに社名・証券コードが直接出ている銘柄(当事者)
    2) 該当テーマから波及する銘柄(rules.json の impacts)
    の順で並べ、同じ銘柄が重複した場合は当事者側を優先する。
    """
    text = item["title"]
    results = {}

    direct_direction = headline_sentiment(text, rules)
    # 見出し自体に明確な語調(拡大/規制強化など)があれば、方向を明示していない
    # (=watch の)ルールだけ、それで補う。金融政策のように「同じ見出しで
    # 銀行は追い風・不動産は逆風」と意図的に方向を分けているルールは、
    # ここで一律に上書きしない(rule側の direction をそのまま尊重する)。
    headline_direction = direct_direction if direct_direction != "watch" else None
    for stock in master.find_in_text(text, limit=4):
        reason = {
            "positive": "見出しで名指しされている当事者。内容は業績にプラスに働きうる材料",
            "negative": "見出しで名指しされている当事者。内容は業績にマイナスに働きうる材料",
            "watch": "見出しで名指しされている当事者。まず株価が反応しやすい",
        }[direct_direction]
        results[stock["code"]] = _impact_entry(
            stock, direct_direction, "大", reason, "direct", "当事者"
        )

    # テーマごとに1バケットにまとめ、ラウンドロビンはテーマ単位で行う。
    # (最初のテーマだけで枠を使い切って「追い風の銘柄しか出ない」のを避けるため)
    # 同じテーマ内では impacts の宣言順を守る(直接影響のブロックを先に書けば、
    # 同じ銘柄が間接影響ブロックにも出てきたときに直接側の方向を優先できる)。
    buckets = []
    for theme in matched_themes:
        theme_bucket = []
        seen_in_theme = set()
        # topic_keywords による並び替えは間接(direction="watch")ブロック
        # 同士の中だけで行う。方向が固定されているブロック(direct)は
        # 常に最優先を維持する(=直接優先の原則を壊さない)。
        # 例: 防衛費テーマで「サイバー防衛強化」という見出しなら、防衛(direct)
        # は変わらず先頭、末尾に書かれているサイバー枠(watch)だけ造船より
        # 先に繰り上がり、max_itemsの表示枠を奪われないようにする。
        def _sort_key(pair):
            idx, rule = pair
            is_fixed_direction = rule.get("direction", "watch") != "watch"
            topic_match = any(_keyword_hit(text, kw) for kw in rule.get("topic_keywords", []))
            return (0 if is_fixed_direction else 1, 0 if topic_match else 1, idx)

        impacts_sorted = sorted(enumerate(theme.get("impacts", [])), key=_sort_key)
        for _, rule in impacts_sorted:
            targets = []
            for code in rule.get("codes", []):
                s = master.by_code.get(code)
                if s:
                    targets.append(s)
            targets += master.by_themes(rule.get("themes", []), limit=rule.get("limit", 4))
            direction = rule.get("direction", "watch")
            if direction == "watch" and headline_direction:
                direction = headline_direction
            matched_kw = theme.get("_matched_keyword")
            base_reason = rule.get("reason", "")
            reason = f"「{matched_kw}」に反応: {base_reason}" if matched_kw else base_reason
            for stock in targets[: rule.get("limit", 4)]:
                if stock["code"] in seen_in_theme:
                    continue
                seen_in_theme.add(stock["code"])
                theme_bucket.append(_impact_entry(
                    stock, direction, rule.get("strength", "中"), reason,
                    "rule", theme["label"],
                    beneficiary_tier=rule.get("beneficiary_tier"),
                    revenue_horizon=rule.get("revenue_horizon"),
                    matched_keyword=matched_kw,
                    theme_id=theme.get("id"),
                ))
        if theme_bucket:
            buckets.append(theme_bucket)

    depth = 0
    while buckets and len(results) < max_items:
        progressed = False
        for bucket in buckets:
            if depth >= len(bucket):
                continue
            progressed = True
            entry = bucket[depth]
            if entry["code"] not in results and len(results) < max_items:
                results[entry["code"]] = entry
        if not progressed:
            break
        depth += 1

    ordered = sorted(
        results.values(),
        key=lambda r: (
            0 if r["origin"] == "direct" else 1,
            -STRENGTH_ORDER.get(r["strength"], 1),
        ),
    )
    return ordered[:max_items]


def pick_category(item, matched_themes, rules):
    """テーマが決まればそのカテゴリ、無ければフィード側のカテゴリを使う。"""
    if matched_themes:
        cat = matched_themes[0].get("category")
        if cat in rules.category_label:
            return cat
    cat = item.get("feed_category", "market")
    return cat if cat in rules.category_label else "market"
