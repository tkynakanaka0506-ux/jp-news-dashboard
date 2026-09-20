#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ニュース収集 → 重要度判定 → 影響銘柄付与 → news.json のデータ構造を組み立てる。"""
import json
from datetime import datetime
from pathlib import Path

from . import backtest as backtest_mod
from . import catalyst_export
from . import impact as impact_mod
from . import llm as llm_mod
from . import policy_lifecycle
from . import rss as rss_mod
from . import stocks as stocks_mod
from . import theme_trends
from .config import FEEDS, GOV_FEEDS, JST, MARKET_TICKERS, MAX_AGE_HOURS, MAX_NEWS_ITEMS, PER_FEED_LIMIT

DIRECTION_LABEL = impact_mod.DIRECTION_LABEL


def log(msg):
    print(f"[analyze] {msg}", flush=True)


def _fmt_dt(dt):
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def _dig(obj, path):
    cur = obj
    for key in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def market_snapshot(data_json_path):
    """既存の data.json(Javaが取得している指数・為替)があればヘッダー用に読み込む。"""
    path = Path(data_json_path)
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log(f"data.json を読めませんでした({e})。市況ヘッダーは省略します。")
        return []

    out = []
    for key, label in MARKET_TICKERS:
        node = _dig(data, key)
        if not isinstance(node, dict):
            continue
        value = (node.get("value") or "").strip()
        if not value or value in ("―", "-"):
            continue
        out.append({
            "label": label,
            "value": value,
            "change_pct": node.get("change_pct"),
            "asof": node.get("asof", ""),
        })
    return out


NOVELTY_LABEL = {"high": "新規", "medium": "続報", "low": "再報道"}


def _news_novelty(item, prior_norms):
    """[PRESENTATION LAYER] 「初出/続報/再報道」の3段階。

    政策として重要でも、既に何度も報じられているニュースは新しい投資材料
    とは限らない、という考え方をスコアと別軸で表現する。theme/direction/
    direct-indirect(FACTUAL LAYER)には一切使わない・影響しない。

    判定材料は2つ:
      - related(同日内に他媒体が同じ話題を報じた数。rss.collect が既に集計済み)
      - 過去日の記録(backtest_events.jsonl)に似た見出しが既にあるか
        (=違う日にも同じ話題が出ている「続報」らしさ)

    prior_norms は事前に rss._normalize() 済みの文字列を渡すこと
    (_same_topic は正規化済み同士の比較を前提にしているため)。
    """
    norm = rss_mod._normalize(item["title"])
    seen_before = any(rss_mod._same_topic(norm, prior) for prior in prior_norms)
    related_count = len(item.get("related", []))

    if seen_before:
        return "low", "similar_seen_before"
    if related_count >= 2:
        return "medium", f"{related_count + 1}媒体が同時報道"
    return "high", "初出・類似の過去記録なし"


