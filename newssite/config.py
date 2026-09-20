#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""収集対象フィードと共通定数。

ニュースの取得元を増やしたいときは FEEDS に1行足すだけでよい。
category は data/rules.json の categories[].id と対応させること。
"""
from datetime import timedelta, timezone

JST = timezone(timedelta(hours=9))

UA = "Mozilla/5.0 (compatible; jp-news-impact-bot/1.0)"

# 1フィードあたりの取得件数と、ページに載せるニュースの最大件数
PER_FEED_LIMIT = 12
MAX_NEWS_ITEMS = 40

# これより古い記事は捨てる(時間)
MAX_AGE_HOURS = 48

# Google News RSS 検索クエリ。(query, category, 基礎重要度)
FEEDS = [
    ("日銀 金融政策 金利", "policy", 2),
    ("FRB FOMC 利上げ 利下げ", "policy", 2),
    ("長期金利 国債利回り", "policy", 1),
    ("円相場 為替 ドル円", "fx", 1),
    ("為替介入 円安 円高", "fx", 2),
    ("関税 貿易摩擦 通商", "trade", 2),
    ("輸出規制 経済安全保障 半導体", "trade", 2),
    ("レアアース 希土類 輸出規制 中国", "trade", 2),
    ("中東情勢 原油 イスラエル イラン", "geopolitics", 2),
    ("ウクライナ ロシア 停戦", "geopolitics", 1),
    ("台湾 中国 安全保障", "geopolitics", 1),
    ("原油価格 OPEC 資源価格", "resources", 1),
    ("電力需要 データセンター 原発", "resources", 1),
    ("半導体 AI 投資 増産", "tech", 2),
    ("エヌビディア 生成AI 決算", "tech", 1),
    ("Meta Microsoft Amazon Google 設備投資 Capex", "tech", 2),
    ("データセンター投資 GPU調達 AI インフラ", "tech", 1),
    ("SNS規制 未成年 プラットフォーム訴訟", "trade", 1),
    ("日本株 東証 株式市場", "market", 1),
    ("日経平均 急騰 急落", "market", 1),
    ("上方修正 業績予想 決算", "corporate", 2),
    ("自社株買い 増配 TOB 買収", "corporate", 2),
    ("経済対策 補正予算 政府", "japan", 1),
    ("造船 海事 政策 支援 投資", "japan", 2),
    ("経済安全保障 基幹産業 支援策 国土交通省 経済産業省", "japan", 1),
    ("訪日客 インバウンド 消費", "japan", 1),
    ("米国株 ダウ ナスダック", "us", 1),
]

# 省庁など一次情報のRSS/RDFを直接購読する。(url, 表示ソース名, category, 基礎重要度)
# Google Newsは報道機関が記事を書き終えるまで拾えないため、発表そのものを
# 直接取りに行くための経路。追加するときは URL が本物か必ず確認すること
# (見つからない/ブロックされる省庁も多く、404や403は静かに無視される)。
GOV_FEEDS = [
    ("https://www.cao.go.jp/rss/news.rdf", "内閣府", "japan", 2),
    ("https://www.digital.go.jp/rss/news.xml", "デジタル庁", "japan", 2),
    ("https://www.kantei.go.jp/index-jnews.rdf", "首相官邸", "japan", 2),
    ("https://www.mhlw.go.jp/stf/news.rdf", "厚生労働省", "japan", 1),
    ("https://www.mhlw.go.jp/stf/kinkyu.rdf", "厚生労働省(緊急情報)", "japan", 2),
    ("https://www.fsa.go.jp/fsaNewsListAll_rss2.xml", "金融庁", "japan", 2),
    ("https://www.moj.go.jp/news.xml", "法務省", "japan", 1),
    ("https://www.mof.go.jp/news.rss", "財務省", "japan", 2),
    ("https://www.maff.go.jp/rss.xml", "農林水産省", "japan", 1),
    ("https://www.meti.go.jp/ml_index_release_atom.xml", "経済産業省", "japan", 2),
    ("https://www.mod.go.jp/j/rss/news.xml", "防衛省", "japan", 1),
    ("https://www.soumu.go.jp/news.rdf", "総務省", "japan", 1),
    ("https://www.mlit.go.jp/pressrelease.rdf", "国土交通省(プレスリリース)", "japan", 2),
    ("https://www.mlit.go.jp/index.rdf", "国土交通省(新着情報)", "japan", 1),
    ("https://www.jpcert.or.jp/rss/jpcert.rdf", "JPCERT/CC", "trade", 2),
    ("https://www.caa.go.jp/news.rss", "消費者庁", "japan", 1),
    ("https://www.ipa.go.jp/about/newsonly-rss.rdf", "IPA(新着情報)", "tech", 1),
    ("https://www.ipa.go.jp/about/press-rss.rdf", "IPA(プレスリリース)", "tech", 2),
    ("https://www.ipa.go.jp/security/alert-rss.rdf", "IPA(セキュリティ注意喚起)", "tech", 2),
]

# ⑤ 一次情報/二次情報/市場解説の信頼度区分(FEEDS経由=Google Newsの記事の
# source名にこれらの語が含まれれば「市場解説(commentary)」とみなす。
# GOV_FEEDS(省庁直接購読)は取得元の時点でprimaryと確定しているため
# ここには含めない(rss.fetch_direct側でsource_tier="primary"を付与する)。
# 現状「企業IR(適時開示等)を直接購読するprimary経路」は無い(バックログ入り)。
# 経験則によるリストで網羅的ではない: 該当しない解説系メディアは
# 従来通りsecondary扱いになる(信頼度を不当に下げるわけではないので実害は無い)。
COMMENTARY_SOURCE_KEYWORDS = [
    "東洋経済", "ダイヤモンド", "会社四季報", "みんかぶ", "株探",
    "トウシル", "ZUUonline", "ZUU online", "Finasee", "モーニングスター",
    "マネーポストWEB", "現代ビジネス",
]

# 市況ヘッダーに出す指標(data.json 由来。取得できない場合は非表示)
MARKET_TICKERS = [
    ("nikkei225", "日経平均"),
    ("nikkei_futures", "日経先物"),
    ("fx", "ドル円"),
    ("us_market.sp500", "S&P500"),
    ("us_market.nasdaq", "ナスダック"),
    ("us_market.sox", "SOX指数"),
]
