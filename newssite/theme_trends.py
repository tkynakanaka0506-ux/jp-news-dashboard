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
    │
    ├─ 政府・省庁の一次情報 ↑
    ├─ 政策の検討・審議段階 ↑
    ├─ 研究開発の進展       ↑
    ├─ 特許                 ↑
    ├─ 企業の設備投資       ↑
    ├─ 海外の動き           ↑
    ├─ 関連企業の直接発表   ↑
    └─ 複数媒体での同時報道 ↑
              ↓
        シグナル集中
              ↓
        🔎 新興テーマ / 👀 要監視テーマ

「このテーマは重要になる」と予言するのではなく、「独立した根拠が
いくつ観測できているか」を数えて事実として提示するだけ。件数と
根拠(signal_labels)を必ず併記し、判定の中身がブラックボックスに
ならないようにする。

同じテーマ(theme_id)が過去に何回・どんな角度で出現したかを日をまたいで
覚えておく必要があるため、policy_lifecycle.py と同じ「登録簿(registry)を
JSONで永続化する」方式を踏襲する。CI(GitHub Actions)側でこのファイルを
gitにコミットし忘れると、毎回まっさらな登録簿から始まってしまい
「新興/要監視」判定が機能しなくなる(実測: policy_event_registry.jsonが
これまでコミットされておらず同じ理由で機能していなかった不具合の再発防止。
.github/workflows/update.ymlのgit addに両レジストリを含めること)。
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

from . import impact as impact_mod

DATA_DIR = Path(__file__).resolve().parent / "data"
REGISTRY_PATH = DATA_DIR / "theme_trend_registry.json"

# シグナルの有効期限。この日数以内に再確認されなかったシグナルは
# 「今も生きている根拠」とはみなさない(大昔の1回きりの言及で
# 永久に「要監視」のままになるのを防ぐ)。
SIGNAL_WINDOW_DAYS = 45

# テーマの初出からこの日数以内・累計出現回数がこの件数以下なら「新興(まだ
# 大きく報じられていない)」候補として扱う。
EMERGING_MAX_DAYS = 14
EMERGING_MAX_OCCURRENCES = 6

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


def detect_signals(item, rules):
    """1件のニュースから検出できる独立したシグナルの集合を返す。

    すでに計算済みのフィールド(source_tier/future_signal/policy_maturity/
    impacts/related/category)をそのまま読むだけで、新しい推測は行わない。
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


def record_and_classify(registry, news, rules, today):
    """今回のnewsに含まれる各テーマの出現・シグナルを登録簿に記録し、
    テーマごとのステージを返す({theme_id: {"stage", "stage_label",
    "signal_count", "signal_labels"}})。

    registry はこの関数が破壊的に更新する(呼び出し側で_save_registryする)。
    """
    themes_state = registry.setdefault("themes", {})
    today_signals_by_theme = {}
    for item in news:
        item_signals = detect_signals(item, rules)
        for tid in item.get("theme_ids", []):
            today_signals_by_theme.setdefault(tid, set()).update(item_signals)

    cutoff = _shift_date(today, -SIGNAL_WINDOW_DAYS)
    result = {}
    for tid, today_signals in today_signals_by_theme.items():
        entry = themes_state.setdefault(tid, {"first_seen": today, "occurrences": 0, "signals": {}})
        entry["occurrences"] = entry.get("occurrences", 0) + 1
        for sig in today_signals:
            entry["signals"][sig] = today
        # 有効期限切れのシグナルを間引く
        entry["signals"] = {s: d for s, d in entry["signals"].items() if d >= cutoff}

        active_signals = sorted(entry["signals"].keys())
        days_since_first = _days_between(entry["first_seen"], today)
        is_new_theme = (
            days_since_first <= EMERGING_MAX_DAYS and entry["occurrences"] <= EMERGING_MAX_OCCURRENCES
        )

        if is_new_theme and len(active_signals) >= EMERGING_MIN_SIGNALS:
            stage = "emerging"
        elif len(active_signals) >= WATCH_MIN_SIGNALS:
            stage = "watch"
        else:
            stage = None

        result[tid] = {
            "stage": stage,
            "stage_label": STAGE_LABEL.get(stage, ""),
            "signal_count": len(active_signals),
            "signal_labels": [SIGNAL_TYPES.get(s, s) for s in active_signals],
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
