#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ネット接続なしで表示を確認するためのサンプルデータ。

`python3 build_news_site.py --sample` から使う。ルール判定は本番と同じコードを通すので、
data/rules.json や data/stocks.json を編集した結果の確認にも使える。

【プロジェクト原則】ここでbuild_news()の判定ロジックを部分的にでも
再実装しないこと。過去に一度、themes/importance/impactsだけ本番と同じ
関数を呼び、その後追加されたpolicy_maturity・news_novelty・
policy_event_id等は複製し忘れる(=このプレビューにだけ表示されない)
というdriftが実際に発生した(該当コミット参照)。raw_items/
persist_lifecycleを使ってbuild_news()自体に委譲し、判定ロジックの
実体は常に1箇所(analyze.build_news)にする。
"""
from datetime import datetime, timedelta

from . import analyze, impact as impact_mod, stocks as stocks_mod
from .config import JST
from .rss import _source_tier, news_id

# ⑤ 一次情報/二次情報/市場解説の信頼度をサンプルでも確認できるよう、
# 一部の見出しは意図的にsource(取得元)をprimary/commentaryの実例に
# している(経済産業省=省庁直接購読を模したprimary、東洋経済オンライン=
# COMMENTARY_SOURCE_KEYWORDS該当のcommentary)。tier未指定はNoneのまま
# _source_tier()に渡し、fetch()と同じ判定(secondary/commentary)を通す。
SAMPLE_HEADLINES = [
    ("日銀、追加利上げを決定 政策金利0.75%に 長期金利は上昇", "日本経済新聞", "policy", 2, 3, None),
    ("米政権、日本車への追加関税を表明 自動車業界に影響懸念", "ロイター", "trade", 2, 2, None),
    ("中東情勢が緊迫 ホルムズ海峡に警戒感、原油価格が急騰", "時事通信", "geopolitics", 2, 1, None),
    ("エヌビディア決算が市場予想を上回る AI半導体とデータセンター投資が拡大", "Bloomberg", "tech", 2, 1, None),
    ("円安進行、一時1ドル=158円台 輸入コスト上昇に警戒", "NHK", "fx", 1, 0, None),
    ("半導体の対中輸出規制を強化へ 経済安全保障を重視", "経済産業省", "trade", 2, 2, "primary"),
    ("トヨタ自動車の上方修正をどう読むか 増益基調は続くか", "東洋経済オンライン", "corporate", 2, 1, None),
    ("訪日外国人客が過去最高を更新 インバウンド消費も拡大", "観光経済新聞", "japan", 1, 0, None),
    ("政府が経済対策を閣議決定 補正予算は規模拡大へ", "読売新聞", "japan", 1, 1, None),
    ("米国株、ナスダックが反落 ハイテク株に利益確定売り", "ロイター", "us", 1, 0, None),
    ("データセンター向け電力需要が急増 原発の再稼働論議も", "電気新聞", "resources", 1, 0, None),
    ("大手商社に大規模なサイバー攻撃 情報漏えいの可能性", "ITmedia", "tech", 1, 0, None),
    ("中国、対日レアアース輸出規制を強化 自動車部品の調達に懸念", "共同通信", "trade", 2, 1, None),
    ("双日、レアアース権益確保へ豪州で新規開発 中国依存低減を加速", "日本経済新聞", "trade", 2, 0, None),
]


def sample_data():
    rules = impact_mod.load()
    now = datetime.now(JST)

    items = []
    for i, (title, source, category, weight, related_count, tier) in enumerate(SAMPLE_HEADLINES):
        published = now - timedelta(hours=i * 2 + 1)
        items.append({
            "id": news_id(title, f"https://example.com/sample/{i}"),
            "title": title,
            "url": f"https://news.google.com/search?q={i}",
            "source": source,
            "source_tier": tier or _source_tier(source),
            "published": published,
            "feed_query": "sample",
            "feed_category": category,
            "feed_weight": weight,
            "feed_categories": [category],
            "related": [
                {"title": f"{title}(関連報道 {j + 1})", "url": f"https://example.com/related/{i}/{j}",
                 "source": "関連媒体"}
                for j in range(related_count)
            ],
        })

    # raw_items/persist_lifecycle=False: RSS取得はスキップしつつ、判定ロジック
    # (成熟度・新規性・policy_event_id含む)は本番のbuild_news()と完全に同じ
    # コードを通す(ここで別ロジックを書くと、今回のように機能追加のたびに
    # サンプル画面だけ表示されない、という drift が再発するため)。
    # 本番のpolicy_event_registry.jsonは汚さない(persist_lifecycle=False)。
    news = analyze.build_news(rules=rules, raw_items=items, use_llm=False, persist_lifecycle=False)
    ranking = analyze.stock_ranking(news)
    # 本番のtheme_trend_registry.jsonは汚さない(persist=False)。
    theme_stage_info = analyze.apply_emergence_signals(news, rules, now.strftime("%Y-%m-%d"), persist=False)
    master = stocks_mod.load()
    clusters = analyze.attach_cluster_emergence(
        analyze.build_material_clusters(news, rules, master), theme_stage_info
    )

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "generated_iso": now.isoformat(timespec="seconds"),
        "status": "sample",
        "status_message": "サンプルデータで生成した表示確認用のページです。",
        "llm_used": False,
        "categories": rules.categories,
        "market": [
            {"label": "日経平均", "value": "41,250.10", "change_pct": 0.82, "asof": "15:00"},
            {"label": "ドル円", "value": "157.42", "change_pct": 0.31, "asof": "18:00"},
            {"label": "S&P500", "value": "6,102.44", "change_pct": -0.45, "asof": "終値"},
        ],
        "news": news,
        "stock_ranking": ranking,
        "clusters": clusters,
        "counts": {
            "news": len(news),
            "high_importance": sum(1 for n in news if n["importance"] >= 4),
            "stocks": len(ranking),
        },
    }