def build_news(feeds=None, rules=None, master=None, use_llm=True, limit=MAX_NEWS_ITEMS,
                raw_items=None, persist_lifecycle=True):
    """ニュースを集めて、1件ずつに重要度・カテゴリ・影響銘柄を付けたリストを返す。

    raw_items: 指定すればRSS取得をスキップしてこれを使う(sample.py等、本番と
    同じ判定ロジックを通したいがネット接続はしたくない呼び出し元向け)。
    persist_lifecycle: Falseならpolicy_event_registry.jsonの読み書きをせず、
    その場限りの空レジストリで判定する(サンプル/テスト実行が本番の
    政策ライフサイクル追跡状態を汚さないようにするため)。
    """
    rules = rules or impact_mod.load()
    master = master or stocks_mod.load()
    if raw_items is None:
        raw_items = rss_mod.collect(
            feeds or FEEDS, direct_feeds=GOV_FEEDS, per_feed_limit=PER_FEED_LIMIT, max_age_hours=MAX_AGE_HOURS
        )
    log(f"重複を束ねた結果 {len(raw_items)} 件の話題を取得しました。")

    today = datetime.now(JST).strftime("%Y-%m-%d")
    prior_titles = backtest_mod.load_prior_titles(today)
    prior_norms = [rss_mod._normalize(t) for t in prior_titles]
    lifecycle_registry = policy_lifecycle._load_registry() if persist_lifecycle else {"active": {}, "_seq": {}}

    news = []
    for item in raw_items:
        title = item["title"]
        if impact_mod.is_noise(title, rules):
            continue
        themes = impact_mod.match_themes(title, rules)
        stars, reason = impact_mod.score_importance(item, themes, rules)
        impacts = impact_mod.affected_stocks(item, themes, rules, master, max_items=8)
        category = impact_mod.pick_category(item, themes, rules)
        maturity_score, maturity_label = impact_mod.score_policy_maturity(title, rules)
        novelty, novelty_reason = _news_novelty(item, prior_norms)
        # [FACTUAL LAYER] 同じ政策の続報に安定したIDを割り当てるだけで、
        # score/direction/primary_themeの再計算はしない(policy_lifecycle.py参照)。
        policy_event_id = policy_event_is_update = policy_event_first_seen = policy_event_state = None
        if themes:
            policy_event_id, policy_event_is_update, policy_event_first_seen, policy_event_state = (
                policy_lifecycle.resolve_policy_event_id(
                    lifecycle_registry,
                    theme_id=themes[0].get("id", ""),
                    matched_keyword=themes[0].get("_matched_keyword"),
                    maturity_score=maturity_score,
                    title=title,
                    day=today,
                )
            )
        # [PRESENTATION LAYER] 表示用の統合スコアを付与するだけで、direction/theme
        # など impacts の中身(FACTUAL LAYER)は書き換えない。
        # 「政府・政策が市場を動かす力」(policy_impact_score)と「巨大テックの
        # 設備投資が需要を動かす力」(ai_capex_impact_score)は別の力学なので、
        # 同じ計算式(compute_policy_impact_score)を使い回しつつ、別フィールド
        # として書き分ける(MJS側にも分離したまま渡す。ユーザー方針2026-09-13)。
        # platform_regulation(プラットフォーム規制リスク)はどちらの実現度
        # スコアにも馴染まないため、どちらのフィールドにも値を入れない
        # (テーマ検出・重要度スコアのみ。JP株への波及先が無い設計のため)。
        source_tier = item.get("source_tier")
        for imp in impacts:
            layer = rules.theme_layer.get(imp.get("theme_id"), "government_policy")
            score = (
                impact_mod.compute_policy_impact_score(imp, maturity_score, source_tier)
                if layer in ("government_policy", "corporate_capex") else None
            )
            imp["intelligence_layer"] = layer
            imp["policy_impact_score"] = score if layer == "government_policy" else None
            imp["ai_capex_impact_score"] = score if layer == "corporate_capex" else None
            imp["policy_to_earnings_stage"] = impact_mod.policy_to_earnings_stage(
                maturity_score, imp.get("revenue_horizon")
            )
        news.append({
            "id": item["id"],
            "title": title,
            "url": item["url"],
            "source": item["source"],
            "source_tier": source_tier,
            "source_tier_label": impact_mod.SOURCE_TIER_LABEL.get(source_tier, ""),
            "published_at": _fmt_dt(item.get("published")),
            "published_ts": item["published"].timestamp() if item.get("published") else 0,
            "category": category,
            "category_label": rules.category_label.get(category, "市況"),
            "category_emoji": rules.category_emoji.get(category, "📰"),
            "importance": stars,
            "importance_reason": reason,
            "future_signal": impact_mod.is_future_signal(title, rules),
            "policy_maturity": maturity_score,
            "policy_maturity_label": maturity_label,
            "news_novelty": novelty,
            "news_novelty_label": NOVELTY_LABEL[novelty],
            "policy_event_id": policy_event_id,
            "policy_event_is_update": policy_event_is_update,
            "policy_event_first_seen": policy_event_first_seen,
            "policy_event_state": policy_event_state,
            "themes": [t["label"] for t in themes],
            "theme_ids": [t["id"] for t in themes if t.get("id")],
            "summary": "",
            "impact_comment": "",
            "impacts": impacts,
            "related": item.get("related", [])[:4],
        })

    # 今回のニュースに含まれなかった系列も、無音のままLIFECYCLE_WINDOW_DAYS
    # 経過していればCLOSEDにする(resolve_policy_event_id()だけでは検知できない)。
    policy_lifecycle.sweep_expired(lifecycle_registry, today)
    if persist_lifecycle:
        policy_lifecycle._save_registry(lifecycle_registry)

    # 重要度 → 新しさ の順に並べ、上位だけをページに載せる
    news.sort(key=lambda n: (-n["importance"], -n["published_ts"]))
    news = news[:limit]

    if use_llm and llm_mod.available():
        _apply_llm(news, master)

    return news


