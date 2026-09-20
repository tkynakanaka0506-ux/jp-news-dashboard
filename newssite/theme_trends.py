#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""萌芽シグナル検知: まだ大きなニュースになっていないテーマを早期に見つける。

[PRESENTATION LAYER] ここでの判定は表示・優先度付けにのみ使う。
impact.pyのdirection/theme/direct-indirect(FACTUAL LAYER)判定には
一切使わない・影響しない(rules.jsonの_comment_layering_principle参照)。

ユーザー方針(2026-09-20、複数回のやり取りで段階的に確定):
  「『ビッグニュースになる可能性が高い』を最初から予言させる設計には
  しない方がいい。代わりに『先行シグナルが何個集まっているか』を検出
  する」「本当に異なる情報源・出来事からテーマが広がっているのかを
  より正確に把握したい」

■ 絶対に維持する原則(ユーザー指定)
  1. 1記事が複数シグナルを満たしても独立イベントは1件
  2. 同一情報の転載を可能な限り重複カウントしない
  3. 異なる主体からの独立した情報は区別する
  4. first_seenは上書きしない
  5. 根拠はツールチップ等で確認できる(ブラックボックス化しない)
  6. なぜテーマが検出されたのか説明可能にする
  7. 将来のニュースや株価を予測しない(単一の「将来性スコア」は作らない)
  8. 既存の42テーマ・つながっている材料・Policy Catalyst・グローバル
     観測は一切変更しない(このモジュールはそれらを読むだけの追加レイヤー)

■ 設計(2026-09-20の追加要望に基づく最終形)
  ①③⑤新規性・加速: 直近7/30/90日の「独立イベント数」を並べて出す
     (単一の伸び率スコアには合成しない=生の件数の並置だけに留める)。
  ②情報源の独立性: 「記事数」ではなく「情報源(item['source'])の異なり
     数」を独立性の判定基準にする。同じ省庁が2回発表しても情報源は1つ、
     省庁+企業なら情報源は2つ、という数え方(記事の転載・複数媒体報道
     は既存のrss.py側の同一話題統合、およびここでの「media」区分で
     "独立した新しい事実"としては扱わない)。
  ②'情報源の種類(可視化): 政府(日本/海外)・企業・研究機関・国際機関・
     複数メディアの6区分を検出し、件数ではなく種類そのものを開示する
     (「メディア5件」と「政府+企業+研究機関+海外+メディア」は意味が
     違う、という指摘への対応)。
  ④地域情報: 「発信元」として確度高く判定できる場合(政府機関名が
     見出しに明示されている、またはGOV_FEED経由=日本の一次情報)だけ
     origin_regionを埋める。それ以外(単なる国名の言及)は「関連する
     地域」として区別せず、不明な発信元を推測で埋めない。
  ⑤テーマの成長履歴: 「各情報源タイプを最初に観測した日」を
     milestonesとして記録し、SIGNAL_WINDOW_DAYSの経過で消える
     eventsとは別に、一度記録したら絶対に削除・上書きしない
     (後から「テーマがどのように広がってきたか」を辿れるようにする)。
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

from . import impact as impact_mod

DATA_DIR = Path(__file__).resolve().parent / "data"
REGISTRY_PATH = DATA_DIR / "theme_trend_registry.json"

# シグナルの有効期限。この日数より前のイベントは「今も生きている根拠」
# とはみなさない(大昔の1回きりの言及で永久に「要監視」のままになるのを
# 防ぐ)。milestones(成長履歴)はこの対象外で永久に保持する。
SIGNAL_WINDOW_DAYS = 90

# ③加速の表示に使う、入れ子の集計ウィンドウ(日数)。短い順。
TREND_WINDOWS = (7, 30, 90)

# テーマの初出からこの日数以内・累計出現回数がこの件数以下なら「新興(まだ
# 大きく報じられていない)」候補として扱う。
EMERGING_MAX_DAYS = 14
EMERGING_MAX_OCCURRENCES = 6

# ②独立性: 「複数方面からのシグナル」と呼ぶには、最低でも別々の情報源
# (item["source"]の異なり数)による裏付けが要る。同じ情報源(例: 同じ
# 省庁)からの複数回の発表は、記事(item_id)が違っても情報源としては
# 1つにしかならない。
MIN_INDEPENDENT_SOURCES = 2

EMERGING_MIN_SIGNALS = 2
WATCH_MIN_SIGNALS = 3

SIGNAL_TYPES = {
    "gov_source": "政府・省庁の一次情報",
    "gov_policy": "政策の検討・審議段階",
    "rd": "研究開発の進展",
    "patent": "特許",
    "capex": "企業の設備投資",
    "foreign": "海外の動き",
    "corporate": "関連企業の直接発表",
    "multi_source": "複数媒体での同時報道",
}

