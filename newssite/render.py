#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""news.json から index.html(重要ニュース × 影響銘柄サイト)を生成する。

外部ライブラリもビルド工程も使わない素のHTML/CSS/JSを吐くだけなので、
デザインを変えたいときは下の CSS / テンプレート文字列を直接編集すればよい。
"""
import html
import json
from datetime import datetime

from .config import JST

YAHOO_QUOTE = "https://finance.yahoo.co.jp/quote/{code}.T"

DIRECTION_CLASS = {"positive": "up", "negative": "down", "watch": "flat"}
DIRECTION_MARK = {"positive": "▲", "negative": "▼", "watch": "●"}
ORIGIN_CLASS = {"direct": "origin-direct", "llm": "origin-llm", "rule": "origin-rule"}


def esc(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def stars(n):
    n = max(1, min(5, int(n or 1)))
    return "★" * n + "☆" * (5 - n)


def fmt_change(value):
    if value is None:
        return "", "flat"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "", "flat"
    cls = "up" if v > 0 else ("down" if v < 0 else "flat")
    return f"{v:+.2f}%", cls


def _relative(published_at, now):
    if not published_at:
        return ""
    try:
        dt = datetime.strptime(published_at, "%Y-%m-%d %H:%M").replace(tzinfo=JST)
    except ValueError:
        return esc(published_at)
    minutes = int((now - dt).total_seconds() // 60)
    if minutes < 1:
        return "たった今"
    if minutes < 60:
        return f"{minutes}分前"
    if minutes < 24 * 60:
        return f"{minutes // 60}時間前"
    return f"{minutes // (60 * 24)}日前"


def market_bar_html(market):
    if not market:
        return ""
    cells = []
    for m in market:
        text, cls = fmt_change(m.get("change_pct"))
        cells.append(
            f'<div class="ticker"><span class="ticker-label">{esc(m["label"])}</span>'
            f'<span class="ticker-value">{esc(m["value"])}</span>'
            f'<span class="ticker-change {cls}">{esc(text)}</span></div>'
        )
    return f'<div class="market-bar">{"".join(cells)}</div>'


def impact_chip_html(imp):
    cls = DIRECTION_CLASS.get(imp["direction"], "flat")
    mark = DIRECTION_MARK.get(imp["direction"], "●")
    origin = {"direct": "当事者", "llm": "AI補強", "rule": imp.get("theme", "")}.get(imp.get("origin"), "")
    origin_cls = ORIGIN_CLASS.get(imp.get("origin"), "")
    return f"""
        <div class="chip {cls}" data-code="{esc(imp['code'])}">
          <button class="fav-star" type="button" data-fav-code="{esc(imp['code'])}"
                  title="お気に入り登録/解除" aria-label="お気に入り登録/解除">☆</button>
          <button class="chip-head" type="button" data-filter-code="{esc(imp['code'])}" title="この銘柄に関係するニュースだけ表示">
            <span class="chip-mark">{mark}</span>
            <span class="chip-name">{esc(imp['name'])}</span>
            <span class="chip-code">{esc(imp['code'])}</span>
            <span class="chip-strength">影響{esc(imp.get('strength', '中'))}</span>
          </button>
          <div class="chip-body">
            <span class="chip-tag {origin_cls}">{esc(origin)}</span>
            {f'<span class="chip-tag tier-{esc(imp["beneficiary_tier"])}">{esc(imp["beneficiary_tier_label"])}</span>' if imp.get('beneficiary_tier') else ''}
            {f'<span class="chip-tag">⏱ {esc(imp["revenue_horizon_label"])}</span>' if imp.get('revenue_horizon') else ''}
            {f'<span class="chip-tag" title="政策発表→予算確保→受注→設備投資→売上→利益のどこまで来ていそうか(政策の確度×業績到達時期の見積もりから推定。表示専用)">🛤 {esc(imp["policy_to_earnings_stage"])}</span>' if imp.get('policy_to_earnings_stage') else ''}
            {f'<span class="chip-tag" title="政策実現度×受益距離×時間軸×感応度の合成スコア(表示専用、銘柄判定には使っていません)">🎯 {esc(imp["policy_impact_score"])}</span>' if imp.get('policy_impact_score') is not None else ''}
            {f'<span class="chip-tag" title="巨大テックのAI設備投資サイクルが強さの拠り所(政府政策ではなく企業投資が起点。Policy Impact Scoreとは別物、表示専用)">🖥️ {esc(imp["ai_capex_impact_score"])}</span>' if imp.get('ai_capex_impact_score') is not None else ''}
            <span class="chip-reason">{esc(imp.get('reason', ''))}</span>
            <a class="chip-link" href="{esc(YAHOO_QUOTE.format(code=imp['code']))}" target="_blank" rel="noopener">株価 ↗</a>
          </div>
        </div>"""


def emergence_badge_html(obj):
    """[萌芽シグナル] news_card_html/cluster_htmlで共通のバッジ・ツール
    チップを組み立てる(判定はtheme_trends.py側の1箇所のみ、ここは表示
    のみ)。時間軸(初検知日・直近7/30/90日件数)・独立性(独立イベント数・
    情報源数)・情報源の種類(政府/企業/研究機関/国際機関/海外/メディア)・
    国際的な広がり(地域)・成長の経過(milestones)を、単一のスコアに
    合成せず、事実として並べて開示する(ブラックボックス化しない)。
    """
    # emergence_window_countsはtheme_trends.py内では整数キーだが、
    # news.jsonを経由すると(--render-onlyでの再読み込み時など)JSONの
    # 制約で文字列キーになる。どちらの経路でも壊れないよう両対応する。
    windows = obj.get("emergence_window_counts", {}) or {}

    def _window_count(d):
        return windows.get(d, windows.get(str(d)))

    lines = []
    first_seen = obj.get("emergence_first_seen")
    if first_seen:
        lines.append(f"初検知: {first_seen}")
    lines.append(
        f'独立イベント: {obj.get("emergence_event_count", 0)}件'
        f'(情報源{obj.get("emergence_source_count", 0)}件)'
    )
    window_text = " / ".join(
        f"{d}日:{_window_count(d)}件" for d in (7, 30, 90) if _window_count(d) is not None
    )
    if window_text:
        lines.append(f"直近 {window_text}")
    breakdown = obj.get("emergence_actor_type_breakdown", [])
    if breakdown:
        lines.append("情報源の種類: " + "、".join(f'{b["label"]}{b["count"]}' for b in breakdown))
    regions = obj.get("emergence_regions", [])
    if regions:
        lines.append("地域: " + "、".join(regions))
    milestones = obj.get("emergence_milestones", [])
    if milestones:
        lines.append("成長の経過: " + " → ".join(f'{m["date"]} {m["actor_type_label"]}' for m in milestones))
    lines.append("検出シグナル: " + "、".join(obj.get("emergence_signal_labels", [])))
    # 診断(2026-09-20ユーザー要望): 「なぜ0件/次の段階に届いていないのか」
    # を、判定条件(定数)そのものは変えずに開示する。missingが空(=既に
    # その時点で到達しうる最高段階に達している)場合は表示しない。
    diagnosis = obj.get("emergence_diagnosis") or {}
    missing = diagnosis.get("missing") or []
    if missing:
        lines.append(
            f'診断: 独立情報源 {diagnosis.get("source_count", 0)}/{diagnosis.get("required_sources", "?")}、'
            f'シグナル種別 {diagnosis.get("signal_count", 0)}/{diagnosis.get("required_signals", "?")}'
            f'({diagnosis.get("target_stage_label", "")}まで) → ' + "、".join(missing)
        )
    lines.append("将来重要になると予言するものではなく、観測できた事実を示すだけです")
    title = esc("\n".join(lines))
    stage = obj.get("emergence_stage")
    stage_label = obj.get("emergence_stage_label") or "🔬 観測中"
    return (
        f'<span class="emergence-badge" data-stage="{esc(stage or "watching")}" title="{title}">'
        f'{esc(stage_label)}</span>'
    )


def _short_date(iso_date):
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
        return f"{dt.month}/{dt.day}"
    except (ValueError, TypeError):
        return iso_date or ""


def emergence_growth_html(obj):
    """[新興テーマの成長速度] (2026-09-20ユーザー要望)「初検知後、その
    テーマがどう育ってきたか」を、milestones(各情報源種別を最初に観測
    した日)の時系列として開示する。emergence_badge_html同様、単一の
    「成長スコア」や「将来有望度」には合成せず、観測できた生の件数
    (情報源の種類・地域・7/30/90日件数・直近何日で何種類増えたか)を
    並べるだけに留める(原則7参照)。milestonesが1件も無いテーマ(まだ
    どの情報源種別も観測されていない)では何も出さない。
    """
    milestones = obj.get("emergence_milestones") or []
    if not milestones:
        return ""
    windows = obj.get("emergence_window_counts", {}) or {}

    def _window_count(d):
        return windows.get(d, windows.get(str(d)))

    timeline_items = "".join(
        f'<li><span class="growth-date">{esc(_short_date(m["date"]))}</span>'
        f'<span class="growth-actor">{esc(m["actor_type_label"])}</span></li>'
        for m in milestones
    )
    window_text = " / ".join(
        f"{d}日:{_window_count(d)}件" for d in (7, 30, 90) if _window_count(d) is not None
    )
    regions = obj.get("emergence_regions") or []
    region_text = f"（{'、'.join(regions)}）" if regions else ""
    growth = obj.get("emergence_growth") or {}
    recent_note = ""
    if growth.get("recent_new_actor_types"):
        recent_note = (
            f'<p class="growth-recent">直近{esc(growth.get("window_days"))}日で情報源の種類が'
            f'{esc(growth["recent_new_actor_types"])}種類増えました'
            f'(全{esc(growth.get("total_actor_types", 0))}種類中。観測できた事実のみで、将来の重要度を示すものではありません)</p>'
        )
    return f"""
      <details class="emergence-growth">
        <summary>📈 成長の経過を見る</summary>
        <p class="growth-first-seen">初検知: {esc(obj.get("emergence_first_seen", ""))}</p>
        <ul class="growth-timeline">{timeline_items}</ul>
        <p class="growth-stats">情報源 {esc(obj.get("emergence_actor_type_count", 0))}種類・
          地域 {esc(obj.get("emergence_region_count", 0))}地域{esc(region_text)}・
          直近 {esc(window_text)}</p>
        {recent_note}
      </details>"""


def verification_status_html(v):
    """[検証・開発ステータス] (2026-09-21ユーザー要望「今どこまで完成
    しているのか・データが十分に集まったのか・次のバックテスト段階へ
    進める状態になったのか、が一目で分かるようにボード上に自動表示
    してほしい」)。verification_status.pyが計算した値をテンプレートに
    当てはめるだけで、文言を手で書き換えることはしない(本番データに
    応じて自動的に変わる。ユーザー方針:「検証が終わった」という人間向け
    固定文言にしない)。新しい投資判断・スコア・予測は一切作らない。
    """
    if not v:
        return ""
    phase = v.get("phase")
    age_ge = v.get("age_ge", {})
    ms = v.get("milestones", {})

    if phase == "ready_for_backtest":
        emoji, headline = "📊", "萌芽テーマ検証：分析可能"
        lines = [
            "必要な追跡データが蓄積されました。",
            "これまでの初期シグナルと、その後のテーマ拡大・継続・消滅を比較できます。",
            "→ Backtest Ready",
        ]
    elif phase == "backtest_done":
        emoji, headline = "✅", "萌芽テーマ検証：バックテスト完了"
        lines = [
            "初期シグナルと、その後の展開を比較済みです。",
            "次回検証：データ追加後に自動更新",
        ]
    else:
        emoji, headline = "🔬", "萌芽テーマ検証：データ蓄積中"
        lines = [
            f"初検知テーマ：{v.get('total_themes', 0)}件",
            f"30日以上追跡可能：{age_ge.get(30, 0)}件",
            f"7日追跡可能：{age_ge.get(7, 0)}件",
            "現在はデータ蓄積フェーズです。",
            "十分なデータが集まるまで判定ロジックは変更しません。",
        ]

    body_html = "".join(f'<p class="vs-line">{esc(l)}</p>' for l in lines)

    detail_lines = [
        f"テーマ総数: {v.get('total_themes', 0)}件",
        f"経過日数別: 7日以上 {age_ge.get(7, 0)}件 / 14日以上 {age_ge.get(14, 0)}件 / "
        f"30日以上 {age_ge.get(30, 0)}件 / 90日以上 {age_ge.get(90, 0)}件",
        f"独立情報源が複数あるテーマ: {v.get('multi_source_themes', 0)}件",
        f"成長履歴(milestones)が蓄積されているテーマ: {v.get('milestone_themes', 0)}件",
        f"バックテストに使える水準({v.get('trackable_min_days', '-')}日以上経過かつ意味のある信号あり): "
        f"{v.get('trackable_for_backtest', 0)}件 / 目標{v.get('min_trackable_themes_for_backtest', '-')}件",
        f"データ蓄積開始: {ms.get('accumulating', '-')}",
        f"分析可能(Backtest Ready)になった日: {ms.get('ready_for_backtest') or '(まだ)'}",
    ]
    if ms.get("backtest_done"):
        detail_lines.append(f"初回バックテスト完了日: {ms['backtest_done']}")
    detail_html = "".join(f'<div class="vs-detail-line">{esc(l)}</div>' for l in detail_lines)

    return f"""
  <div class="verify-status-panel" data-phase="{esc(phase)}">
    <div class="vs-headline"><span class="vs-emoji">{esc(emoji)}</span> <span class="vs-headline-text">{esc(headline)}</span></div>
    {body_html}
    <details class="vs-more">
      <summary>詳細を見る</summary>
      {detail_html}
      <p class="vs-caveat">これは投資判断や将来予測ではなく、萌芽シグナルのデータ蓄積状況を可視化したものです。この表示にかかわらず判定ロジックは変更していません。</p>
    </details>
  </div>"""


def news_card_html(item, now):
    impacts = item.get("impacts", [])
    if impacts:
        chips = "".join(impact_chip_html(i) for i in impacts)
        pos = sum(1 for i in impacts if i["direction"] == "positive")
        neg = sum(1 for i in impacts if i["direction"] == "negative")
        watch = sum(1 for i in impacts if i["direction"] == "watch")
        counts = []
        if pos:
            counts.append(f'<span class="up">▲追い風 {pos}</span>')
        if neg:
            counts.append(f'<span class="down">▼逆風 {neg}</span>')
        if watch:
            counts.append(f'<span class="flat">●注目 {watch}</span>')
        impact_block = f"""
      <div class="impact-block">
        <div class="impact-head">影響が出うる銘柄 <span class="impact-counts">{" / ".join(counts)}</span></div>
        <div class="chips">{chips}</div>
      </div>"""
    else:
        impact_block = """
      <div class="impact-block empty">このニュースに対応する影響銘柄は、現在のルールでは特定できませんでした。</div>"""

    summary = f'<p class="summary">{esc(item["summary"])}</p>' if item.get("summary") else ""
    comment = (
        f'<p class="comment"><span class="comment-label">想定される波及</span>{esc(item["impact_comment"])}</p>'
        if item.get("impact_comment") else ""
    )
    themes = "".join(f'<span class="theme-tag">#{esc(t)}</span>' for t in item.get("themes", []))
    related = ""
    if item.get("related"):
        links = "".join(
            f'<li><a href="{esc(r["url"])}" target="_blank" rel="noopener">{esc(r["title"])}</a>'
            f'<span class="related-source">{esc(r.get("source", ""))}</span></li>'
            for r in item["related"]
        )
        related = f"""
      <details class="related">
        <summary>同じ話題の他媒体 {len(item['related'])}件</summary>
        <ul>{links}</ul>
      </details>"""

    codes = " ".join(i["code"] for i in impacts)
    names = " ".join(i["name"] for i in impacts)
    search_blob = f"{item['title']} {item.get('source', '')} {' '.join(item.get('themes', []))} {codes} {names}"

    future_badge = (
        '<span class="future-badge" title="審議会・検討会・パブコメなど、報道になる前の一次情報らしき見出しです">'
        '🔮 先行情報</span>'
        if item.get("future_signal") else ""
    )
    maturity_score = item.get("policy_maturity")
    maturity_badge = (
        f'<span class="maturity-badge" data-maturity="{esc(maturity_score)}" '
        f'title="政策の進行段階(0=検討段階〜100=施策実施済み)。表示のみで銘柄判定には影響しません">'
        f'📊 {esc(item.get("policy_maturity_label", ""))} {esc(maturity_score)}</span>'
        if maturity_score is not None else ""
    )
    # ライフサイクル状態。NEW(初出)はほとんどの記事が該当し視覚ノイズに
    # なるため非表示にし、続報以降(UPDATE/MATURED/CLOSED)だけ明示する。
    lifecycle_state = item.get("policy_event_state")
    lifecycle_emoji = {"UPDATE": "🔁", "MATURED": "✅", "CLOSED": "🏁"}.get(lifecycle_state, "")
    lifecycle_label = {"UPDATE": "続報", "MATURED": "制度成立", "CLOSED": "施策実施済み"}.get(lifecycle_state, "")
    lifecycle_badge = (
        f'<span class="lifecycle-badge" data-state="{esc(lifecycle_state)}" '
        f'title="同じ政策の続報を束ねて追跡した進行状況(表示のみで銘柄判定には影響しません)">'
        f'{lifecycle_emoji} {esc(lifecycle_label)}</span>'
        if lifecycle_emoji else ""
    )
    novelty_badge = (
        f'<span class="novelty-badge" data-novelty="{esc(item.get("news_novelty"))}" '
        f'title="似た見出しが過去に記録されている、または複数媒体が同時報道済みです。'
        f'重要でも既に市場に知られている可能性があります(表示のみで銘柄判定には影響しません)">'
        f'♻️ {esc(item.get("news_novelty_label", ""))}</span>'
        if item.get("news_novelty") == "low" else ""
    )
    # 萌芽シグナル(theme_trends.py)。「重要になる」という予言ではなく、
    # 独立したシグナルが何種類観測できたかの事実だけをtitleに列挙する
    # (ブラックボックスにしない)。
    emergence_stage = item.get("emergence_stage")
    emergence_badge = emergence_badge_html(item) if emergence_stage else ""
    # ⑤ 一次情報/二次情報/市場解説。secondary(大多数)は無表示にして視覚的な
    # ノイズを避け、primary(信頼度を上げた)とcommentary(下げた)だけ明示する。
    source_tier = item.get("source_tier")
    source_tier_badge = (
        f'<span class="source-tier-badge tier-{esc(source_tier)}" '
        f'title="情報源の区分。政策インパクトスコアの信頼度に反映済み(一次情報=そのまま/'
        f'二次情報=×0.85/市場解説=×0.65)。表示のみでtheme/direction等の判定には影響しません">'
        f'{"🏛️" if source_tier == "primary" else "💬"} {esc(item.get("source_tier_label", ""))}</span>'
        if source_tier in ("primary", "commentary") else ""
    )
    return f"""
    <article class="news-card" id="news-{esc(item['id'])}" data-category="{esc(item['category'])}" data-importance="{esc(item['importance'])}"
             data-codes="{esc(codes)}" data-search="{esc(search_blob)}" data-ts="{esc(item.get('published_at', ''))}"
             data-future="{'1' if item.get('future_signal') else '0'}"
             data-maturity="{esc(maturity_score) if maturity_score is not None else ''}">
      <div class="news-meta">
        <span class="stars" title="重要度 {esc(item['importance'])} / 5">{stars(item['importance'])}</span>
        <span class="cat">{esc(item.get('category_emoji', '📰'))} {esc(item.get('category_label', ''))}</span>
        {emergence_badge}
        {future_badge}
        {maturity_badge}
        {lifecycle_badge}
        {novelty_badge}
        {source_tier_badge}
        <span class="time">{esc(_relative(item.get('published_at'), now))}</span>
        <span class="source">{esc(item.get('source', ''))}</span>
        <button class="copy-link" type="button" data-url="{esc(item['url'])}" data-title="{esc(item['title'])}"
                title="リンクをコピー" aria-label="リンクをコピー">🔗 コピー</button>
      </div>
      {f'<div class="theme-tags">{themes}</div>' if themes else ''}
      <h3 class="news-title"><a href="{esc(item['url'])}" target="_blank" rel="noopener">{esc(item['title'])}</a></h3>
      {summary}
      {comment}
      <div class="themes"><span class="why" title="重要度の根拠">重要度の根拠: {esc(item.get('importance_reason', ''))}</span></div>
      {impact_block}
      {related}
    </article>"""


def ranking_html(rows):
    if not rows:
        return '<p class="empty">集計できる影響銘柄がありませんでした。</p>'
    out = []
    for i, row in enumerate(rows, 1):
        if row["score"] > 0:
            tone, label = "up", f"追い風 {row['positive']}件"
        elif row["score"] < 0:
            tone, label = "down", f"逆風 {row['negative']}件"
        else:
            tone, label = "flat", f"注目 {row['mentions']}件"
        details = "".join(
            f'<li class="{DIRECTION_CLASS.get(n["direction"], "flat")}">'
            f'<a href="{esc(n["url"])}" target="_blank" rel="noopener">{esc(n["title"])}</a></li>'
            for n in row.get("news", [])
        )
        out.append(f"""
      <div class="rank-row" data-code="{esc(row['code'])}">
        <div class="rank-line">
          <button class="rank-head" type="button" data-filter-code="{esc(row['code'])}"
                  title="この銘柄に関係するニュースだけ表示">
            <span class="rank-no">{i}</span>
            <span class="rank-name">{esc(row['name'])}<span class="rank-code">{esc(row['code'])}</span></span>
            <span class="rank-badge {tone}">{esc(label)}</span>
            <span class="rank-mentions">{row['mentions']}本</span>
          </button>
          <button class="fav-star" type="button" data-fav-code="{esc(row['code'])}"
                  title="お気に入り登録/解除" aria-label="お気に入り登録/解除">☆</button>
          <button class="rank-toggle" type="button" aria-label="関連ニュースの見出しを開く">▾</button>
        </div>
        <ul class="rank-news">{details}</ul>
      </div>""")
    return "".join(out)


def cluster_html(clusters):
    """[つながっている材料] 見出しの文言は違っても同じテーマ(国・地域・資源・
    政策・規制など)を共有するニュースをまとめて表示する。ユーザー要望
    (2026-09-20)「一見異なる内容のニュースでも関連性を発見しやすくしたい」
    への対応。rank_html と同じ .rank-row/.rank-toggle 構造を再利用し、
    既存のJS(クリックで開閉)・CSSにそのまま乗る(新規実装を増やさない)。
    """
    if not clusters:
        return '<p class="empty">今回は複数ニュースにまたがる材料はありませんでした。</p>'
    out = []
    for i, c in enumerate(clusters, 1):
        stock_chips = "".join(
            f'<span class="chip-tag {"origin-direct" if s["positive"] >= s["negative"] else "tier-peripheral"}" '
            f'title="{esc(s["name"])}: 追い風{s["positive"]}件・逆風{s["negative"]}件">'
            f'{esc(s["name"])}</span>'
            for s in c.get("stocks", [])[:5]
        )
        headlines = "".join(
            f'<li><a href="#news-{esc(m["id"])}">{esc(m["title"])}</a></li>'
            for m in c.get("members", [])
        )
        # 萌芽シグナルがまだ「新興/要監視」段階に届いていないテーマ型
        # クラスターにも、診断情報(emergence_diagnosis)がある限りは
        # ニュートラルな🔬チップで開示する(2026-09-20ユーザー要望:
        # 「0件だから何も見せない」ではなく、なぜ0件か検証できるようにする)。
        emergence_label = c.get("emergence_stage_label")
        has_diagnosis = bool(c.get("emergence_diagnosis"))
        emergence_badge = emergence_badge_html(c) if (emergence_label or has_diagnosis) else ""
        # [新興テーマの成長速度] (2026-09-20ユーザー要望)milestonesが
        # あるテーマ型クラスターだけ、初検知後の育ち方を時系列で開ける
        # ようにする(既存の"同じ話題の他媒体"と同じdetails展開パターン)。
        growth_block = emergence_growth_html(c)
        # [UI再設計 2026-09-21] 「この材料を見る」でニュース一覧側を
        # このクラスターの構成記事だけに絞り込む(ユーザー要望「関連材料
        # からニュース一覧へジャンプ」)。判定ロジックには関与せず、
        # 既存のmember idの一覧をdata属性で渡すだけ。
        member_ids = " ".join(esc(m["id"]) for m in c.get("members", []))
        out.append(f"""
      <div class="rank-row cluster-row">
        <div class="rank-line">
          <div class="rank-head">
            <span class="rank-no">{i}</span>
            <span class="rank-name">{esc(c.get('category_emoji', '📰'))} {esc(c['label'])}
              <span class="cluster-type-badge">{esc(c.get('connection_label', ''))}</span>{emergence_badge}</span>
            <span class="rank-mentions">{len(c['members'])}件のニュース</span>
          </div>
          <button class="rank-toggle" type="button" aria-label="関連ニュースの見出しを開く">▾</button>
        </div>
        <div class="cluster-stocks">{stock_chips}</div>
        <button class="see-in-feed" type="button" data-filter-ids="{member_ids}" data-label="{esc(c['label'])}">🔎 この材料を見る</button>
        <ul class="rank-news">{headlines}</ul>{growth_block}
      </div>""")
    return "".join(out)


def category_filter_html(categories, news):
    """[UI再設計 2026-09-21] テーマ数が増えても固定バーが画面を圧迫しない
    よう、「すべて」を除いてMAX_VISIBLE件を超える分は「その他」に畳む
    (ユーザー要望「テーマを探すために画面をスクロールしなくていい状態」)。
    判定ロジックには関与しない、表示順の調整のみ。
    """
    counts = {}
    for n in news:
        counts[n["category"]] = counts.get(n["category"], 0) + 1
    all_chip = ('<button class="filter-chip filter-chip-all is-active" type="button" data-category="all">すべて'
                f'<span class="chip-count">{len(news)}</span></button>')
    visible, overflow = [], []
    MAX_VISIBLE = 8
    for cat in categories:
        count = counts.get(cat["id"], 0)
        if not count:
            continue
        target, cls = (
            (visible, "filter-chip") if len(visible) < MAX_VISIBLE
            else (overflow, "filter-chip filter-chip-overflow")
        )
        target.append(
            f'<button class="{cls}" type="button" data-category="{esc(cat["id"])}">'
            f'{esc(cat.get("emoji", "📰"))} {esc(cat["label"])}<span class="chip-count">{count}</span></button>'
        )
    more_btn = (
        '<button class="filter-chip filter-more" type="button" id="filterMoreToggle" aria-expanded="false">'
        f'その他 <span class="chip-count">{len(overflow)}</span></button>'
        if overflow else ""
    )
    return all_chip + "".join(visible) + more_btn + "".join(overflow)


CSS = """
@property --card{
  syntax:'<color>';
  inherits:true;
  initial-value:rgba(10,26,19,.86);
}
@property --card-2{
  syntax:'<color>';
  inherits:true;
  initial-value:rgba(14,34,25,.86);
}
:root{
  --bg:#010402; --bg-soft:#050f0a; --card:rgba(10,26,19,.86); --card-2:rgba(14,34,25,.86);
  --line:rgba(120,255,180,.28); --text:#f4fff9; --muted:#8fe6b6;
  --up:#ff5d7a; --down:#3fd0ff; --flat:#e0c34a; --accent:#4dff7e; --accent-2:#22d3ee; --accent-3:#a78bfa;
  --accent-4:#ff4fa3; --accent-5:#ffb347; --accent-lime:#c6ff3d;
  animation:cardFill 8s ease-in-out infinite;
  --shadow:0 14px 40px rgba(0,0,0,.7), 0 0 0 1px rgba(57,255,136,.05);
  --glow:0 0 18px rgba(77,255,126,.45);
  --glow-cyan:0 0 16px rgba(34,211,238,.35);
  --glow-violet:0 0 16px rgba(167,139,250,.35);
  --glow-pink:0 0 16px rgba(255,79,163,.35);
  --glow-lime:0 0 18px rgba(198,255,61,.45);
  --glass-blur:blur(22px) saturate(150%);
  --glass-edge:inset 0 1px 0 rgba(255,255,255,.1), inset 0 0 0 1px rgba(57,255,136,.07);
  --font-body:"Noto Sans JP",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  --font-head:"Zen Kaku Gothic New","Noto Sans JP",sans-serif;
  --font-mono:"JetBrains Mono","Noto Sans JP",monospace;
}
@keyframes cardFill{
  0%,100%{--card:rgba(10,26,19,.86); --card-2:rgba(14,34,25,.86);}
  50%{--card:rgba(8,32,38,.86); --card-2:rgba(10,38,44,.86);}
}
:root[data-theme="light"]{
  --bg:#eef7f1; --bg-soft:#ffffff; --card:rgba(255,255,255,.55); --card-2:rgba(235,250,242,.6);
  --line:rgba(4,60,35,.14); --text:#0c211a; --muted:#2e4a3d;
  --up:#c81e3c; --down:#0b6fa8; --flat:#8a6d1f; --accent:#0aa858; --accent-2:#0c8fae; --accent-3:#6d4aff;
  --accent-4:#d6297c; --accent-5:#c97a12; --accent-lime:#7cb500; animation:none;
  --shadow:0 8px 24px rgba(10,40,25,.1);
  --glow:0 0 0 rgba(0,0,0,0);
  --glow-cyan:0 0 0 rgba(0,0,0,0);
  --glow-violet:0 0 0 rgba(0,0,0,0);
  --glow-pink:0 0 0 rgba(0,0,0,0);
  --glow-lime:0 0 0 rgba(0,0,0,0);
  --glass-blur:blur(18px) saturate(140%);
  --glass-edge:inset 0 1px 0 rgba(255,255,255,.5), inset 0 0 0 1px rgba(10,168,88,.06);
}
*{box-sizing:border-box}
html{background:var(--bg)}
body{margin:0;position:relative;isolation:isolate;color:var(--text);
  font-family:var(--font-body);
  line-height:1.7;-webkit-font-smoothing:antialiased;min-height:100vh}