def _apply_llm(news, master, target=14):
    """上位ニュースにだけ要約・コメント・追加銘柄を付ける(無料枠を節約するため)。

    candidate_stocks も全銘柄ではなく、今回のニュースのテーマに関係する銘柄
    (+主力銘柄は保険として常に残す)だけに絞り、プロンプトのトークン量を減らす。
    """
    targets = news[:target]
    if not targets:
        return
    used_themes = set()
    for n in targets:
        used_themes.update(n.get("themes", []))
    if used_themes:
        pool = [s for s in master.stocks if used_themes & set(s.get("themes", []))]
    else:
        pool = list(master.stocks)
    seen = {s["code"] for s in pool}
    for s in master.stocks:
        if "主力" in s.get("themes", []) and s["code"] not in seen:
            pool.append(s)
            seen.add(s["code"])
    candidates = [
        {"code": s["code"], "name": s["name"], "sector": s.get("sector", ""), "themes": s.get("themes", [])}
        for s in pool
    ]
    result = llm_mod.enrich(targets, candidates)
    if not result:
        return
    by_id = {n["id"]: n for n in targets}
    applied = 0
    for entry in result.get("items", []) or []:
        news_item = by_id.get(entry.get("id"))
        if not news_item:
            continue
        summary = (entry.get("summary") or "").strip()
        comment = (entry.get("impact_comment") or "").strip()
        if summary:
            news_item["summary"] = summary
        if comment:
            news_item["impact_comment"] = comment
        importance = entry.get("importance")
        if isinstance(importance, int) and 1 <= importance <= 5:
            # ルール判定とLLM判定の平均(四捨五入)にして、片方の極端な判定に寄せない
            news_item["importance"] = int(round((news_item["importance"] + importance) / 2))
        have = {i["code"] for i in news_item["impacts"]}
        for extra in (entry.get("extra_stocks") or [])[:3]:
            stock = master.by_code.get((extra.get("code") or "").strip())
            if not stock or stock["code"] in have:
                continue
            direction = extra.get("direction") if extra.get("direction") in DIRECTION_LABEL else "watch"
            news_item["impacts"].append({
                "code": stock["code"],
                "name": stock["name"],
                "sector": stock.get("sector", ""),
                "direction": direction,
                "direction_label": DIRECTION_LABEL[direction],
                "strength": "中",
                "reason": (extra.get("reason") or "").strip() or "ニュース内容との関連がAI判定で指摘された銘柄",
                "theme": "AI判定",
                "origin": "llm",
            })
            have.add(stock["code"])
        applied += 1
    log(f"{applied} 件のニュースにAI補強を反映しました。")
    news.sort(key=lambda n: (-n["importance"], -n["published_ts"]))


def stock_ranking(news, limit=20):
    """ニュース横断で「今どの銘柄に材料が集まっているか」を集計する。"""
    agg = {}
    for n in news:
        weight = n["importance"]
        for imp in n["impacts"]:
            row = agg.setdefault(imp["code"], {
                "code": imp["code"],
                "name": imp["name"],
                "sector": imp.get("sector", ""),
                "positive": 0,
                "negative": 0,
                "watch": 0,
                "score": 0,
                "news": [],
            })
            row[imp["direction"]] = row.get(imp["direction"], 0) + 1
            sign = {"positive": 1, "negative": -1, "watch": 0}[imp["direction"]]
            row["score"] += sign * weight
            if len(row["news"]) < 3:
                row["news"].append({
                    "title": n["title"],
                    "url": n["url"],
                    "direction": imp["direction"],
                    "reason": imp["reason"],
                    "importance": n["importance"],
                })
    rows = list(agg.values())
    for row in rows:
        row["mentions"] = row["positive"] + row["negative"] + row["watch"]
    rows.sort(key=lambda r: (-r["mentions"], -abs(r["score"])))
    return rows[:limit]


def apply_emergence_signals(news, rules, today, persist=True):
    """[分析レイヤー/PRESENTATION LAYER] 各ニュースに萌芽シグナル(theme_trends.py)
    のステージを付与する。1件のニュースが複数テーマに該当する場合は、
    最も優先度の高いステージを採用する(best_stage_for_theme_ids)。

    戻り値はテーマごとのステージ情報({theme_id: {...}})。
    build_theme_clusters/build_material_clustersのクラスターにも
    同じ情報を付与できるよう、呼び出し側に返す。
    """
    registry = theme_trends._load_registry() if persist else {"themes": {}}
    theme_stage_info = theme_trends.record_and_classify(registry, news, rules, today)
    if persist:
        theme_trends._save_registry(registry)

    for item in news:
        best = theme_trends.best_stage_for_theme_ids(item.get("theme_ids", []), theme_stage_info)
        item.update(_emergence_fields(best))
    return theme_stage_info


