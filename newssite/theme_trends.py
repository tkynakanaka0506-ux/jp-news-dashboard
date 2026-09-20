#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""萌芽シグナル検知: まだ大きなニュースになっていないテーマを早期に見つける。

[PRESENTATION LAYER] ここでの判定は表示・優先度付けにのみ使う。
impact.pyのdirection/theme/direct-indirect(FACTUAL LAYER)判定には
一切使わない・影響しない(rules.jsonの_comment_layering_principle参照)。

ユーザー方針(2026-09-20)「『ビッグニュースになる可能性が高い』を最初から
予言させる設計にはしない方がいい。代わりに『先行シグナルが何個集まって
いるか』を検出する」に基づく設計:

    核融合
    9/1  政府一次情報        ●
    9/4  研究開発            ●
    9/8  海外政府            ●
    9/12 企業発表            ●
    9/15 特許                ●
    9/18 複数媒体報道        ●
              ↓
    「単発ニュース」ではなく「複数方面から継続的にシグナル発生」
    という状態を、観測できた事実(件数・時期・情報源)として提示する。
    「これは重要になる」と予言する一つのスコアには潰さない。

2026-09-20の追加要望に基づく設計:
  ① 新規性: 過去7/30/90日でシグナルの発生件数を分けて出す(古い1回きりの
     シグナルがいつまでも残り続けない)。
  ② 独立性: 同じニュース(item_id)が複数の観点を満たしていても、それは
     「1件の裏付け」として数える。同じ話題の転載(rss.pyが既に統合済み)を
     二重に数えない。異なるitem_id(=独立した発生源)が複数そろって
     初めて「独立したシグナルが集まっている」と数える。
  ③ 加速: 直近7日・30日・90日の件数を並べて出すことで、「最近になって
     複数方面から集中し始めた」状態を見えるようにする。単一の伸び率や
     スコアには合成しない(それ自体が一種の予言になるため)。
  ④ 国際的な広がり: 見出しに含まれる国・地域名を検出し、何か国・地域から
     シグナルが上がっているかを数える。
  ⑤ 前段階の保存: テーマを最初に検知した日(first_seen)は一度設定したら
     絶対に上書きしない。後から「市場が注目する何日前に検知していたか」
     を検証できるようにする。

いずれも新しい予測ロジックではなく、既存の計算済みフィールドと少数の
汎用キーワードから「観測できた事実」を集計するだけ(表示専用)。
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

from . import impact as impact_mod

DATA_DIR = Path(__file__).resolve().parent / "data"
REGISTRY_PATH = DATA_DIR / "theme_trend_registry.json"

# シグナルの有効期限。この日数より前のイベントは「今も生きている根拠」
# とはみなさない(大昔の1回きりの言及で永久に「要監視」のままになるのを
# 防ぐ)。
SIGNAL_WINDOW_DAYS = 90

# ③加速の表示に使う、入れ子の集計ウィンドウ(日数)。短い順。
TREND_WINDOWS = (7, 30, 90)

# テーマの初出からこの日数以内・累計出現回数がこの件数以下なら「新興(まだ
# 大きく報じられていない)」候補として扱う。
EMERGING_MAX_DAYS = 14
EMERGING_MAX_OCCURRENCES = 6

# ②独立性: 「複数方面からのシグナル」と呼ぶには、最低でも別々のニュース
# (item_id)による裏付けが要る。1件の記事が複数のシグナル種別を満たして
# いても、それだけでは1件の裏付けにしかならない。
MIN_INDEPENDENT_EVENTS = 2

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

STAGE_LABEL = {
    "emerging": "🔎 新興テーマ",
    "watch": "👀 要監視テーマ",
}

STAGE_PRIORITY = {"emerging": 0, "watch": 1, None: 9}

_FOREIGN_CATEGORIES = {"geopolitics", "us"}
_EARLY_POLICY_MATURITY_MAX = 45  # 「検討・審議会」段階(rules.jsonのpolicy_maturity_stages参照)

# ④国際的な広がり。見出しに含まれる国・地域名から、どの国・地域発の
# 動きかを大まかに推定する(厳密な言語処理はせず、代表的な呼称の部分
# 一致だけを見る)。1つの見出しが複数の地域に一致してもよい。
REGION_KEYWORDS = {
    "日本": ["日本", "経済産業省", "国土交通省", "財務省", "金融庁", "首相官邸", "内閣府"],
    "米国": ["米国", "アメリカ", "米政府", "米商務省", "米エネルギー省", "米国防総省", "ホワイトハウス", "FRB", "FOMC"],
    "EU/欧州": ["EU", "欧州委員会", "欧州連合", "欧州"],
    "英国": ["英国", "イギリス"],
    "中国": ["中国", "中国政府"],
    "韓国": ["韓国", "韓国政府"],
    "台湾": ["台湾"],
}