# ②'情報源の種類。1件のニュースが複数の種類に該当してもよい
# (例: 経産省の一次情報がAI関連の設備投資支援にも触れている、等)。
# "media"だけは「新しい独立した事実」ではなく「既存の話題の転載・拡散」
# を表すため、独立性の判定(distinct_sources)には別途sourceの異なり数
# を使う一方、種類の可視化としてはここに残す。
ACTOR_TYPES = {
    "government_jp": "日本政府・省庁",
    "government_foreign": "海外政府・当局",
    "corporate": "関連企業",
    "research": "研究機関・大学",
    "international_org": "国際機関",
    "media": "複数メディア",
}

STAGE_LABEL = {
    "emerging": "🔎 新興テーマ",
    "watch": "👀 要監視テーマ",
}

STAGE_PRIORITY = {"emerging": 0, "watch": 1, None: 9}

_FOREIGN_CATEGORIES = {"geopolitics", "us"}
_EARLY_POLICY_MATURITY_MAX = 45  # 「検討・審議会」段階(rules.jsonのpolicy_maturity_stages参照)

# ④地域情報。REGION_AGENCY_KEYWORDSは機関名そのものが主体(発信元)を
# 特定できる、確度の高いキーワードだけを置く(例:「米商務省」が見出しに
# あれば発信元は米国、と確信を持って言える)。REGION_MENTION_KEYWORDSは
# 単なる国名の言及で、その国が主体か対象かは見出しの文言だけでは
# 判別できないため、「関連する地域」としてのみ扱い発信元とはみなさない。
REGION_AGENCY_KEYWORDS = {
    "日本": ["経済産業省", "国土交通省", "財務省", "金融庁", "首相官邸", "内閣府"],
    "米国": ["米商務省", "米エネルギー省", "米国防総省", "ホワイトハウス", "FRB", "FOMC"],
    "EU/欧州": ["欧州委員会"],
    "中国": ["中国政府"],
    "韓国": ["韓国政府"],
}
REGION_MENTION_KEYWORDS = {
    "日本": ["日本"],
    "米国": ["米国", "アメリカ"],
    "EU/欧州": ["EU", "欧州連合", "欧州"],
    "英国": ["英国", "イギリス"],
    "中国": ["中国"],
    "韓国": ["韓国"],
    "台湾": ["台湾"],
}


def _matched_regions(title, keyword_map):
    return {region for region, kws in keyword_map.items() if any(k in title for k in kws)}


def detect_signals(item, rules):
    """1件のニュースから検出できる独立した「観点」の集合を返す(既存の
    8種類、検出シグナルのツールチップ表示用。原則1に基づき、ここで複数
    検出されても record_and_classify 側で1件の裏付けとしてしか扱わない)。
    """
    title = item.get("title", "")
    signals = set()
    if item.get("source_tier") == "primary":
        signals.add("gov_source")
    if item.get("future_signal") or (
        item.get("policy_maturity") is not None and item["policy_maturity"] < _EARLY_POLICY_MATURITY_MAX
    ):
        signals.add("gov_policy")
    if impact_mod.has_rd_signal(title, rules):
        signals.add("rd")
    if impact_mod.has_patent_signal(title, rules):
        signals.add("patent")
    if impact_mod.has_capex_signal(title, rules):
        signals.add("capex")
    if item.get("category") in _FOREIGN_CATEGORIES:
        signals.add("foreign")
    if any(i.get("origin") == "direct" for i in item.get("impacts", [])):
        signals.add("corporate")
    if len(item.get("related", [])) >= 1:
        signals.add("multi_source")
    return signals


def detect_actor_types(item, rules):
    """②'情報源の種類。政府(日本/海外)・企業・研究機関・国際機関・
    複数メディアの6区分を検出する(1件が複数に該当してもよい)。
    """
    title = item.get("title", "")
    types = set()
    if item.get("source_tier") == "primary":
        types.add("government_jp")
    if _matched_regions(title, REGION_AGENCY_KEYWORDS) - {"日本"}:
        types.add("government_foreign")
    if any(i.get("origin") == "direct" for i in item.get("impacts", [])):
        types.add("corporate")
    if impact_mod.has_research_signal(title, rules):
        types.add("research")
    if impact_mod.has_international_org_signal(title, rules):
        types.add("international_org")
    if len(item.get("related", [])) >= 1:
        types.add("media")
    return types