# theme_trends.record_and_classify()が返す診断情報のキー一覧。newsの各
# 記事にもクラスターにも同じ形で付与するため、フィールド追加はここ1箇所
# だけ直せばよい(analyze.py/sample.pyでの二重管理を避ける)。
_EMERGENCE_FIELD_DEFAULTS = {
    "stage": None, "stage_label": "", "signal_labels": [], "event_count": 0,
    "source_count": 0, "actor_type_count": 0, "actor_type_labels": [], "actor_type_breakdown": [],
    "region_count": 0, "regions": [], "origin_regions": [], "first_seen": None,
    "window_counts": {}, "timeline": [], "milestones": [], "diagnosis": {},
}


def _emergence_fields(info):
    """theme_trends.record_and_classify()の1テーマぶんの診断情報を、
    news item / cluster にそのまま付与できる emergence_* キーの辞書にする。
    """
    info = info or {}
    return {
        f"emergence_{key}": info.get(key, default)
        for key, default in _EMERGENCE_FIELD_DEFAULTS.items()
    }


def attach_cluster_emergence(clusters, theme_stage_info):
    """テーマ型クラスターに萌芽シグナル情報を付与する(build()/sample.pyの
    両方から呼ぶ共通処理。判定ロジック自体はtheme_trends.pyの1箇所のみ)。
    """
    for c in clusters:
        info = theme_stage_info.get(c.get("theme_id")) if c["connection_type"] == "theme" else None
        c.update(_emergence_fields(info))
    return clusters


def build_theme_clusters(news, rules, min_members=2, max_clusters=8):
    """[分析レイヤー] 見出しの文言が違っても同じ「材料」(テーマ)を共有する
    ニュースを束ねて、ひとつの大きなテーマとして把握できるようにする。

    ユーザー要望(2026-09-20)「一見異なる内容のニュースでも、関連する企業・
    業界・資源・国地域・政策・規制・地政学・サプライチェーンなどに
    共通点がある場合、自動的に関連付けたい」への対応。

    連結の単位は既存のtheme(rules.jsonの各テーマ)をそのまま使う。
    国・地域(middle_east等)・資源(rare_earth等)・政策(boj_hike等)・
    規制(semi_regulation等)は元々rules.jsonのテーマ単位で表現されている
    ため、新しい判定ロジックを作らず「同じtheme_idに複数の見出しが
    ヒットしている」という既存の判定結果を数えるだけでよい。

    1件のニュースが複数テーマ(theme_ids)に該当する場合は、それぞれの
    クラスターに重複して入れる(Union-Findのような相互排他な統合は
    しない。1つの出来事が複数の切り口で重要というケースを潰さないため)。
    """
    by_theme = {}
    for item in news:
        for tid in item.get("theme_ids", []):
            by_theme.setdefault(tid, []).append(item)

    theme_meta = {t["id"]: t for t in rules.themes}
    clusters = []
    for tid, members in by_theme.items():
        if len(members) < min_members:
            continue
        theme = theme_meta.get(tid, {})
        category = theme.get("category", "")

        # このクラスターに属する影響銘柄だけを集計する(他テーマ由来の
        # 影響まで混ぜると「このテーマで何が動くか」がぼやけるため、
        # imp["theme_id"]がこのtidと一致するものだけを数える)。
        stock_agg = {}
        for m in members:
            for imp in m.get("impacts", []):
                if imp.get("theme_id") != tid:
                    continue
                row = stock_agg.setdefault(imp["code"], {
                    "code": imp["code"], "name": imp["name"],
                    "positive": 0, "negative": 0, "watch": 0,
                })
                row[imp["direction"]] = row.get(imp["direction"], 0) + 1
        stocks = sorted(
            stock_agg.values(),
            key=lambda r: -(r["positive"] + r["negative"] + r["watch"]),
        )[:6]

        sorted_members = sorted(members, key=lambda m: -(m["importance"] or 0))
        clusters.append({
            "connection_type": "theme",
            "connection_label": "同じテーマ",
            "theme_id": tid,
            "label": theme.get("label", tid),
            "category": category,
            "category_label": rules.category_label.get(category, ""),
            "category_emoji": rules.category_emoji.get(category, "📰"),
            "member_ids": [m["id"] for m in sorted_members],
            "members": [
                {"id": m["id"], "title": m["title"], "url": m["url"], "importance": m["importance"], "source": m["source"]}
                for m in sorted_members
            ],
            "stocks": stocks,
            "max_importance": max(m["importance"] for m in members),
        })

    clusters.sort(key=lambda c: (-len(c["members"]), -c["max_importance"]))
    return clusters[:max_clusters]