def detect_signals(item, rules):
    """1件のニュースから検出できる独立した「観点」の集合を返す。

    すでに計算済みのフィールド(source_tier/future_signal/policy_maturity/
    impacts/related/category)をそのまま読むだけで、新しい推測は行わない。
    1件の記事がここで複数の観点(signal type)を満たしても、独立性の
    担保はrecord_and_classify側(item_idベースのイベント集約)で行う
    ため、ここでは純粋に「この記事にどんな角度が書かれているか」だけを見る。
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


def detect_regions(item):
    """見出しに含まれる国・地域名から、どの国・地域発の動きかを推定する
    (④国際的な広がり用)。厳密な地名抽出ではなく代表的な呼称の部分一致。
    """
    title = item.get("title", "")
    return {region for region, keywords in REGION_KEYWORDS.items() if any(k in title for k in keywords)}


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
    """③加速: 直近7/30/90日でそれぞれ何件の独立したイベント(item_id)が
    あったかを数える。件数を並べて出すだけで、伸び率やスコアには
    合成しない(単一指標に潰すと、それ自体が一種の予言になるため)。
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
    独立性(②)を担保するため、記録の単位は「シグナル種別」ではなく
    「(item_id, その記事が持つシグナル・地域)」のイベントにする。同じ
    item_id は1回しかイベント化されない(rss.py側で既に同一話題の転載は
    1件のニュースに統合済みなので、ここでの二重カウントは基本的に
    「同じ記事が複数の観点を同時に満たす」ケースだけになる。それも
    1イベントとして扱うことで、1本の記事が複数のシグナル種別を名乗って
    独立した複数の裏付けであるかのように見えることを防ぐ)。
    """
    themes_state = registry.setdefault("themes", {})
    today_by_theme = {}
    for item in news:
        item_signals = detect_signals(item, rules)
        item_regions = detect_regions(item)
        for tid in item.get("theme_ids", []):
            today_by_theme.setdefault(tid, []).append((item.get("id"), item_signals, item_regions))

    cutoff = _shift_date(today, -SIGNAL_WINDOW_DAYS)
    result = {}
    for tid, today_events in today_by_theme.items():
        entry = themes_state.setdefault(tid, {"first_seen": today, "occurrences": 0, "events": []})
        entry["occurrences"] = entry.get("occurrences", 0) + 1
        # ⑤前段階の保存: first_seenは一度設定したら絶対に上書きしない。
        entry.setdefault("first_seen", today)

        events = entry.setdefault("events", [])
        existing_ids = {e["item_id"] for e in events}
        for item_id, sigs, regions in today_events:
            if not sigs or not item_id or item_id in existing_ids:
                continue
            events.append({
                "date": today, "item_id": item_id,
                "signals": sorted(sigs), "regions": sorted(regions),
            })
            existing_ids.add(item_id)

        # 有効期限切れのイベントを間引く(①新規性: 古い1回きりの
        # シグナルがいつまでも残り続けないようにする)。
        entry["events"] = [e for e in events if e["date"] >= cutoff]
        events = entry["events"]

        distinct_signal_types = sorted({s for e in events for s in e["signals"]})
        distinct_event_ids = {e["item_id"] for e in events}
        distinct_regions = sorted({r for e in events for r in e["regions"]})
        window_counts = _window_counts(events, today)

        days_since_first = _days_between(entry["first_seen"], today)
        is_new_theme = (
            days_since_first <= EMERGING_MAX_DAYS and entry["occurrences"] <= EMERGING_MAX_OCCURRENCES
        )

        # ②独立性を反映したステージ判定: シグナル種別の多さだけでなく、
        # 別々のニュース(item_id)で最低MIN_INDEPENDENT_EVENTS件の裏付けが
        # あることを必須にする(1本の記事が複数の観点を満たすだけでは
        # 「独立した複数のシグナル」とは扱わない)。
        if (
            is_new_theme
            and len(distinct_event_ids) >= MIN_INDEPENDENT_EVENTS
            and len(distinct_signal_types) >= EMERGING_MIN_SIGNALS
        ):
            stage = "emerging"
        elif (
            len(distinct_event_ids) >= MIN_INDEPENDENT_EVENTS
            and len(distinct_signal_types) >= WATCH_MIN_SIGNALS
        ):
            stage = "watch"
        else:
            stage = None

        timeline = [
            {"date": e["date"], "signal_labels": [SIGNAL_TYPES.get(s, s) for s in e["signals"]], "regions": e["regions"]}
            for e in sorted(events, key=lambda e: e["date"])
        ]

        result[tid] = {
            "stage": stage,
            "stage_label": STAGE_LABEL.get(stage, ""),
            "signal_count": len(distinct_signal_types),
            "signal_labels": [SIGNAL_TYPES.get(s, s) for s in distinct_signal_types],
            "event_count": len(distinct_event_ids),
            "region_count": len(distinct_regions),
            "regions": distinct_regions,
            "first_seen": entry["first_seen"],
            "window_counts": window_counts,
            "timeline": timeline,
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