body::before{content:"";position:fixed;inset:-10%;z-index:-2;
  background:
    radial-gradient(36% 30% at 10% 6%, rgba(77,255,126,.16), transparent 60%),
    radial-gradient(26% 24% at 40% 2%, rgba(198,255,61,.09), transparent 60%),
    radial-gradient(28% 26% at 92% 8%, rgba(34,211,238,.10), transparent 60%),
    radial-gradient(32% 30% at 84% 84%, rgba(124,92,255,.11), transparent 60%),
    radial-gradient(26% 26% at 4% 88%, rgba(255,79,163,.08), transparent 60%),
    radial-gradient(24% 24% at 55% 40%, rgba(255,179,71,.05), transparent 60%),
    linear-gradient(160deg, #000201 0%, #000704 45%, #000a06 75%, #000302 100%);
  filter:blur(70px) saturate(125%);
  animation:auroraDrift 26s ease-in-out infinite alternate, hueSwing 9s ease-in-out infinite;
}
@keyframes hueSwing{
  0%{filter:blur(70px) saturate(125%) hue-rotate(0deg)}
  50%{filter:blur(70px) saturate(140%) hue-rotate(24deg)}
  100%{filter:blur(70px) saturate(125%) hue-rotate(0deg)}
}
:root[data-theme="light"] body::before{animation:none}
:root[data-theme="light"] body::before{
  background:
    radial-gradient(38% 32% at 12% 8%, rgba(10,168,88,.28), transparent 60%),
    radial-gradient(32% 30% at 88% 14%, rgba(0,180,190,.16), transparent 60%),
    radial-gradient(34% 32% at 82% 88%, rgba(109,74,255,.12), transparent 60%),
    radial-gradient(26% 26% at 6% 88%, rgba(214,41,124,.09), transparent 60%),
    linear-gradient(160deg, #f4fbf6 0%, #e7f6ec 50%, #dcf3e6 100%);
  filter:blur(50px) saturate(130%);
}
body::after{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;opacity:.5;
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='140' height='140'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/><feColorMatrix type='saturate' values='0'/></filter><rect width='100%25' height='100%25' filter='url(%23n)' opacity='0.35'/></svg>");
  mix-blend-mode:overlay}
:root[data-theme="light"] body::after{opacity:.15;mix-blend-mode:multiply}
@keyframes auroraDrift{
  0%{transform:translate3d(0,0,0) scale(1)}
  50%{transform:translate3d(-2%,1.5%,0) scale(1.04)}
  100%{transform:translate3d(1.5%,-2%,0) scale(1.02)}
}
a{color:inherit}
.up{color:var(--up)} .down{color:var(--down)} .flat{color:var(--flat)}
::selection{background:rgba(57,255,136,.3);color:#04120a}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:rgba(57,255,136,.25);border-radius:8px}
::-webkit-scrollbar-track{background:transparent}

header.site{position:sticky;top:0;z-index:20;background:rgba(4,14,10,.42);
  -webkit-backdrop-filter:var(--glass-blur);backdrop-filter:var(--glass-blur);
  border-bottom:1px solid var(--line);box-shadow:var(--glass-edge),0 8px 24px rgba(0,0,0,.3);
  overflow:hidden}
header.site::before{content:"";position:absolute;inset:0;pointer-events:none;
  background:linear-gradient(90deg,transparent,rgba(57,255,136,.5) 20%,rgba(34,211,238,.6) 50%,rgba(167,139,250,.5) 80%,transparent);
  height:2px;top:auto;bottom:0;opacity:.8}
:root[data-theme="light"] header.site{background:rgba(255,255,255,.5)}
.head-inner{max-width:1240px;margin:0 auto;padding:14px 20px 10px;
  display:flex;gap:16px;align-items:flex-start;justify-content:space-between;flex-wrap:wrap}
.brand h1{margin:0;font-size:24px;font-weight:900;letter-spacing:.05em;font-family:var(--font-head);
  background:linear-gradient(100deg,var(--accent-lime) 0%,var(--accent) 30%,var(--accent-2) 60%,var(--accent-3) 85%,var(--accent-lime) 100%);
  background-size:220% auto;
  -webkit-background-clip:text;background-clip:text;color:transparent;
  filter:drop-shadow(0 0 16px rgba(57,255,136,.4));
  animation:titleShine 7s ease-in-out infinite}
:root[data-theme="light"] .brand h1{filter:none;animation:none}
@keyframes titleShine{0%{background-position:0% 50%}50%{background-position:100% 50%}100%{background-position:0% 50%}}
.brand .eyebrow{font-size:12.5px;letter-spacing:.3em;color:var(--accent-2);text-transform:uppercase;
  font-family:"Orbitron","Noto Sans JP",sans-serif;position:relative;padding-left:14px}
.brand .eyebrow::before{content:"";position:absolute;left:0;top:50%;width:9px;height:9px;
  transform:translateY(-50%);border-left:2px solid var(--accent-2);border-top:2px solid var(--accent-2)}
.brand .sub{font-size:13.5px;color:var(--muted);margin-top:4px;font-variant-numeric:tabular-nums}
.head-actions{display:flex;gap:8px;align-items:center}
.theme-toggle{background:var(--card);border:1px solid var(--line);color:var(--text);
  border-radius:999px;padding:7px 13px;cursor:pointer;font-size:14.5px;transition:box-shadow .2s,border-color .2s}
.theme-toggle:hover{border-color:var(--accent);box-shadow:var(--glow)}

.market-bar{display:flex;gap:10px;overflow-x:auto;max-width:1240px;margin:0 auto;
  padding:0 20px 12px;scrollbar-width:thin}
.ticker{display:flex;gap:8px;align-items:baseline;background:var(--card);border:1px solid var(--line);
  -webkit-backdrop-filter:var(--glass-blur);backdrop-filter:var(--glass-blur);box-shadow:var(--glass-edge);
  border-radius:10px;padding:6px 12px;white-space:nowrap;font-size:13.5px}
.ticker-label{color:var(--muted);font-family:var(--font-head);font-size:12.5px}
.ticker-value{font-weight:700;font-family:var(--font-mono);font-variant-numeric:tabular-nums}
.ticker-change{font-weight:700;font-size:13.5px;font-family:var(--font-mono);font-variant-numeric:tabular-nums}

.wrap{max-width:1240px;margin:0 auto;padding:20px}
.notice{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--accent);
  -webkit-backdrop-filter:var(--glass-blur);backdrop-filter:var(--glass-blur);box-shadow:var(--glass-edge);
  border-radius:12px;padding:12px 16px;font-size:14px;color:var(--muted);margin-bottom:18px}
.notice b{color:var(--text)}
.notice.sample-banner{border-left-color:var(--flat);color:var(--text)}

/* [検証・開発ステータス] (2026-09-21) 既存の.noticeより控えめに
   (font-sizeを1段小さく・paddingを詰める)。「Aurora Mesh」の
   青(--accent-2)〜紫(--accent-3)グラデーションで、ニュース/銘柄カードの
   緑系アクセントとは意図的に区別しつつ、既存トークン(--card/--line/
   --glass-blur/--glass-edge)は再利用して統一感を保つ。 */
.verify-status-panel{
  position:relative;background:var(--card-2);
  border:1px solid rgba(167,139,250,.28);border-radius:12px;
  padding:9px 15px;margin-bottom:16px;font-size:12.5px;color:var(--muted);
  -webkit-backdrop-filter:var(--glass-blur);backdrop-filter:var(--glass-blur);
  box-shadow:var(--glass-edge), 0 0 22px rgba(34,211,238,.08);
  overflow:hidden;
}
.verify-status-panel::before{
  content:"";position:absolute;inset:0;pointer-events:none;border-radius:inherit;
  background:linear-gradient(120deg, rgba(34,211,238,.10), transparent 40%, rgba(167,139,250,.10));
}
.verify-status-panel[data-phase="ready_for_backtest"]{
  border-color:rgba(77,255,126,.32);box-shadow:var(--glass-edge), 0 0 22px rgba(77,255,126,.12);
}
.verify-status-panel[data-phase="backtest_done"]{
  border-color:rgba(198,255,61,.32);box-shadow:var(--glass-edge), 0 0 22px rgba(198,255,61,.12);
}
.verify-status-panel .vs-headline{position:relative;font-family:var(--font-head);font-weight:700;font-size:13.5px;margin-bottom:3px}
.verify-status-panel .vs-emoji{filter:none}
.verify-status-panel .vs-headline-text{
  background:linear-gradient(90deg, var(--accent-2), var(--accent-3));
  -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;
}
.verify-status-panel[data-phase="ready_for_backtest"] .vs-headline-text{
  background:linear-gradient(90deg, var(--accent), var(--accent-2));
  -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;
}
.verify-status-panel[data-phase="backtest_done"] .vs-headline-text{
  background:linear-gradient(90deg, var(--accent-lime), var(--accent));
  -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;
}
.verify-status-panel .vs-line{position:relative;margin:1px 0}
.verify-status-panel .vs-more{position:relative;margin-top:5px}
.verify-status-panel .vs-more summary{cursor:pointer;color:var(--accent-2);font-size:12px;list-style:none}
.verify-status-panel .vs-more summary::-webkit-details-marker{display:none}
.verify-status-panel .vs-more summary::before{content:"▸ ";display:inline-block}
.verify-status-panel .vs-more[open] summary::before{content:"▾ "}
.verify-status-panel .vs-detail-line{margin:3px 0;padding-left:8px;border-left:2px solid rgba(167,139,250,.25);font-size:12px}
.verify-status-panel .vs-caveat{margin-top:7px;font-size:11px;opacity:.7;font-style:italic}
:root[data-theme="light"] .verify-status-panel{background:rgba(255,255,255,.55)}

.controls{position:sticky;top:74px;z-index:15;background:rgba(4,14,10,.32);
  -webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);padding:10px 0 12px;
  border-bottom:1px solid var(--line);margin-bottom:18px}
:root[data-theme="light"] .controls{background:rgba(255,255,255,.4)}
.filter-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.filter-chip{background:var(--card);border:1px solid var(--line);color:var(--text);border-radius:999px;
  -webkit-backdrop-filter:var(--glass-blur);backdrop-filter:var(--glass-blur);
  padding:5px 11px;font-size:13px;cursor:pointer;display:inline-flex;gap:5px;align-items:center;
  transition:box-shadow .2s,border-color .2s}
.filter-chip:hover{border-color:var(--accent);box-shadow:var(--glow)}
.filter-chip:not(.is-active):nth-of-type(5n+2){border-left:3px solid var(--accent-2)}
.filter-chip:not(.is-active):nth-of-type(5n+3){border-left:3px solid var(--accent-3)}
.filter-chip:not(.is-active):nth-of-type(5n+4){border-left:3px solid var(--accent-4)}
.filter-chip:not(.is-active):nth-of-type(5n+5){border-left:3px solid var(--accent-5)}
.filter-chip:not(.is-active):nth-of-type(5n+6){border-left:3px solid var(--accent-lime)}
.filter-chip.is-active{background:linear-gradient(100deg,var(--accent-lime),var(--accent),var(--accent-lime));
  background-size:220% auto;animation:duoFill 5s ease-in-out infinite;
  border-color:var(--accent);color:#04140b;font-weight:700;box-shadow:var(--glow)}
@keyframes duoFill{0%{background-position:0% 50%}50%{background-position:100% 50%}100%{background-position:0% 50%}}
:root[data-theme="light"] .filter-chip.is-active{animation:none}
.chip-count{opacity:.95;font-size:12px;font-family:var(--font-mono)}
/* [UI再設計 2026-09-21] 「すべて」は折り返し対象外で常に先頭固定。
   テーマ数が増えてMAX_VISIBLEを超えた分は「その他」の裏に隠し、
   トグルで展開する(category_filter_html()参照)。 */
.filter-chip-all{flex-shrink:0}
.filter-chip-overflow{display:none}
.filter-row.is-expanded .filter-chip-overflow{display:inline-flex}
.filter-more{opacity:.85;border-style:dashed}
.search-row{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
.search-row input,.search-row select{background:var(--card);border:1px solid var(--line);color:var(--text);
  -webkit-backdrop-filter:var(--glass-blur);backdrop-filter:var(--glass-blur);
  border-radius:10px;padding:8px 12px;font-size:14.5px}
.search-row input:focus,.search-row select:focus{outline:none;border-color:var(--accent);box-shadow:var(--glow)}
.search-row input{flex:1;min-width:200px}
.active-filter{display:none;align-items:center;gap:8px;font-size:14px;color:var(--muted)}
.active-filter.is-on{display:inline-flex}
.active-filter button{background:transparent;border:1px solid var(--line);color:var(--text);
  border-radius:999px;padding:3px 10px;cursor:pointer;font-size:13.5px}
/* [UI再設計 2026-09-21] 「つながっている材料」→ニュース一覧への絞り込み。
   既存の.filter-chipより控えめな見た目にする(サイドパネル内なので
   主役のニュース一覧より目立たせない)。 */
.see-in-feed{margin-top:8px;background:transparent;border:1px solid rgba(34,211,238,.35);
  color:var(--accent-2);border-radius:999px;padding:4px 12px;font-size:12.5px;cursor:pointer;
  transition:box-shadow .2s,border-color .2s}
.see-in-feed:hover{border-color:var(--accent-2);box-shadow:var(--glow-cyan)}
.see-in-feed.is-active{background:var(--accent-2);color:#04140b;font-weight:700;border-color:var(--accent-2)}
.fav-toggle.is-active{background:linear-gradient(135deg,var(--flat),var(--accent-5));color:#241a02;
  border-color:var(--flat);animation:none}
.future-toggle.is-active{background:linear-gradient(135deg,var(--accent-3),var(--accent-2));color:#0a0620;
  border-color:var(--accent-3);animation:none}
.future-badge{background:linear-gradient(135deg,var(--accent-3),var(--accent-2));color:#0a0620;
  border-radius:999px;padding:1px 10px;font-size:12px;font-weight:700;box-shadow:var(--glow-violet);
  white-space:nowrap}
.maturity-badge{background:var(--card-2);border:1px solid var(--line);color:var(--muted);
  border-radius:999px;padding:1px 10px;font-size:12px;white-space:nowrap;font-family:var(--font-mono)}
.novelty-badge{background:var(--card-2);border:1px solid rgba(224,195,74,.4);color:var(--flat);
  border-radius:999px;padding:1px 10px;font-size:12px;white-space:nowrap}
.source-tier-badge{border-radius:999px;padding:1px 10px;font-size:12px;white-space:nowrap;background:var(--card-2)}
.source-tier-badge.tier-primary{border:1px solid rgba(77,255,126,.4);color:var(--accent)}
.source-tier-badge.tier-commentary{border:1px solid rgba(224,195,74,.4);color:var(--flat)}
.lifecycle-badge{background:var(--card-2);border:1px solid var(--line);color:var(--muted);
  border-radius:999px;padding:1px 10px;font-size:12px;white-space:nowrap}
.lifecycle-badge[data-state="MATURED"]{border-color:rgba(77,255,126,.4);color:var(--accent)}
.lifecycle-badge[data-state="CLOSED"]{border-color:rgba(167,139,250,.4);color:var(--accent-3)}
.emergence-badge{background:var(--card-2);border:1px solid rgba(57,255,136,.4);color:var(--accent);
  border-radius:999px;padding:1px 10px;font-size:12px;white-space:nowrap;font-weight:700}
.emergence-badge[data-stage="watch"]{border-color:rgba(34,211,238,.4);color:var(--accent-2)}
.emergence-badge[data-stage="watching"]{border-color:rgba(148,163,184,.35);color:var(--muted);font-weight:600}

.scroll-progress{position:fixed;top:0;left:0;height:3px;width:0%;z-index:30;
  background:linear-gradient(90deg,var(--accent-lime),var(--accent),var(--accent-2),var(--accent-3));
  box-shadow:0 0 10px rgba(77,255,126,.6);transition:width .12s linear}

.fav-star{background:transparent;border:0;color:var(--muted);cursor:pointer;font-size:15.5px;
  line-height:1;padding:4px;border-radius:6px;transition:color .15s,transform .15s}
.fav-star:hover{color:var(--flat);transform:scale(1.15)}
.fav-star.is-fav{color:var(--flat);text-shadow:0 0 10px rgba(224,195,74,.6)}
.chip{position:relative}
.chip .fav-star{position:absolute;top:4px;right:6px;z-index:2}
.chip-head{padding-right:26px}

.copy-link{margin-left:auto;background:transparent;border:1px solid var(--line);color:var(--muted);
  border-radius:999px;padding:2px 9px;font-size:12.5px;cursor:pointer;transition:border-color .15s,color .15s}
.copy-link:hover{border-color:var(--accent);color:var(--accent)}
.copy-link.is-copied{border-color:var(--accent);color:var(--accent)}

:is(a,button,input,select,summary):focus-visible{outline:2px solid var(--accent-2);outline-offset:2px;
  box-shadow:var(--glow-cyan)}

@media(prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.001ms!important;animation-iteration-count:1!important;
    transition-duration:.001ms!important;scroll-behavior:auto!important}
}

.layout{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:22px;align-items:start}
@media(max-width:960px){.layout{grid-template-columns:1fr}.controls{top:0}}

/* [UI再設計 2026-09-21] 「固定UI/ニュース一覧/分析パネル」の3層分離。
   961px以上だけ、ページ全体スクロールをやめて「ニュース一覧」と
   「右側分析パネル」をそれぞれ独立スクロールにするapp-shell構造に
   切り替える(960px以下は既存のsticky方式のフォールバックのまま=
   このブロックより上の既存ルールを変更しない)。 */
@media(min-width:961px){
  #desktop-view{height:100vh;display:flex;flex-direction:column;overflow:hidden}
  header.site{position:static;flex-shrink:0}
  .controls{position:static;flex-shrink:0}
  /* [固定枠の圧縮 2026-09-21] ユーザー報告「ニュース項目で見える範囲が
     狭い」への対応。header/market-bar/controlsが独立スクロールの外に
     固定されたことで専有面積の影響が相対的に大きくなったため、961px
     以上だけ縦の余白・文字サイズを詰める(960px以下のフォールバックは
     このブロックの外なので無変更)。brand行はeyebrow/h1/subの3行縦積みを
     1行の横並びに変え、判定ロジック・機能は一切変えていない。 */
  .head-inner{padding:5px 20px 4px;align-items:center}
  .brand{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
  .brand h1{font-size:17px}
  .brand .eyebrow{display:none}
  .brand .sub{margin-top:0;font-size:12.5px}
  .theme-toggle{padding:5px 11px;font-size:13px}
  .market-bar{padding:0 20px 5px}
  .ticker{padding:3px 8px;font-size:11.5px}
  .ticker-label{font-size:11px}
  .ticker-change{font-size:11.5px}
  .controls{padding:4px 0 6px;margin-bottom:8px}
  .filter-chip{padding:3px 8px;font-size:11.5px}
  .chip-count{font-size:10.5px}
  .search-row{margin-top:4px}
  .search-row input,.search-row select{padding:5px 9px;font-size:13.5px}
  .wrap{display:flex;flex-direction:column;flex:1;min-height:0;padding-bottom:0;overflow:hidden}
  /* align-items:stretch(既定のalign-items:startを上書き)しないと、
     グリッドの行が中身(=全ニュース件数ぶんの高さ)に合わせて伸びてしまい、
     mainのheight:100%が意味を持たない(実測: これが無いとページ全体が
     縦に伸びきり、独立スクロールにならなかった)。 */
  .layout{flex:1;min-height:0;height:100%;align-items:stretch;grid-template-rows:minmax(0,1fr)}
  main{height:100%;overflow-y:auto;padding-right:6px;scrollbar-width:thin}
  main::-webkit-scrollbar{width:8px}
  main::-webkit-scrollbar-thumb{background:rgba(57,255,136,.25);border-radius:8px}
  aside.side{position:static;max-height:none;height:100%}
}

.news-card{background:var(--card);border:1px solid var(--line);border-radius:16px;
  -webkit-backdrop-filter:var(--glass-blur);backdrop-filter:var(--glass-blur);
  padding:18px 20px;margin-bottom:16px;box-shadow:var(--shadow),var(--glass-edge);
  transition:box-shadow .2s,border-color .2s}
.news-card:hover{border-color:var(--accent);box-shadow:var(--shadow),var(--glass-edge),var(--glow)}
.news-card[data-importance="5"]{border-left:4px solid var(--accent-4);box-shadow:var(--shadow),var(--glass-edge),var(--glow-pink)}
.news-card[data-importance="4"]{border-left:4px solid var(--accent-2);box-shadow:var(--shadow),var(--glass-edge),var(--glow-cyan)}
.news-meta{display:flex;flex-wrap:wrap;gap:10px;align-items:center;font-size:13.5px;color:var(--muted)}
.stars{color:#f2c744;letter-spacing:1px}
.cat{background:var(--card-2);border:1px solid var(--line);border-radius:999px;padding:2px 10px}
.news-title{margin:8px 0 6px;font-size:18.5px;line-height:1.55;font-family:var(--font-head);font-weight:700}
.news-title a{text-decoration:none}
.news-title a:hover{text-decoration:underline;color:var(--accent)}
.summary{margin:6px 0;font-size:15px;color:var(--text)}
.comment{margin:6px 0;font-size:14.5px;color:var(--muted);background:var(--card-2);
  border-radius:10px;padding:9px 12px}
.comment-label{display:inline-block;font-size:12.5px;color:var(--accent);margin-right:8px;font-weight:700}
.themes{display:flex;flex-wrap:wrap;gap:7px;margin:8px 0 4px;font-size:13px;color:var(--muted)}
.theme-tag{background:var(--card-2);border:1px solid var(--line);border-radius:6px;padding:1px 8px}
/* [UI再設計 2026-09-21] テーマタグをカード上部(news-metaの直後)に移動し、
   要約より前に一目で見えるようにする(ユーザー要望「重要度・先行情報・
   テーマ・情報源・時間・影響銘柄をカード上部で瞬時に把握」)。 */
.theme-tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.theme-tags .theme-tag{font-size:12px}
.why{opacity:.95}

.impact-block{margin-top:12px;border-top:1px dashed var(--line);padding-top:12px}
.impact-block.empty{font-size:14px;color:var(--muted)}
.impact-head{font-size:14px;font-weight:700;margin-bottom:9px;display:flex;gap:10px;flex-wrap:wrap}
.impact-counts{font-weight:600;font-size:13.5px;display:flex;gap:10px}
.chips{display:grid;grid-template-columns:repeat(auto-fill,minmax(226px,1fr));gap:8px}
.chip{border:1px solid var(--line);border-radius:12px;background:var(--card-2);overflow:hidden;
  transition:box-shadow .2s,border-color .2s}
.chip:hover{border-color:var(--accent);box-shadow:var(--glow)}
.chip.up{border-left:3px solid var(--up)} .chip.down{border-left:3px solid var(--down)}
.chip.flat{border-left:3px solid var(--flat)}
.chip-head{width:100%;display:flex;gap:7px;align-items:baseline;background:transparent;border:0;
  color:var(--text);padding:8px 10px 4px;cursor:pointer;text-align:left;font-size:14.5px}
.chip-head:hover .chip-name{text-decoration:underline}
.chip.up .chip-mark{color:var(--up)} .chip.down .chip-mark{color:var(--down)}
.chip.flat .chip-mark{color:var(--flat)}
.chip-name{font-weight:700;font-family:var(--font-head)}
.chip-code{font-size:12.5px;color:var(--muted);font-family:var(--font-mono)}
.chip-strength{margin-left:auto;font-size:12px;color:var(--muted);white-space:nowrap}
.chip-body{padding:0 10px 9px;font-size:13px;color:var(--muted);display:flex;flex-wrap:wrap;gap:6px}
.chip-tag{background:var(--card);border:1px solid var(--line);border-radius:5px;padding:0 6px;font-size:12px}
.chip-tag.origin-direct{border-color:rgba(34,211,238,.5);color:var(--accent-2)}
.chip-tag.origin-llm{border-color:rgba(167,139,250,.5);color:var(--accent-3)}
.chip-tag.origin-rule{border-color:rgba(57,255,136,.4);color:var(--accent)}
.chip-tag.tier-direct{border-color:rgba(77,255,126,.5);color:var(--accent)}
.chip-tag.tier-primary{border-color:rgba(34,211,238,.5);color:var(--accent-2)}
.chip-tag.tier-secondary{border-color:rgba(255,179,71,.5);color:var(--accent-5)}
.chip-tag.tier-peripheral{border-color:var(--line);color:var(--muted)}
.chip-reason{flex:1 1 100%;line-height:1.55;color:var(--text)}
.chip-link{color:var(--accent);text-decoration:none;font-size:12.5px}

.related{margin-top:10px;font-size:13.5px;color:var(--muted)}
.related summary{cursor:pointer}
.related ul{margin:8px 0 0;padding-left:18px}
.related li{margin-bottom:4px}
.related-source{margin-left:8px;font-size:12.5px;opacity:.95}
.emergence-growth{margin-top:10px;font-size:13.5px;color:var(--muted)}
.emergence-growth summary{cursor:pointer}
.growth-first-seen{margin:8px 0 4px}
.growth-timeline{list-style:none;margin:0 0 8px;padding:0;border-left:2px solid var(--line)}
.growth-timeline li{position:relative;padding:2px 0 2px 14px;font-variant-numeric:tabular-nums}
.growth-timeline li::before{content:"●";position:absolute;left:-6px;font-size:9px;color:var(--accent)}
.growth-date{font-weight:700;color:var(--text);margin-right:8px}
.growth-stats{margin:4px 0}
.growth-recent{margin:6px 0 0;color:var(--accent);font-weight:600}

aside.side{position:sticky;top:150px;max-height:calc(100vh - 170px);
  overflow-y:auto;overflow-x:hidden;padding-right:4px;scrollbar-width:thin}
aside.side::-webkit-scrollbar{width:8px}
aside.side::-webkit-scrollbar-thumb{background:rgba(57,255,136,.25);border-radius:8px}
@media(max-width:960px){aside.side{position:static;max-height:none;overflow-y:visible}}
.panel{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px 16px 8px;
  -webkit-backdrop-filter:var(--glass-blur);backdrop-filter:var(--glass-blur);
  box-shadow:var(--shadow),var(--glass-edge);margin-bottom:16px}
.panel h2{margin:0 0 4px;font-size:16.5px;font-family:var(--font-head);font-weight:900;letter-spacing:.02em}
.panel .panel-desc{margin:0 0 12px;font-size:13px;color:var(--muted)}
.rank-row{border-top:1px solid var(--line)}
.rank-row:first-of-type{border-top:0}
.rank-line{display:flex;align-items:center;gap:4px}
.rank-toggle{background:transparent;border:0;color:var(--muted);cursor:pointer;font-size:13.5px;
  padding:6px 4px;border-radius:6px}
.rank-toggle:hover{color:var(--accent)}
.rank-row.is-open .rank-toggle{transform:rotate(180deg)}
.rank-head{flex:1;display:flex;gap:9px;align-items:center;background:transparent;border:0;color:var(--text);
  padding:9px 2px;cursor:pointer;text-align:left;font-size:14.5px}
.rank-head:hover .rank-name{color:var(--accent)}
.rank-no{width:20px;font-size:12.5px;color:var(--muted)}
.rank-name{flex:1;font-weight:600}
.rank-code{display:block;font-size:12px;color:var(--muted);font-weight:400}
.rank-badge{font-size:12.5px;white-space:nowrap}
.rank-mentions{font-size:12.5px;color:var(--muted)}
.rank-news{display:none;margin:0 0 10px;padding-left:30px;font-size:13px}
.rank-row.is-open .rank-news{display:block}
.rank-news li{margin-bottom:4px}
.rank-news a{color:var(--muted);text-decoration:none}
.rank-news a:hover{color:var(--accent)}
.cluster-row .rank-head{cursor:default}
.cluster-stocks{display:none;flex-wrap:wrap;gap:5px;padding-left:30px;margin-bottom:6px}
.cluster-row.is-open .cluster-stocks{display:flex}
.cluster-type-badge{display:inline-block;margin-left:7px;font-size:10.5px;font-weight:400;
  color:var(--muted);border:1px solid var(--line);border-radius:5px;padding:0 5px;vertical-align:1px}

.empty,.no-result{background:var(--card);border:1px dashed var(--line);border-radius:14px;
  -webkit-backdrop-filter:var(--glass-blur);backdrop-filter:var(--glass-blur);
  padding:20px;text-align:center;color:var(--muted);font-size:14.5px}
.no-result{display:none}
footer{margin-top:28px;padding:20px 0 40px;border-top:1px solid var(--line);font-size:13px;color:var(--muted)}
footer a{color:var(--accent)}
.back-top{position:fixed;right:18px;bottom:18px;width:42px;height:42px;border-radius:50%;
  border:1px solid var(--line);background:var(--card);color:var(--text);cursor:pointer;display:none;
  -webkit-backdrop-filter:var(--glass-blur);backdrop-filter:var(--glass-blur);
  box-shadow:var(--shadow),var(--glass-edge);font-size:17.5px;transition:box-shadow .2s,border-color .2s}
.back-top.is-on{display:block}
.back-top:hover{border-color:var(--accent);box-shadow:var(--shadow),var(--glow)}

/* ================================================================
   モバイル専用UI（PWA・3画面構成、ユーザー方針2026-09-19）
   サイバーネオン×ミニマルガラス。株ボード(jp-stock-dashboard)と
   同じデザイン言語(SVGラインアイコン+ネオングロー+ガラスパネル)。
   520px以下では#desktop-viewを隠し#mobile-appに完全に切り替える。
   ================================================================ */
#mobile-app{display:none}
@media(max-width:520px){
  #desktop-view{display:none}
  #mobile-app{display:block}
}
/* スモークガラス・トークン(Figmaで検証した配色を移植、2026-09-19)。
   中立白ではなく青紫がかったティント(--glass-tint)で「未来的な冷たい
   ガラス」の質感を出す。背景も暗めのAurora Meshに(株ボードと統一)。 */
#mobile-app{
  --glass-tint:168,184,255;
  --glass-bg:rgba(var(--glass-tint),.06); --glass-border:rgba(var(--glass-tint),.24);
  --glass-highlight:inset 0 1px 0 rgba(255,255,255,.08);
  min-height:100vh; padding-bottom:80px;
  font-family:var(--font-body); color:#fff;
  /* 黒×多色の融合を強める（ユーザー要望2026-09-19）: 漆黒ベースの面積を
     広く保ちつつ、彩度の高い色帯を増やして黒との対比でカラフルに見せる。 */
  background:
    radial-gradient(46% 26% at 16% 2%, rgba(157,143,255,.30), transparent 66%),
    radial-gradient(50% 28% at 86% 10%, rgba(63,224,245,.26), transparent 66%),
    radial-gradient(46% 28% at 90% 78%, rgba(240,138,212,.22), transparent 64%),
    radial-gradient(40% 24% at 6% 86%, rgba(255,203,112,.14), transparent 60%),
    radial-gradient(34% 20% at 50% 46%, rgba(180,237,74,.08), transparent 58%),
    radial-gradient(38% 22% at 62% 68%, rgba(255,133,149,.08), transparent 58%),
    linear-gradient(180deg,#020309 0%,#050814 34%,#0a0e24 62%,#111a3d 100%);
}
/* ガラス板上端のsheen(反射ライン)。全ガラスカード共通。 */
.m-row::before, .m-stat::before, .m-cta::before{
  content:""; position:absolute; top:0; left:8%; right:8%; height:1.5px; pointer-events:none;
  background:linear-gradient(90deg, transparent, rgba(255,255,255,.55), transparent);
}
.m-topbar{
  position:sticky; top:0; z-index:20; display:flex; justify-content:space-between; align-items:center;
  padding:14px 16px; background:rgba(2,3,9,.6); backdrop-filter:blur(22px) saturate(180%);
  -webkit-backdrop-filter:blur(22px) saturate(180%);
  border-bottom:1px solid var(--glass-border); box-shadow:var(--glass-highlight);
}
.m-brand{font-weight:800; font-size:16px; font-family:var(--font-head); display:flex; align-items:center; gap:8px}
.m-updated{font-size:11px; color:var(--muted); font-family:var(--font-mono); opacity:.85}
.m-screens{padding:16px 14px 8px}
.m-screen{display:none}
.m-screen.is-active{display:block}
.m-h2{font-size:12.5px; margin:22px 0 10px; color:var(--muted); letter-spacing:.09em; text-transform:uppercase;
  font-family:var(--font-mono); display:flex; align-items:center; gap:7px}
.m-h2:first-child{margin-top:4px}
.m-h2-icon, .m-cta-icon{width:15px; height:15px; flex-shrink:0; stroke:currentColor}
.m-brand .m-h2-icon{width:19px; height:19px; color:var(--accent-2)}
.m-cta-icon{width:16px; height:16px; margin-right:6px; vertical-align:-3px}
.m-h2.accent-cyan{color:var(--accent-2)}
.m-h2.accent-amber{color:#ffcb70}
.m-h2.accent-violet{color:#b9aeff}
.m-h2.accent-magenta{color:#f0a8dd}
.m-h2-count{margin-left:auto;font-weight:400;opacity:.8}

/* [UI再設計 2026-09-21] HOME上部の検索/絞り込み解除(コンパクトな
   固定領域。詳細フィルターはPC版に譲り、まずは検索とワンタップ解除だけ)。 */
.m-search-row{position:sticky;top:0;z-index:5;background:var(--bg);padding:6px 0 4px;margin:-4px 0 0}
.m-search-input{width:100%;background:var(--glass-bg);border:1px solid var(--glass-border);color:#fff;
  border-radius:12px;padding:10px 14px;font-size:14px;-webkit-backdrop-filter:blur(20px);backdrop-filter:blur(20px)}
.m-search-input:focus{outline:none;border-color:var(--accent-2)}
.m-active-filter{display:none;align-items:center;gap:8px;font-size:12.5px;color:var(--muted);margin-top:8px}
.m-active-filter.is-on{display:flex}
.m-active-filter #mActiveFilterText{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.m-active-filter button{background:transparent;border:1px solid var(--glass-border);color:#fff;
  border-radius:999px;padding:3px 10px;font-size:12px;cursor:pointer;flex-shrink:0}
.m-see-in-feed{display:block;width:100%;text-align:center;margin-bottom:10px;padding:9px 12px;font-size:13px}

.m-list{display:flex; flex-direction:column; gap:8px}
/* 枠デザイン改良（ユーザー要望2026-09-19、株ボードと統一）:
   左端にカラーアクセントバー、右下角を落としたチケット風シェイプ。 */
.m-row{
  position:relative;
  display:flex; justify-content:space-between; align-items:center; gap:10px;
  background:var(--glass-bg); backdrop-filter:blur(20px) saturate(200%); -webkit-backdrop-filter:blur(20px) saturate(200%);
  border:1px solid var(--glass-border); box-shadow:var(--glass-highlight), 0 12px 32px -12px rgba(0,0,0,.55);
  border-radius:14px 14px 14px 4px; padding:13px 14px 13px 16px; text-decoration:none; color:#fff; min-height:44px;
  transition:border-color .15s ease, background .15s ease, transform .1s ease;
  overflow:hidden;
}
.m-row::after{
  content:""; position:absolute; top:10px; bottom:10px; left:0; width:3px; border-radius:0 3px 3px 0;
  background:var(--muted); opacity:.5;
}
.m-row:has(.m-chg.up)::after{background:var(--accent); box-shadow:0 0 8px rgba(77,255,126,.6); opacity:1}
.m-row:has(.m-chg.down)::after{background:var(--up); box-shadow:0 0 8px rgba(255,93,122,.6); opacity:1}
.m-row:active{background:rgba(34,211,238,.08); border-color:rgba(34,211,238,.35); transform:scale(.985)}
.m-row-main{display:flex; flex-direction:column; gap:4px; min-width:0; flex:1}
.m-row-cat{font-size:11px; color:var(--muted)}
.m-emergence-chip{margin-left:6px;padding:1px 7px;border-radius:999px;font-weight:700;
  background:var(--card-2);border:1px solid rgba(57,255,136,.4);color:var(--accent)}
.m-emergence-chip[data-stage="watch"]{border-color:rgba(34,211,238,.4);color:var(--accent-2)}
.m-emergence-chip[data-stage="watching"]{border-color:rgba(148,163,184,.35);color:var(--muted);font-weight:600}
.m-row-title{font-size:14.5px; font-weight:700; line-height:1.4;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden}
.m-row-sub{font-size:12px; color:var(--accent); letter-spacing:.06em}
.m-row-sub .up, .m-row-sub .down, .m-row-sub .flat{margin-left:7px; font-weight:700}
.m-row-sub .up{color:var(--accent)} .m-row-sub .down{color:var(--up)} .m-row-sub .flat{color:var(--flat)}
.m-code-chip{display:inline-block; margin-left:6px; font-family:var(--font-mono); font-size:10.5px; color:var(--muted)}
/* [UI再設計 2026-09-21] 材料が集まっている銘柄→ニュース一覧への絞り込み用に
   <div>から<button>に変えたので、ボタンのデフォルト見た目だけ打ち消す
   (レイアウトは既存の.m-rowをそのまま使う)。 */
.m-rank-row-btn{font:inherit;text-align:left;cursor:pointer;width:100%;-webkit-appearance:none;appearance:none}

/* ニュース行は展開式: タップで影響銘柄の内訳をその場に開く(PC版へ飛ばさない、2026-09-19) */
.m-news-row{flex-direction:column; align-items:stretch; padding:13px 14px 13px 16px}
.m-row-head{display:flex; justify-content:space-between; align-items:center; gap:10px; width:100%;
  background:none; border:0; padding:0; margin:0; color:inherit; font:inherit; text-align:left; cursor:pointer; min-height:44px}
.m-row-chevron{width:14px; height:14px; flex-shrink:0; color:var(--muted); transition:transform .18s ease}
.m-news-row.is-expanded .m-row-chevron{transform:rotate(180deg)}
.m-impact-panel{display:none; flex-direction:column; gap:7px; margin-top:11px; padding-top:11px; border-top:1px dashed var(--glass-border)}
.m-news-row.is-expanded .m-impact-panel{display:flex}
.m-impact-item{display:flex; align-items:center; gap:8px; font-size:12.5px}
.m-impact-item .mark{width:13px; flex-shrink:0; text-align:center}
.m-impact-item.up .mark{color:var(--accent)} .m-impact-item.down .mark{color:var(--up)} .m-impact-item.flat .mark{color:var(--flat)}
.m-impact-item .name{flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#fff}
.m-impact-item .code{font-family:var(--font-mono); font-size:11px; color:var(--muted); flex-shrink:0}
.m-cluster-item .name{white-space:normal; line-height:1.5; text-decoration:none}
.m-cluster-item .name:active{color:var(--accent-2)}
.m-empty-sm{font-size:12.5px; color:var(--muted)}
.m-row-link{display:inline-flex; align-items:center; gap:4px; margin-top:2px; font-size:12px; color:var(--accent-2); text-decoration:none; font-weight:700}
/* 順位バッジは円形グロー表示、上位1-3位は金銀銅トーンで強調（株ボードと統一） */
.m-rank{
  flex-shrink:0; width:20px; height:20px; display:inline-flex; align-items:center; justify-content:center;
  font-family:var(--font-mono); font-size:11px; font-weight:800; color:var(--accent-2); border-radius:50%;
  background:rgba(34,211,238,.14); border:1px solid rgba(34,211,238,.4); box-shadow:0 0 8px rgba(34,211,238,.35);
  margin-right:8px;
}
.m-rank[data-top="1"]{color:#ffd76b; background:rgba(255,215,107,.16); border-color:rgba(255,215,107,.5); box-shadow:0 0 10px rgba(255,215,107,.5)}
.m-rank[data-top="2"]{color:#d9e2f2; background:rgba(217,226,242,.14); border-color:rgba(217,226,242,.4); box-shadow:0 0 8px rgba(217,226,242,.35)}
.m-rank[data-top="3"]{color:#e3a875; background:rgba(227,168,117,.14); border-color:rgba(227,168,117,.4); box-shadow:0 0 8px rgba(227,168,117,.35)}
.m-chg{font-family:var(--font-mono); font-size:13px; font-weight:700; flex-shrink:0}
.m-chg.up{color:var(--accent); text-shadow:0 0 10px rgba(77,255,126,.35)} .m-chg.down{color:var(--up)} .m-chg.flat{color:var(--flat)}
.m-empty{color:var(--muted); font-size:13px; padding:20px 4px; text-align:center}

/* 便利機能: 「上へ戻る」ボタン（ユーザー要望2026-09-19、株ボードと統一） */
#m-top-btn{
  display:none; position:fixed; right:16px; bottom:96px; z-index:35;
  width:42px; height:42px; border-radius:50%; align-items:center; justify-content:center;
  background:rgba(14,34,25,.7); backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px);
  border:1px solid var(--glass-border); color:var(--accent-2); box-shadow:var(--glass-highlight), 0 10px 24px -8px rgba(0,0,0,.6);
}
#m-top-btn.is-visible{display:flex}
#m-top-btn svg{width:18px; height:18px}

.m-cat-group{margin-bottom:22px}
.m-cat-h3{font-size:14px; font-weight:700; margin:0 0 10px; display:flex; align-items:center; gap:8px}
.m-cat-count{font-size:11px; color:var(--muted); font-family:var(--font-mono)}

.m-stat-row{display:grid; grid-template-columns:repeat(3,1fr); gap:8px}
.m-stat{
  position:relative;
  background:var(--glass-bg); backdrop-filter:blur(20px) saturate(200%); -webkit-backdrop-filter:blur(20px) saturate(200%);
  border:1px solid var(--glass-border); box-shadow:var(--glass-highlight), 0 12px 32px -12px rgba(0,0,0,.55);
  border-radius:14px; padding:14px 8px; display:flex; flex-direction:column; align-items:center; gap:4px;
}
.m-stat-n{font-size:22px; font-weight:800; font-family:var(--font-mono); color:var(--accent-2); text-shadow:0 0 10px rgba(34,211,238,.4)}
.m-stat-l{font-size:11px; color:var(--muted)}

.m-cta{
  position:relative;
  display:block; width:100%; background:linear-gradient(135deg,rgba(157,143,255,.18),rgba(240,138,212,.16));
  backdrop-filter:blur(20px) saturate(200%); -webkit-backdrop-filter:blur(20px) saturate(200%);
  border:1px solid rgba(157,143,255,.4); box-shadow:var(--glass-highlight), 0 0 24px -6px rgba(157,143,255,.35);
  color:var(--accent-2); font-weight:800; font-size:15px; border-radius:14px;
  padding:16px; margin-top:4px;
}
.m-cta:active{opacity:.85}
.m-settings-row{
  display:flex; justify-content:space-between; padding:13px 4px; border-bottom:1px solid var(--glass-border); font-size:14px;
}
.m-note{font-size:12px; color:var(--muted); margin-top:16px; line-height:1.6}

/* タブバー: 上端ガラスパネル+選択中タブのネオングロー */
.m-tabbar{
  position:fixed; left:0; right:0; bottom:0; z-index:30; display:flex;
  background:rgba(3,10,7,.62); backdrop-filter:blur(22px) saturate(160%);
  -webkit-backdrop-filter:blur(22px) saturate(160%);
  border-top:1px solid rgba(255,255,255,.08);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.06), 0 -12px 30px -14px rgba(34,211,238,.18);
  padding:9px 4px calc(6px + env(safe-area-inset-bottom));
}
.m-tab{
  flex:1; display:flex; flex-direction:column; align-items:center; gap:4px;
  background:none; border:none; color:#6b8a7a; padding:6px 0 5px; border-radius:12px;
  position:relative; transition:color .2s ease;
}
.m-tab-icon{width:22px; height:22px; display:block}
.m-tab i{font-style:normal; font-size:9.5px; letter-spacing:.06em; font-family:var(--font-mono)}
.m-tab.is-active{color:var(--accent-2)}
.m-tab.is-active .m-tab-icon{filter:drop-shadow(0 0 6px rgba(34,211,238,.85))}
.m-tab.is-active::before{
  content:""; position:absolute; top:-9px; left:50%; transform:translateX(-50%);
  width:22px; height:2px; border-radius:2px;
  background:linear-gradient(90deg,transparent,var(--accent-2),transparent);
  box-shadow:0 0 8px 1px rgba(34,211,238,.9);
}

#m-back-btn{
  display:none; position:fixed; left:50%; transform:translateX(-50%); bottom:16px; z-index:40;
  background:rgba(14,34,25,.6); backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px);
  color:var(--accent-2); font-weight:800; font-size:13px; border:1px solid rgba(34,211,238,.4);
  border-radius:999px; padding:10px 18px;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.08), 0 0 20px -4px rgba(34,211,238,.4);
}
"""

JS = r"""
<script>
(function(){
  var root = document.documentElement;
  try{ if(localStorage.getItem('news_theme') === 'light'){ root.setAttribute('data-theme','light'); } }catch(e){}

  var toggle = document.getElementById('themeToggle');
  toggle && toggle.addEventListener('click', function(){
    var light = root.getAttribute('data-theme') === 'light';
    if(light){ root.removeAttribute('data-theme'); } else { root.setAttribute('data-theme','light'); }
    try{ localStorage.setItem('news_theme', light ? 'dark' : 'light'); }catch(e){}
  });

  var main = document.querySelector('main');
  // [UI再設計 2026-09-21] 961px以上ではmain(ニュース一覧)自身が
  // overflow-y:autoで独立スクロールし、ページ(documentElement)は
  // 動かない。960px以下は従来通りページ全体がスクロールする
  // フォールバックのまま(main自身は内部スクロールを持たない)。
  // どちらの状態かをCSSに問い合わせず、実際にmainがスクロール可能に
  // なっているかで判定する(リサイズでbreakpointをまたいでも壊れない)。
  function scrollEl(){
    return (main && main.scrollHeight > main.clientHeight + 1) ? main : document.documentElement;
  }
  function scrollToTop(){
    var el = scrollEl();
    if(el.scrollTo){ el.scrollTo({ top: 0, behavior: 'smooth' }); } else { el.scrollTop = 0; }
  }
  var cards = Array.prototype.slice.call(document.querySelectorAll('.news-card'));
  var searchInput = document.getElementById('searchInput');
  var minStars = document.getElementById('minStars');
  var sortSelect = document.getElementById('sortSelect');
  var favOnlyToggle = document.getElementById('favOnlyToggle');
  var futureOnlyToggle = document.getElementById('futureOnlyToggle');
  var noResult = document.getElementById('noResult');
  var activeFilter = document.getElementById('activeFilter');
  var activeFilterText = document.getElementById('activeFilterText');
  // memberIds: 「この材料を見る」で選んだクラスターの構成記事id集合。
  // ページをまたいで保存する対象ではない(その場限りの絞り込みのため
  // persist()には含めない)。
  var state = { category:'all', text:'', stars:0, code:'', sort:'new', favOnly:false, futureOnly:false, memberIds:null };

  var favorites = [];
  try{ favorites = JSON.parse(localStorage.getItem('fav_stocks') || '[]'); }catch(e){ favorites = []; }
  function isFav(code){ return favorites.indexOf(code) !== -1; }
  function syncFavButtons(){
    document.querySelectorAll('.fav-star').forEach(function(btn){
      var on = isFav(btn.dataset.favCode);
      btn.classList.toggle('is-fav', on);
      btn.textContent = on ? '★' : '☆';
    });
  }
  function toggleFav(code){
    var i = favorites.indexOf(code);
    if(i === -1){ favorites.push(code); } else { favorites.splice(i, 1); }
    try{ localStorage.setItem('fav_stocks', JSON.stringify(favorites)); }catch(e){}
    syncFavButtons();
    if(state.favOnly){ apply(); }
  }

  function persist(){
    try{
      localStorage.setItem('news_filters', JSON.stringify({
        category: state.category, stars: state.stars, text: state.text,
        sort: state.sort, favOnly: state.favOnly, futureOnly: state.futureOnly
      }));
    }catch(e){}
  }
  try{
    var saved = JSON.parse(localStorage.getItem('news_filters') || 'null');
    if(saved){
      state.category = saved.category || 'all';
      state.stars = saved.stars || 0;
      state.text = saved.text || '';
      state.sort = saved.sort || 'new';
      state.favOnly = !!saved.favOnly;
      state.futureOnly = !!saved.futureOnly;
    }
  }catch(e){}

  function apply(){
    var shown = 0;
    cards.forEach(function(card){
      var ok = true;
      if(state.category !== 'all' && card.dataset.category !== state.category){ ok = false; }
      if(ok && state.stars && parseInt(card.dataset.importance,10) < state.stars){ ok = false; }
      if(ok && state.code && (' ' + card.dataset.codes + ' ').indexOf(' ' + state.code + ' ') === -1){ ok = false; }
      if(ok && state.memberIds && !state.memberIds.has(card.id.replace(/^news-/, ''))){ ok = false; }
      if(ok && state.favOnly){
        var codes = (card.dataset.codes || '').split(/\s+/).filter(Boolean);
        ok = codes.some(isFav);
      }
      if(ok && state.futureOnly && card.dataset.future !== '1'){ ok = false; }
      if(ok && state.text){
        var blob = (card.dataset.search || '').toLowerCase();
        ok = state.text.split(/\s+/).every(function(w){ return !w || blob.indexOf(w) !== -1; });
      }
      card.style.display = ok ? '' : 'none';
      if(ok){ shown++; }
    });
    noResult.style.display = shown ? 'none' : 'block';
    if(state.code){
      activeFilter.classList.add('is-on');
      activeFilterText.textContent = '銘柄コード ' + state.code + ' に関係するニュースのみ表示中';
    } else if(state.memberIds){
      activeFilter.classList.add('is-on');
      activeFilterText.textContent = '「' + (state.memberLabel || 'この材料') + '」に関係するニュースのみ表示中';
    } else {
      activeFilter.classList.remove('is-on');
    }
    document.querySelectorAll('.see-in-feed').forEach(function(btn){
      btn.classList.toggle('is-active', !!state.memberIds && btn.dataset.active === '1');
    });
  }

  function sortCards(){
    if(!main){ return; }
    var arr = cards.slice();
    if(state.sort === 'importance'){
      arr.sort(function(a,b){ return parseInt(b.dataset.importance,10) - parseInt(a.dataset.importance,10); });
    } else {
      arr.sort(function(a,b){ return (b.dataset.ts||'').localeCompare(a.dataset.ts||''); });
    }
    arr.forEach(function(card){ main.insertBefore(card, noResult); });
  }

  var filterRow = document.querySelector('.filter-row');
  document.querySelectorAll('.filter-chip[data-category]').forEach(function(chip){
    var active = chip.dataset.category === state.category;
    chip.classList.toggle('is-active', active);
    // 復元した選択テーマが「その他」に畳まれている場合は、選べなくなら
    // ないよう最初から展開しておく(2026-09-21 UI再設計)。
    if(active && chip.classList.contains('filter-chip-overflow') && filterRow){
      filterRow.classList.add('is-expanded');
    }
    chip.addEventListener('click', function(){
      document.querySelectorAll('.filter-chip[data-category]').forEach(function(c){ c.classList.remove('is-active'); });
      chip.classList.add('is-active');
      state.category = chip.dataset.category;
      apply(); persist();
    });
  });
  var filterMoreToggle = document.getElementById('filterMoreToggle');
  filterMoreToggle && filterMoreToggle.addEventListener('click', function(){
    var open = filterRow.classList.toggle('is-expanded');
    filterMoreToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  if(searchInput){ searchInput.value = state.text; }
  if(minStars){ minStars.value = String(state.stars); }
  if(sortSelect){ sortSelect.value = state.sort; }
  if(favOnlyToggle){ favOnlyToggle.classList.toggle('is-active', state.favOnly); }
  if(futureOnlyToggle){ futureOnlyToggle.classList.toggle('is-active', state.futureOnly); }

  searchInput && searchInput.addEventListener('input', function(){
    state.text = searchInput.value.trim().toLowerCase();
    apply(); persist();
  });
  minStars && minStars.addEventListener('change', function(){
    state.stars = parseInt(minStars.value, 10) || 0;
    apply(); persist();
  });
  sortSelect && sortSelect.addEventListener('change', function(){
    state.sort = sortSelect.value;
    sortCards(); persist();
  });
  favOnlyToggle && favOnlyToggle.addEventListener('click', function(){
    state.favOnly = !state.favOnly;
    favOnlyToggle.classList.toggle('is-active', state.favOnly);
    apply(); persist();
  });
  futureOnlyToggle && futureOnlyToggle.addEventListener('click', function(){
    state.futureOnly = !state.futureOnly;
    futureOnlyToggle.classList.toggle('is-active', state.futureOnly);
    apply(); persist();
  });

  function flashCopied(btn){
    var original = btn.textContent;
    btn.textContent = 'コピーしました';
    btn.classList.add('is-copied');
    setTimeout(function(){ btn.textContent = original; btn.classList.remove('is-copied'); }, 1400);
  }
  function copyText(text){
    if(navigator.clipboard && navigator.clipboard.writeText){
      return navigator.clipboard.writeText(text);
    }
    var ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try{ document.execCommand('copy'); }catch(e){}
    document.body.removeChild(ta);
    return Promise.resolve();
  }

  document.addEventListener('click', function(ev){
    var favBtn = ev.target.closest ? ev.target.closest('.fav-star') : null;
    if(favBtn){ toggleFav(favBtn.dataset.favCode); return; }

    var copyBtn = ev.target.closest ? ev.target.closest('.copy-link') : null;
    if(copyBtn){
      copyText(copyBtn.dataset.title + ' ' + copyBtn.dataset.url).then(function(){ flashCopied(copyBtn); });
      return;
    }

    var rankToggle = ev.target.closest ? ev.target.closest('.rank-toggle') : null;
    if(rankToggle){ rankToggle.closest('.rank-row').classList.toggle('is-open'); return; }
    var btn = ev.target.closest ? ev.target.closest('[data-filter-code]') : null;
    if(btn){
      state.code = (state.code === btn.dataset.filterCode) ? '' : btn.dataset.filterCode;
      apply();
      scrollToTop();
      return;
    }

    // [UI再設計 2026-09-21] 「この材料を見る」→ニュース一覧をこの
    // クラスターの構成記事だけに絞り込む(トグル式。同じボタンを再度
    // クリックすると解除)。既存の[data-filter-code]と同じ考え方だが、
    // 絞り込み対象が銘柄コード1つではなく記事id集合なのでstate.memberIdsを使う。
    var seeBtn = ev.target.closest ? ev.target.closest('[data-filter-ids]') : null;
    if(seeBtn){
      var wasActive = seeBtn.dataset.active === '1';
      document.querySelectorAll('.see-in-feed').forEach(function(b){ b.dataset.active = '0'; });
      if(wasActive){
        state.memberIds = null; state.memberLabel = '';
      } else {
        var ids = seeBtn.dataset.filterIds.split(/\s+/).filter(Boolean);
        state.memberIds = new Set(ids);
        state.memberLabel = seeBtn.dataset.label || 'この材料';
        seeBtn.dataset.active = '1';
      }
      apply();
      scrollToTop();
      return;
    }
  });

  document.addEventListener('keydown', function(ev){
    var tag = document.activeElement ? document.activeElement.tagName : '';
    var typing = tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA';
    if(ev.key === '/' && !typing){
      ev.preventDefault();
      searchInput && searchInput.focus();
      return;
    }
    if(ev.key === 'Escape'){
      if(searchInput){ searchInput.value = ''; searchInput.blur(); }
      state.text = ''; state.code = ''; state.memberIds = null;
      apply(); persist();
    }
  });

  var clearBtn = document.getElementById('clearCode');
  clearBtn && clearBtn.addEventListener('click', function(){ state.code = ''; state.memberIds = null; apply(); });

  var backTop = document.getElementById('backTop');
  var scrollProgress = document.getElementById('scrollProgress');
  function onScroll(){
    var el = scrollEl();
    var top = (el === main) ? main.scrollTop : (window.scrollY || document.documentElement.scrollTop);
    backTop.classList.toggle('is-on', top > 600);
    if(scrollProgress){
      var scrollable = el.scrollHeight - el.clientHeight;
      scrollProgress.style.width = (scrollable > 0 ? (top / scrollable) * 100 : 0) + '%';
    }
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  main && main.addEventListener('scroll', onScroll, { passive: true });
  backTop.addEventListener('click', scrollToTop);

  syncFavButtons();
  sortCards();
  apply();
  onScroll();
})();

// ==================================================================
// モバイル専用UI（#mobile-app）のタブ切り替え・「詳細を見る」→PC版該当
// 記事へのジャンプ・テーマ切替(既存のthemeToggleボタンを叩くだけで
// 二重実装しない)。株ボード(jp-stock-dashboard)のscraper.mjsと
// 同じ実装パターン。
// ==================================================================
(function () {
  var TAB_KEY = 'news.mobileTab';

  window.mobileGoTo = function (screen) {
    document.querySelectorAll('#mobile-app .m-screen').forEach(function (el) {
      el.classList.toggle('is-active', el.dataset.screen === screen);
    });
    document.querySelectorAll('#mobile-app .m-tab').forEach(function (el) {
      el.classList.toggle('is-active', el.dataset.tab === screen);
    });
    try { sessionStorage.setItem(TAB_KEY, screen); } catch (e) { }
    var screensEl = document.querySelector('#mobile-app .m-screens');
    if (screensEl) screensEl.scrollTop = 0;
  };

  try {
    var savedTab = sessionStorage.getItem(TAB_KEY);
    if (savedTab) window.mobileGoTo(savedTab);
  } catch (e) { /* file:// で sessionStorage が使えない環境では諦める */ }

  window.mobileShowDesktop = function () {
    document.getElementById('mobile-app').style.display = 'none';
    document.getElementById('desktop-view').style.display = 'block';
    var back = document.getElementById('m-back-btn');
    if (back) back.style.display = 'block';
  };

  window.mobileShowMobile = function () {
    document.getElementById('mobile-app').style.display = '';
    document.getElementById('desktop-view').style.display = '';
    var back = document.getElementById('m-back-btn');
    if (back) back.style.display = 'none';
    window.scrollTo(0, 0);
  };

  window.mobileScrollToTop = function () {
    window.scrollTo(0, 0);
  };

  window.addEventListener('scroll', function () {
    var btn = document.getElementById('m-top-btn');
    if (!btn) return;
    var mobileApp = document.getElementById('mobile-app');
    var isMobileVisible = mobileApp && getComputedStyle(mobileApp).display !== 'none';
    btn.classList.toggle('is-visible', isMobileVisible && window.scrollY > 400);
  }, { passive: true });

  window.mobileToggleTheme = function () {
    var toggle = document.getElementById('themeToggle');
    if (toggle) toggle.click();
  };

  // [UI再設計 2026-09-21] HOME上部の検索と、MATERIALSタブの「この材料/
  // 銘柄を見る」からのHOME絞り込み。デスクトップ側のstate/apply()とは
  // 別実装(モバイルはDOM構造が違うため)だが、考え方は同じ:
  // テキスト一致 or id/コード一致でm-news-rowの表示・非表示を切り替える。
  var mState = { text: '', ids: null, code: '', label: '' };
  var mSearchInput = document.getElementById('mSearchInput');
  var mActiveFilter = document.getElementById('mActiveFilter');
  var mActiveFilterText = document.getElementById('mActiveFilterText');
  var mNoResult = document.getElementById('mNoResult');

  function mApply() {
    var rows = document.querySelectorAll('#mNewsList .m-news-row');
    var shown = 0;
    rows.forEach(function (row) {
      var ok = true;
      if (ok && mState.ids && !mState.ids.has(row.id.replace(/^m-news-/, ''))) { ok = false; }
      if (ok && mState.code) {
        var codes = (row.dataset.mCodes || '').split(/\s+/).filter(Boolean);
        if (codes.indexOf(mState.code) === -1) { ok = false; }
      }
      if (ok && mState.text) {
        var blob = (row.dataset.mSearch || '').toLowerCase();
        ok = mState.text.split(/\s+/).every(function (w) { return !w || blob.indexOf(w) !== -1; });
      }
      row.style.display = ok ? '' : 'none';
      if (ok) shown++;
    });
    if (mNoResult) mNoResult.style.display = shown ? 'none' : 'block';
    if (mActiveFilter) {
      var on = !!(mState.ids || mState.code);
      mActiveFilter.classList.toggle('is-on', on);
      if (on && mActiveFilterText) { mActiveFilterText.textContent = '「' + mState.label + '」で絞り込み中'; }
    }
  }

  mSearchInput && mSearchInput.addEventListener('input', function () {
    mState.text = mSearchInput.value.trim().toLowerCase();
    mApply();
  });
  var mClearFilter = document.getElementById('mClearFilter');
  mClearFilter && mClearFilter.addEventListener('click', function () {
    mState.ids = null; mState.code = ''; mState.label = '';
    mApply();
  });

  document.addEventListener('click', function (ev) {
    var seeBtn = ev.target.closest ? ev.target.closest('[data-m-filter-ids]') : null;
    if (seeBtn) {
      var ids = seeBtn.dataset.mFilterIds.split(/\s+/).filter(Boolean);
      mState.ids = new Set(ids); mState.code = ''; mState.label = seeBtn.dataset.mLabel || 'この材料';
      mApply();
      window.mobileGoTo('home');
      return;
    }
    var codeBtn = ev.target.closest ? ev.target.closest('[data-m-filter-code]') : null;
    if (codeBtn) {
      mState.code = codeBtn.dataset.mFilterCode; mState.ids = null; mState.label = codeBtn.dataset.mLabel || mState.code;
      mApply();
      window.mobileGoTo('home');
      return;
    }
  });

  // PWAインストール可否の必須条件（Service Worker登録実績）を満たす。
  // GitHub Pages配信(HTTPS)なので株ボードと違い実際にPush通知等も
  // 将来使える。登録失敗時は他の機能に影響しないよう例外を握りつぶす。
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(function () { });
  }
})();
</script>
"""

DISCLAIMER = (
    "本サイトは Google ニュースの公開RSSで集めた見出しを、あらかじめ定義したキーワードルール"
    "(および任意で生成AI)で機械的に分類・関連付けした<b>情報提供のみを目的としたページ</b>です。"
    "「影響が出うる銘柄」は、過去の一般的な連想関係にもとづく機械的な推定であり、"
    "<b>実際にその銘柄の株価が動くことを保証・予想するものではありません。投資助言でもありません。</b>"
    "見出しの解釈には誤りが含まれることがあります。必ずリンク先の原文と実際の株価をご自身で確認し、"
    "投資判断はご自身の責任で行ってください。"
)


# ==================================================================
# モバイル専用UI（PWA・3画面構成、ユーザー方針2026-09-19。
# 株ボード(jp-stock-dashboard)と同じ「サイバーネオン×ミニマルガラス」
# デザイン言語に揃える。判定ロジック・データは一切新規計算せず、
# build_html()が既に持っているnews/categories/ranking等をそのまま
# 参照するだけ（PC版とモバイル版で表示内容が食い違わないようにするため）。
# 「詳細を見る」は、desktop側の該当<article id="news-${id}">まで
# スクロールする形にして、同じ記事をモバイル用に二重に作り込まない。
# ==================================================================

MOBILE_TAB_ICONS = {
    "home": '<svg class="m-tab-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">'
            '<path d="M4 11.5 12 4l8 7.5" stroke-linecap="round" stroke-linejoin="round"/>'
            '<path d="M6 10v9h12v-9" stroke-linecap="round" stroke-linejoin="round"/>'
            '<path d="M10 19v-5h4v5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "category": '<svg class="m-tab-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">'
                '<rect x="3.5" y="3.5" width="7" height="7" rx="1.4"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.4"/>'
                '<rect x="3.5" y="13.5" width="7" height="7" rx="1.4"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.4"/></svg>',
    "settings": '<svg class="m-tab-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">'
                '<circle cx="12" cy="12" r="3.2"/>'
                '<path d="M12 3.5v2.4M12 18.1v2.4M20.5 12h-2.4M5.9 12H3.5M17.7 6.3l-1.7 1.7M8 16l-1.7 1.7M17.7 17.7 16 16M8 8 6.3 6.3" stroke-linecap="round"/></svg>',
    "materials": '<svg class="m-tab-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">'
                 '<circle cx="6" cy="7" r="2.6"/><circle cx="18" cy="7" r="2.6"/><circle cx="12" cy="18" r="2.6"/>'
                 '<path d="M8.3 8.6 10 15.5M15.7 8.6 14 15.5" stroke-linecap="round"/></svg>',
}

# セクション見出しのアイコン（絵文字廃止・ユーザー要望2026-09-19。株ボードと
# 同じライン画SVGパターンで統一する）。
MOBILE_H2_ICONS = {
    "brand": '<svg class="m-h2-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">'
             '<path d="M3 16 9 10l4 4 8-9" stroke-linecap="round" stroke-linejoin="round"/>'
             '<path d="M15 5h6v6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "importance": '<svg class="m-h2-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">'
                  '<path d="M12 3.5 14.6 9.2 21 10l-4.7 4.3L17.5 21 12 17.7 6.5 21l1.2-6.7L3 10l6.4-.8Z" stroke-linejoin="round"/></svg>',
    "ranking": '<svg class="m-h2-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">'
               '<rect x="3.5" y="12" width="4" height="8" rx="1"/><rect x="10" y="7" width="4" height="13" rx="1"/>'
               '<rect x="16.5" y="3.5" width="4" height="16.5" rx="1"/></svg>',
    "summary": '<svg class="m-h2-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">'
               '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1"/></svg>',
    "category": MOBILE_TAB_ICONS["category"].replace("m-tab-icon", "m-h2-icon"),
    "settings": MOBILE_TAB_ICONS["settings"].replace("m-tab-icon", "m-h2-icon"),
    "monitor": '<svg class="m-cta-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">'
               '<rect x="3" y="4.5" width="18" height="12" rx="1.6"/><path d="M8.5 20h7M12 16.5V20" stroke-linecap="round"/></svg>',
    "theme": '<svg class="m-cta-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">'
             '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z" stroke-linejoin="round"/></svg>',
    "clusters": '<svg class="m-h2-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">'
                '<circle cx="6" cy="7" r="3"/><circle cx="18" cy="7" r="3"/><circle cx="12" cy="18" r="3"/>'
                '<path d="M8.5 8.8 10.3 15.8M15.5 8.8 13.7 15.8" stroke-linecap="round"/></svg>',
}


def mobile_cluster_row(c):
    """[つながっている材料] 見出しは違っても同じテーマを共有する複数ニュースを
    その場で展開して見せる(mobile_news_rowと同じ展開式カードの流儀)。"""
    stock_chips = "".join(
        f'<span class="m-code-chip">{esc(s["name"])}</span>'
        for s in c.get("stocks", [])[:4]
    )
    members = "".join(
        f'<div class="m-impact-item m-cluster-item">'
        f'<a class="name" href="{esc(m["url"])}" target="_blank" rel="noopener">{esc(m["title"])}</a></div>'
        for m in c.get("members", [])
    )
    # 2026-09-20実測差異修正: デスクトップのcluster_htmlは、まだemerging/
    # watchに届いていないテーマ型クラスターにも診断チップ(🔬観測中)と
    # 成長の経過パネルを出すようにしたのに、モバイル側だけemergence_stage_label
    # 有無だけで判定したままになっていて、同じテーマがモバイルでは
    # 何も見えないという抜け漏れがあった。<details>と違い<button>展開式
    # なので、成長パネルは既存の展開エリア(.m-impact-panel)に含める。
    emergence_label = c.get("emergence_stage_label")
    has_diagnosis = bool(c.get("emergence_diagnosis"))
    chip_label = emergence_label or ("🔬 観測中" if has_diagnosis else "")
    chip_stage = c.get("emergence_stage") or ("watching" if has_diagnosis else "")
    emergence_chip = (
        f'<span class="m-emergence-chip" data-stage="{esc(chip_stage)}">{esc(chip_label)}</span>'
        if chip_label else ""
    )
    growth_block = emergence_growth_html(c)
    # [UI再設計 2026-09-21] MATERIALSタブから「この材料を見る」でHOMEの
    # ニュース一覧を絞り込む(デスクトップのsee-in-feedと同じ考え方)。
    member_ids = " ".join(esc(m["id"]) for m in c.get("members", []))
    return f"""
  <div class="m-row m-news-row">
    <button class="m-row-head" type="button" onclick="this.closest('.m-news-row').classList.toggle('is-expanded')">
      <div class="m-row-main">
        <span class="m-row-cat">{esc(c.get('category_emoji', '📰'))} {esc(c.get('category_label', ''))}{emergence_chip}</span>
        <span class="m-row-title">{esc(c['label'])}</span>
        <span class="m-row-sub">{len(c.get('members', []))}件のニュース{stock_chips}</span>
      </div>
      <svg class="m-row-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6" stroke-linecap="round" stroke-linejoin="round"/></svg>
    </button>
    <div class="m-impact-panel">
      <button class="see-in-feed m-see-in-feed" type="button" data-m-filter-ids="{member_ids}" data-m-label="{esc(c['label'])}">🔎 この材料をニュース一覧で見る</button>
      {members}
      {growth_block}
    </div>
  </div>"""


def mobile_news_row(item):
    stars_n = max(1, min(5, int(item.get("importance") or 1)))
    impacts = item.get("impacts", [])
    pos = sum(1 for i in impacts if i["direction"] == "positive")
    neg = sum(1 for i in impacts if i["direction"] == "negative")
    watch = sum(1 for i in impacts if i["direction"] == "watch")
    counts_html = "".join([
        f'<span class="up">▲{pos}</span>' if pos else "",
        f'<span class="down">▼{neg}</span>' if neg else "",
        f'<span class="flat">●{watch}</span>' if watch else "",
    ])
    if impacts:
        impact_items = "".join(
            f'<div class="m-impact-item {DIRECTION_CLASS.get(i["direction"], "flat")}">'
            f'<span class="mark">{DIRECTION_MARK.get(i["direction"], "●")}</span>'
            f'<span class="name">{esc(i["name"])}</span>'
            f'<span class="code">{esc(i["code"])}</span></div>'
            for i in impacts
        )
    else:
        impact_items = '<p class="m-empty-sm">影響が出うる銘柄は現在のルールでは特定できませんでした</p>'
    emergence_label = item.get("emergence_stage_label")
    emergence_chip = (
        f'<span class="m-emergence-chip" data-stage="{esc(item.get("emergence_stage"))}">{esc(emergence_label)}</span>'
        if emergence_label else ""
    )
    # [UI再設計 2026-09-21] HOME上部の検索・材料/銘柄からの絞り込みで
    # 対象を特定するためのid・検索用テキスト(既存のdesktop版news-cardの
    # data-search/idと同じ考え方をモバイル側にも持たせるだけ)。
    codes = " ".join(i["code"] for i in impacts)
    names = " ".join(i["name"] for i in impacts)
    search_blob = esc(f"{item['title']} {item.get('source', '')} {codes} {names}")
    return f"""
  <div class="m-row m-news-row" id="m-news-{esc(item['id'])}" data-m-codes="{esc(codes)}" data-m-search="{search_blob}">
    <button class="m-row-head" type="button" onclick="this.closest('.m-news-row').classList.toggle('is-expanded')">
      <div class="m-row-main">
        <span class="m-row-cat">{esc(item.get('category_emoji', '📰'))} {esc(item.get('category_label', ''))}{emergence_chip}</span>
        <span class="m-row-title">{esc(item['title'])}</span>
        <span class="m-row-sub">{'★' * stars_n}{counts_html}</span>
      </div>
      <svg class="m-row-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6" stroke-linecap="round" stroke-linejoin="round"/></svg>
    </button>
    <div class="m-impact-panel">
      {impact_items}
      <a class="m-row-link" href="{esc(item['url'])}" target="_blank" rel="noopener">元記事を読む ↗</a>
    </div>
  </div>"""


def mobile_app_html(data, now):
    news = data.get("news", [])
    categories = data.get("categories", [])
    ranking = data.get("stock_ranking", [])[:10]

    # [UI再設計 2026-09-21] HOMEはニュース一覧そのものを主役にする
    # (旧: 重要度上位5件のみ→つながっている材料→材料が集まっている銘柄、
    # という縦積みで窮屈だった。後者2つはMATERIALSタブへ分離した)。
    high_importance = [n for n in news if (n.get("importance") or 0) >= 4]

    counts = {}
    for n in news:
        counts[n["category"]] = counts.get(n["category"], 0) + 1
    category_sections = "".join(
        f"""
    <div class="m-cat-group">
      <h3 class="m-cat-h3">{esc(cat.get('emoji', '📰'))} {esc(cat['label'])}<span class="m-cat-count">{counts[cat['id']]}</span></h3>
      <div class="m-list">
        {''.join(mobile_news_row(n) for n in news if n['category'] == cat['id'])}
      </div>
    </div>"""
        for cat in categories if counts.get(cat["id"])
    )

    ranking_rows = "".join(f"""
  <button type="button" class="m-row m-rank-row m-rank-row-btn" data-m-filter-code="{esc(row['code'])}" data-m-label="{esc(row['name'])}">
    <div class="m-row-main">
      <span class="m-rank" data-top="{i + 1 if i + 1 <= 3 else 0}">{i + 1}</span>
      <span class="m-row-title">{esc(row['name'])}<span class="m-code-chip">{esc(row['code'])}</span></span>
    </div>
    <span class="m-chg {'up' if row['score'] > 0 else 'down' if row['score'] < 0 else 'flat'}">{row['mentions']}件</span>
  </button>""" for i, row in enumerate(ranking))

    cluster_rows = "".join(mobile_cluster_row(c) for c in data.get("clusters", []))

    generated_at = esc(data.get("generated_at", ""))

    return f"""
<div id="mobile-app">
  <header class="m-topbar">
    <div class="m-brand">{MOBILE_H2_ICONS['brand']}日本株ニュース</div>
    <div class="m-updated">{generated_at} 更新</div>
  </header>

  <div class="m-screens">
    <section class="m-screen is-active" data-screen="home">
      {verification_status_html(data.get("verification_status"))}
      <div class="m-search-row">
        <input id="mSearchInput" type="search" placeholder="キーワード・銘柄名で絞り込み" class="m-search-input">
        <span class="m-active-filter" id="mActiveFilter">
          <span id="mActiveFilterText"></span>
          <button id="mClearFilter" type="button">解除</button>
        </span>
      </div>
      <h2 class="m-h2 accent-amber">{MOBILE_H2_ICONS['importance']}ニュース一覧<span class="m-h2-count">{len(news)}件</span></h2>
      <div class="m-list" id="mNewsList">
        {''.join(mobile_news_row(n) for n in news) if news else '<p class="m-empty">今回はニュースを取得できませんでした</p>'}
      </div>
      <p class="m-empty" id="mNoResult" style="display:none">条件に一致するニュースはありません。絞り込みを緩めてください。</p>
    </section>

    <section class="m-screen" data-screen="materials">
      <h2 class="m-h2 accent-magenta">{MOBILE_H2_ICONS['clusters']}つながっている材料</h2>
      <div class="m-list">
        {cluster_rows if cluster_rows else '<p class="m-empty">今回は複数ニュースにまたがる材料はありません</p>'}
      </div>

      <h2 class="m-h2 accent-cyan">{MOBILE_H2_ICONS['ranking']}材料が集まっている銘柄</h2>
      <div class="m-list">
        {ranking_rows if ranking_rows else '<p class="m-empty">集計できる影響銘柄がありません</p>'}
      </div>

      <h2 class="m-h2 accent-violet">{MOBILE_H2_ICONS['summary']}サマリー</h2>
      <div class="m-stat-row">
        <div class="m-stat"><span class="m-stat-n">{len(news)}</span><span class="m-stat-l">ニュース</span></div>
        <div class="m-stat"><span class="m-stat-n">{len(high_importance)}</span><span class="m-stat-l">★4以上</span></div>
        <div class="m-stat"><span class="m-stat-n">{data.get('counts', {}).get('stocks', 0)}</span><span class="m-stat-l">影響銘柄</span></div>
      </div>
    </section>

    <section class="m-screen" data-screen="category">
      <h2 class="m-h2 accent-magenta">{MOBILE_H2_ICONS['category']}カテゴリ別</h2>
      {category_sections if category_sections else '<p class="m-empty">表示できるカテゴリがありません</p>'}
    </section>

    <section class="m-screen" data-screen="settings">
      <h2 class="m-h2">{MOBILE_H2_ICONS['settings']}設定</h2>
      <div class="m-settings-row"><span>最終更新</span><span>{generated_at}</span></div>
      <div class="m-settings-row"><span>ニュース件数</span><span>{len(news)}件</span></div>
      <button class="m-cta" onclick="mobileToggleTheme()">{MOBILE_H2_ICONS['theme']}テーマ切り替え</button>
      <button class="m-cta" onclick="mobileShowDesktop()" style="margin-top:10px">{MOBILE_H2_ICONS['monitor']}PC版を表示</button>
      <p class="m-note">このモバイル画面はβ版です。検索・お気に入り等の詳細操作はPC版でご利用ください。</p>
    </section>
  </div>

  <nav class="m-tabbar">
    <button class="m-tab is-active" data-tab="home" onclick="mobileGoTo('home')">{MOBILE_TAB_ICONS['home']}<i>HOME</i></button>
    <button class="m-tab" data-tab="materials" onclick="mobileGoTo('materials')">{MOBILE_TAB_ICONS['materials']}<i>MATERIALS</i></button>
    <button class="m-tab" data-tab="category" onclick="mobileGoTo('category')">{MOBILE_TAB_ICONS['category']}<i>CATEGORY</i></button>
    <button class="m-tab" data-tab="settings" onclick="mobileGoTo('settings')">{MOBILE_TAB_ICONS['settings']}<i>SET</i></button>
  </nav>
</div>
<button id="m-back-btn" onclick="mobileShowMobile()">{MOBILE_TAB_ICONS['home']}モバイル表示に戻る</button>
<button id="m-top-btn" onclick="mobileScrollToTop()" aria-label="上へ戻る">
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 19V6M6 11l6-6 6 6" stroke-linecap="round" stroke-linejoin="round"/></svg>
</button>"""


def build_html(data):
    now = datetime.now(JST)
    news = data.get("news", [])
    categories = data.get("categories", [])
    counts = data.get("counts", {})

    cards = "".join(news_card_html(n, now) for n in news) or (
        '<p class="empty">今回はニュースを取得できませんでした。'
        '古い情報で判断してしまわないよう、前回取得した内容は表示していません。<br>'
        '次回の自動更新(市場時間帯は15分おき)をお待ちください。</p>'
    )
    sample_banner = (
        '<div class="notice sample-banner">🧪 <b>これはサンプルデータで生成した表示確認用のページです。</b>'
        '掲載されている見出しは実際のニュースではありません。</div>'
        if data.get("status") == "sample" else ""
    )
    llm_note = (
        "見出しの要約・波及コメントは生成AIによる補強を含みます。"
        if data.get("llm_used") else
        "今回は生成AIによる補強なし(キーワードルールのみ)で生成しています。"
    )
    verify_status_block = verification_status_html(data.get("verification_status"))

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="日本株に影響しうる重要ニュースと、その影響が出うる銘柄をまとめた自動更新サイト">
<meta name="theme-color" content="#010402">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="株ニュース">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<link rel="icon" href="icon-192.png">
<link rel="manifest" href="manifest.json">
<title>重要ニュース × 影響銘柄 | 日本株ニュースインパクト</title>
<script>try{{if(localStorage.getItem('news_theme')==='light'){{document.documentElement.setAttribute('data-theme','light');}}}}catch(e){{}}</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;600;700&family=Zen+Kaku+Gothic+New:wght@500;700;900&family=JetBrains+Mono:wght@500;700&family=Orbitron:wght@600;700&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
{mobile_app_html(data, now)}
<div id="desktop-view">
<div class="scroll-progress" id="scrollProgress"></div>
<header class="site">
  <div class="head-inner">
    <div class="brand">
      <div class="eyebrow">JAPAN EQUITY ・ NEWS IMPACT</div>
      <h1>重要ニュース × 影響銘柄</h1>
      <div class="sub">最終更新 {esc(data.get('generated_at', ''))} (JST) ・ ニュース {counts.get('news', 0)}件
        (うち重要度★4以上 {counts.get('high_importance', 0)}件) ・ 影響銘柄 {counts.get('stocks', 0)}銘柄</div>
    </div>
    <div class="head-actions">
      <button id="themeToggle" class="theme-toggle" type="button">🌗 テーマ</button>
    </div>
  </div>
  {market_bar_html(data.get('market', []))}
</header>

<div class="wrap">
  <div class="controls">
    <div class="filter-row">{category_filter_html(categories, news)}</div>
    <div class="search-row">
      <input id="searchInput" type="search" placeholder="キーワード・銘柄名・証券コードで絞り込み(例: 半導体 8035)">
      <select id="minStars">
        <option value="0">重要度: すべて</option>
        <option value="3">★3以上</option>
        <option value="4">★4以上</option>
        <option value="5">★5のみ</option>
      </select>
      <select id="sortSelect">
        <option value="new">新着順</option>
        <option value="importance">重要度順</option>
      </select>
      <button id="favOnlyToggle" class="filter-chip fav-toggle" type="button">★ お気に入りのみ</button>
      <button id="futureOnlyToggle" class="filter-chip future-toggle" type="button">🔮 先行情報のみ</button>
      <span class="active-filter" id="activeFilter">
        <span id="activeFilterText"></span>
        <button id="clearCode" type="button">解除</button>
      </span>
    </div>
  </div>

  <div class="layout">
    <main>
      {sample_banner}
      <div class="notice">
        ⚠️ <b>投資助言ではありません。</b> 各ニュースの「影響が出うる銘柄」は、キーワードルールにもとづく
        機械的な関連付けであり、株価の値動きを保証するものではありません。{esc(llm_note)}
      </div>
      {verify_status_block}
      {cards}
      <p class="no-result" id="noResult">条件に一致するニュースはありません。絞り込みを緩めてください。</p>
      <footer>
        <p>⚠️ {DISCLAIMER}</p>
        <p>情報源: Google ニュース RSS (news.google.com) / 株価リンク: Yahoo!ファイナンス。
          各情報の著作権は提供元に帰属します。</p>
        <p>以前のテクニカル指標つきデイトレードダッシュボードは
          <a href="dashboard.html">dashboard.html</a> に残しています。</p>
        <p>生成日時: {esc(data.get('generated_iso', ''))} ・ {esc(data.get('status_message', ''))}</p>
      </footer>
    </main>
    <aside class="side">
      <div class="panel">
        <h2>🧩 つながっている材料</h2>
        <p class="panel-desc">見出しの文言が違っても、同じテーマ(国・地域・資源・政策・規制など)に
          反応しているニュースをまとめています。クリックで見出し一覧・関連銘柄を開きます。</p>
        {cluster_html(data.get('clusters', []))}
      </div>
      <div class="panel">
        <h2>📌 材料が集まっている銘柄</h2>
        <p class="panel-desc">表示中のニュース全体で、影響銘柄として挙がった回数と方向を集計しています。
          クリックでその銘柄のニュースだけ表示します。</p>
        {ranking_html(data.get('stock_ranking', []))}
      </div>
      <div class="panel">
        <h2>🔎 このページの作り方</h2>
        <p class="panel-desc">
          Google ニュースRSSの見出しを収集 → 重複を束ねて重要度を採点 → キーワードルール
          (newssite/data/rules.json)で「影響が出うる銘柄」を紐づけ → このページを生成、という流れです。
          銘柄やルールは <code>newssite/data/stocks.json</code> と <code>newssite/data/rules.json</code> を
          編集するだけで変更できます。
        </p>
      </div>
    </aside>
  </div>
</div>
<button class="back-top" id="backTop" type="button" aria-label="上に戻る">↑</button>
</div><!-- /#desktop-view -->
{JS}
</body>
</html>
"""


def render_to_file(data, out_path):
    html_text = build_html(data)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_text)
    return html_text


def load_and_render(news_json_path, out_path):
    with open(news_json_path, encoding="utf-8") as f:
        data = json.load(f)
    return render_to_file(data, out_path)