# サプライチェーン・資源の切り口としては実質的な意味を持たない、財務上の
# 特性ラベル(円安/円高の恩恵を受けやすい・主力銘柄)は除外する。これらは
# 「同じ材料でつながっている」という実質的な関係ではなく、ポートフォリオ上の
# 分類にすぎず、無関係な企業同士を大量に結びつけてノイズになるため。
NON_SUBSTANTIVE_STOCK_TAGS = {"主力", "円安メリット", "円高メリット"}


def _grouped_material_clusters(news, master, exclude_member_sets, min_members, max_per_type):
    """[分析レイヤー] テーマが一致していなくても実質的につながっている
    ニュースを見つける。

    ユーザー要望(2026-09-20)「単純なテーマ一致だけでは見つけられない
    ニュース同士の実質的なつながりも発見したい」への対応。
    ニュース→企業→業界→資源・サプライチェーンという、既に計算済みの
    データ(impacts[].code/sector、stocks.jsonのthemesタグ)をそのまま
    辿るだけで、新しい判定(要約・埋め込み類似度など)は一切追加しない:

      - 企業: 同じ影響銘柄コードに複数ニュースが触れている
              (例: 政策ニュースの間接影響と、その企業自身の決算ニュースが
              同じ企業を指していれば、テーマが違っても実質的につながる)
      - 業界: 影響銘柄のsector(業種)が複数ニュースで重なっている
      - 資源・サプライチェーン: 影響銘柄のthemesタグ(stocks.json、
              「資源エネルギー」「半導体材料」「EV」等)が複数ニュースで
              重なっている

    exclude_member_sets(既存のテーマクラスターの構成員集合)と完全に
    同じ集合になったクラスターは、重複表示を避けるため捨てる。
    """
    by_code, by_sector, by_tag = {}, {}, {}
    for item in news:
        seen_codes, seen_sectors, seen_tags = set(), set(), set()
        for imp in item.get("impacts", []):
            code = imp.get("code")
            if code and code not in seen_codes:
                by_code.setdefault(code, []).append(item)
                seen_codes.add(code)
            sector = imp.get("sector")
            if sector and sector not in seen_sectors:
                by_sector.setdefault(sector, []).append(item)
                seen_sectors.add(sector)
            stock = master.by_code.get(code) if code else None
            for tag in (stock or {}).get("themes", []):
                if tag in NON_SUBSTANTIVE_STOCK_TAGS or tag in seen_tags:
                    continue
                by_tag.setdefault(tag, []).append(item)
                seen_tags.add(tag)

    def build(groups, connection_type, connection_label, label_fn, emoji, stock_filter):
        out = []
        for key, members in groups.items():
            if len(members) < min_members:
                continue
            member_ids = frozenset(m["id"] for m in members)
            if member_ids in exclude_member_sets:
                continue
            sorted_members = sorted(members, key=lambda m: -(m["importance"] or 0))
            stock_agg = {}
            for m in members:
                for imp in m.get("impacts", []):
                    if not stock_filter(imp, key):
                        continue
                    row = stock_agg.setdefault(imp["code"], {
                        "code": imp["code"], "name": imp["name"],
                        "positive": 0, "negative": 0, "watch": 0,
                    })
                    row[imp["direction"]] = row.get(imp["direction"], 0) + 1
            stocks = sorted(
                stock_agg.values(), key=lambda r: -(r["positive"] + r["negative"] + r["watch"])
            )[:6]
            out.append({
                "connection_type": connection_type,
                "connection_label": connection_label,
                "theme_id": None,
                "label": label_fn(key),
                "category": "",
                "category_label": connection_label,
                "category_emoji": emoji,
                "member_ids": [m["id"] for m in sorted_members],
                "members": [
                    {"id": m["id"], "title": m["title"], "url": m["url"], "importance": m["importance"], "source": m["source"]}
                    for m in sorted_members
                ],
                "stocks": stocks,
                "max_importance": max(m["importance"] for m in members),
            })
        out.sort(key=lambda c: (-len(c["members"]), -c["max_importance"]))
        return out[:max_per_type]

    company_clusters = build(
        by_code, "company", "同じ企業",
        lambda code: f"{(master.by_code.get(code) or {}).get('name', code)}に関するニュース",
        "🏢", lambda imp, key: imp.get("code") == key,
    )
    sector_clusters = build(
        by_sector, "sector", "同じ業界",
        lambda sector: f"{sector}業界のニュース",
        "🏭", lambda imp, key: imp.get("sector") == key,
    )
    tag_clusters = build(
        by_tag, "supply_chain", "資源・サプライチェーン",
        lambda tag: f"{tag}に関連するニュース",
        "🔗", lambda imp, key: key in (master.by_code.get(imp.get("code")) or {}).get("themes", []),
    )
    return company_clusters + sector_clusters + tag_clusters