def detect_regions(item):
    """④地域情報(関連する地域): 見出しに含まれる国・地域名の全体集合。
    発信元/対象/関連の区別はしない(見出しの文言だけでは主体・客体を
    確度高く判別できないため、無理に推測せず一括りの「言及地域」とする)。
    """
    title = item.get("title", "")
    return _matched_regions(title, REGION_AGENCY_KEYWORDS) | _matched_regions(title, REGION_MENTION_KEYWORDS)


def detect_origin_region(item):
    """④地域情報(発信元): 確度高く判定できる場合だけ返す。それ以外は
    None(不明)。推測で埋めない(ユーザー方針: 正確に取得できない場合は
    不明として扱う)。
      - source_tier=="primary"(GOV_FEED経由) → 日本の一次情報と確定できる
      - 見出しに海外の政府機関名が1つだけ一致 → その機関の国・地域
    見出しに複数の機関名が混在する等、確信が持てない場合はNoneのまま。
    """
    if item.get("source_tier") == "primary":
        return "日本"
    agency_regions = _matched_regions(item.get("title", ""), REGION_AGENCY_KEYWORDS)
    if len(agency_regions) == 1:
        return next(iter(agency_regions))
    return None


def _load_registry(path=REGISTRY_PATH):
    if not path.exists():
        return {"themes": {}}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"themes": {}}
    data.setdefault("themes", {})
    return data


def _save_registry(registry, path=REGISTRY_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=1, sort_keys=True)


def _days_between(day_a, day_b):
    fmt = "%Y-%m-%d"
    return abs((datetime.strptime(day_b, fmt) - datetime.strptime(day_a, fmt)).days)


def _shift_date(day, delta_days):
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=delta_days)).strftime("%Y-%m-%d")


def _window_counts(events, today):
    """③⑤加速・新規性: 直近7/30/90日でそれぞれ何件の独立したイベント
    (item_id)があったかを数える。件数を並べて出すだけで、伸び率や
    スコアには合成しない(単一指標に潰すと、それ自体が一種の予言に
    なるため)。「最近になって増えているのか、以前から一定数存在するのか」
    は、この3つの数字を並べて見る人が判断する。
    """
    counts = {}
    for w in TREND_WINDOWS:
        cutoff = _shift_date(today, -w)
        counts[w] = len({e["item_id"] for e in events if e["date"] >= cutoff})
    return counts