def build_material_clusters(news, rules, master, min_members=2, max_clusters=8, max_per_type=4):
    """[分析レイヤー] build_theme_clustersの「同じテーマ」に加えて、
    企業・業界・資源/サプライチェーンという軸でも実質的なつながりを探す
    (ユーザー要望2026-09-20「テーマ一致だけでは見つけられないつながりも
    発見したい」)。テーマで束ねられる分は従来通り優先し、テーマだけでは
    束ねられない追加のつながりだけを新しい種類として載せる。
    """
    theme_clusters = build_theme_clusters(news, rules, min_members=min_members, max_clusters=max_clusters)
    exclude = {frozenset(c["member_ids"]) for c in theme_clusters}
    extra = _grouped_material_clusters(news, master, exclude, min_members, max_per_type)
    combined = theme_clusters + extra
    # 精度の高い(誤解を招きにくい)つながりを先に見せる: テーマ一致が
    # 最も具体的で説明しやすく、業界・サプライチェーンタグは対象が
    # 広がりやすい分、粒度が粗くなる。件数の多さだけで並べると、粗い
    # つながりが具体的なつながりを埋もれさせてしまうため、種類の精度を
    # 第一キーにする(同じ種類の中では従来通り件数順)。
    type_priority = {"theme": 0, "company": 1, "sector": 2, "supply_chain": 3}
    combined.sort(key=lambda c: (type_priority.get(c["connection_type"], 9), -len(c["members"]), -c["max_importance"]))
    return combined[:max_clusters + 3 * max_per_type]


def build(data_json_path="data.json", use_llm=True):
    """news.json に書き出すデータ全体を作る。"""
    rules = impact_mod.load()
    master = stocks_mod.load()
    now = datetime.now(JST)
    news = build_news(rules=rules, master=master, use_llm=use_llm)
    ranking = stock_ranking(news)
    theme_stage_info = apply_emergence_signals(news, rules, now.strftime("%Y-%m-%d"))
    clusters = attach_cluster_emergence(build_material_clusters(news, rules, master), theme_stage_info)
    # [バックテスト基盤] 本番ビルドのたびに今回判定したイベントをログへ追記する。
    # 株価データはまだ接続していないため、現時点ではニュース×銘柄×スコアの
    # 履歴を貯めるだけ(dev.py backtest で BACKTEST_STATUS を確認できる)。
    try:
        backtest_mod.record_events(news, generated_at=now)
    except Exception as e:  # バックテスト記録の失敗でサイト生成自体を止めない
        log(f"バックテストイベントの記録に失敗しました({e})。サイト生成は続行します。")

    # [Catalyst Intelligence 出力] 他プロジェクト(投資判断エンジン側)が読み込む
    # 「今アクティブな政策材料」スナップショット。統合演算はしない(catalyst_export
    # のdocstring参照)。書き出しの失敗でサイト生成自体は止めない。
    try:
        n = catalyst_export.export_signals(news)
        log(f"政策材料シグナルを{n}件書き出しました({catalyst_export.EXPORT_PATH})。")
    except Exception as e:
        log(f"政策材料シグナルの書き出しに失敗しました({e})。サイト生成は続行します。")

    status = "updated" if news else "unavailable"
    status_message = (
        f"{len(news)} 件のニュースを取得しました。"
        if news
        else "ニュースを取得できませんでした(RSS取得失敗の可能性があります)。"
    )

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "generated_iso": now.isoformat(timespec="seconds"),
        "status": status,
        "status_message": status_message,
        "llm_used": bool(use_llm and llm_mod.available()),
        "categories": rules.categories,
        "market": market_snapshot(data_json_path),
        "news": news,
        "stock_ranking": ranking,
        "clusters": clusters,
        "counts": {
            "news": len(news),
            "high_importance": sum(1 for n in news if n["importance"] >= 4),
            "stocks": len(ranking),
        },
    }