def record_and_classify(registry, news, rules, today):
    """今回のnewsに含まれる各テーマの出現・シグナルを登録簿に記録し、
    テーマごとのステージ・診断情報を返す。

    registry はこの関数が破壊的に更新する(呼び出し側で_save_registryする)。

    記録の単位は「(item_id, その記事のsource/シグナル/主体種別/地域)の
    イベント」にする(原則1・2): 同じitem_idは1回しかイベント化されない
    ので、1本の記事が複数の観点を同時に満たしても独立イベントは1件のまま
    になる。
    """
    themes_state = registry.setdefault("themes", {})
    today_by_theme = {}
    for item in news:
        info = {
            "source": item.get("source") or "",
            "signals": detect_signals(item, rules),
            "actor_types": detect_actor_types(item, rules),
            "regions": detect_regions(item),
            "origin_region": detect_origin_region(item),
        }
        for tid in item.get("theme_ids", []):
            today_by_theme.setdefault(tid, []).append((item.get("id"), info))

    cutoff = _shift_date(today, -SIGNAL_WINDOW_DAYS)
    result = {}
    for tid, today_events in today_by_theme.items():
        entry = themes_state.setdefault(tid, {"first_seen": today, "occurrences": 0, "events": [], "milestones": {}})
        entry["occurrences"] = entry.get("occurrences", 0) + 1
        # 原則4: first_seenは一度設定したら絶対に上書きしない。
        entry.setdefault("first_seen", today)
        entry.setdefault("milestones", {})

        events = entry.setdefault("events", [])
        existing_ids = {e["item_id"] for e in events}
        for item_id, info in today_events:
            # 8種類の既存シグナル(info["signals"])とactor_types(政府/企業/
            # 研究機関/国際機関/海外政府/メディア)は別々の判定軸なので、
            # どちらか一方でも検出されていればイベント化する(実測バグ:
            # government_foreign等がsignalsを経由せず検出されるケースで、
            # signalsが空という理由だけでイベント自体が握りつぶされていた)。
            if (not info["signals"] and not info["actor_types"]) or not item_id or item_id in existing_ids:
                continue
            events.append({
                "date": today, "item_id": item_id, "source": info["source"],
                "signals": sorted(info["signals"]), "actor_types": sorted(info["actor_types"]),
                "regions": sorted(info["regions"]), "origin_region": info["origin_region"],
            })
            existing_ids.add(item_id)
            # ⑤成長履歴: 各主体種別を最初に観測した日を、一度記録したら
            # 削除・上書きしない形で永久に保持する(SIGNAL_WINDOW_DAYSの
            # 経過でeventsからは間引かれても、milestonesには残り続ける)。
            for actor_type in info["actor_types"]:
                entry["milestones"].setdefault(actor_type, today)

        # 有効期限切れのイベントを間引く(①新規性: 古い1回きりの
        # シグナルがいつまでも残り続けないようにする)。milestonesは
        # ここでは間引かない(永久保存)。
        entry["events"] = [e for e in events if e["date"] >= cutoff]
        events = entry["events"]

        distinct_signal_types = sorted({s for e in events for s in e["signals"]})
        distinct_sources = {e["source"] for e in events if e["source"]}
        distinct_actor_types = sorted({a for e in events for a in e["actor_types"]})
        # ②'情報源の種類の可視化: 種類のリストだけでなく、種類ごとに
        # 何件のイベントがあったかも見せる(「政府2・企業1」のように、
        # 「メディア5件」と「政府+企業+研究機関+海外+メディア」の違いが
        # 一目で分かるようにする)。
        actor_type_counts = {
            a: sum(1 for e in events if a in e["actor_types"]) for a in distinct_actor_types
        }
        distinct_regions = sorted({r for e in events for r in e["regions"]})
        distinct_origin_regions = sorted({e["origin_region"] for e in events if e["origin_region"]})
        window_counts = _window_counts(events, today)

        days_since_first = _days_between(entry["first_seen"], today)
        is_new_theme = (
            days_since_first <= EMERGING_MAX_DAYS and entry["occurrences"] <= EMERGING_MAX_OCCURRENCES
        )

        # ②独立性を反映したステージ判定: シグナル種別の多さだけでなく、
        # 別々の情報源(item["source"]の異なり数)で最低
        # MIN_INDEPENDENT_SOURCES件の裏付けがあることを必須にする
        # (同じ情報源からの複数回の発表・同じ話題の転載だけでは
        # 「独立した複数のシグナル」とは扱わない)。
        if (
            is_new_theme
            and len(distinct_sources) >= MIN_INDEPENDENT_SOURCES
            and len(distinct_signal_types) >= EMERGING_MIN_SIGNALS
        ):
            stage = "emerging"
        elif (
            len(distinct_sources) >= MIN_INDEPENDENT_SOURCES
            and len(distinct_signal_types) >= WATCH_MIN_SIGNALS
        ):
            stage = "watch"
        else:
            stage = None

        timeline = [
            {
                "date": e["date"], "source": e["source"],
                "signal_labels": [SIGNAL_TYPES.get(s, s) for s in e["signals"]],
                "actor_type_labels": [ACTOR_TYPES.get(a, a) for a in e["actor_types"]],
                "regions": e["regions"],
            }
            for e in sorted(events, key=lambda e: e["date"])
        ]
        # ⑤成長履歴を時系列で見せる(「9/20 政府→9/24 海外政府→…」)。
        # milestonesは永久保存なので、eventsが間引かれた後の古い区分も
        # ここには残り続ける。
        milestone_timeline = sorted(
            ({"date": d, "actor_type_label": ACTOR_TYPES.get(a, a)} for a, d in entry["milestones"].items()),
            key=lambda m: m["date"],
        )

        result[tid] = {
            "stage": stage,
            "stage_label": STAGE_LABEL.get(stage, ""),
            "signal_count": len(distinct_signal_types),
            "signal_labels": [SIGNAL_TYPES.get(s, s) for s in distinct_signal_types],
            "event_count": len(events),
            "source_count": len(distinct_sources),
            "actor_type_count": len(distinct_actor_types),
            "actor_type_labels": [ACTOR_TYPES.get(a, a) for a in distinct_actor_types],
            "actor_type_breakdown": [
                {"label": ACTOR_TYPES.get(a, a), "count": actor_type_counts[a]} for a in distinct_actor_types
            ],
            "region_count": len(distinct_regions),
            "regions": distinct_regions,
            "origin_regions": distinct_origin_regions,
            "first_seen": entry["first_seen"],
            "window_counts": window_counts,
            "timeline": timeline,
            "milestones": milestone_timeline,
        }
    return result


def best_stage_for_theme_ids(theme_ids, theme_stage_info):
    """1件のニュースが複数テーマに該当する場合、最も優先度の高いステージを選ぶ。"""
    best = None
    for tid in theme_ids:
        info = theme_stage_info.get(tid)
        if not info or not info.get("stage"):
            continue
        if best is None or STAGE_PRIORITY.get(info["stage"], 9) < STAGE_PRIORITY.get(best["stage"], 9):
            best = info
    return best
