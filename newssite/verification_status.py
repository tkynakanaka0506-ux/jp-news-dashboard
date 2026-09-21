#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""萌芽シグナル・監視基盤の「検証・開発ステータス」パネル用の集計
(2026-09-21ユーザー要望「今どこまで完成しているのか・データが十分に
集まったのか・次のバックテスト段階へ進める状態になったのか、が一目で
分かるようにボード上に自動表示してほしい」)。

[このモジュールが扱うのは開発プロジェクト自体の進捗だけ] ニュース判定・
銘柄影響判定・萌芽シグナルの判定条件(theme_trends.py)には一切関与
しない、完全に別軸の表示専用ロジック。新しい投資判断・スコア・予測は
一切作らない(ユーザー方針を継続)。

■ 実装前の調査で確認した既存データとの関係(重複実装を避けるため)
  - theme_trend_history.jsonl(theme_trend_history.py): 判定を追加せず
    1日1テーマ1行だけ記録する既存の日次スナップショット。ここでの集計は
    このファイルを読むだけで、書き込み・スキーマ変更は一切行わない。
  - theme_trend_registry.json/milestones/first_seen: 現在アクティブな
    テーマの状態(theme_trends.py側)。events は SIGNAL_WINDOW_DAYS(90日)
    で間引かれるため、「テーマが過去にどれだけ育ったか」を後から遡って
    見るにはtheme_trend_history.jsonl(間引かれない)の方が向いている。
    ここではregistryを直接読まず、historyだけを唯一の入力源にする。
  - dev.py monitor/monitor.mjs: 「パイプラインが動いているか」を見る
    既存の稼働監視。このモジュールが扱う「検証プロジェクトの進捗
    フェーズ」とは別の関心事だが、この機能自身の鮮度もdev.py monitorの
    永続化ファイル一覧に加えることで「監視自身の監視」に含める
    (2026-09-21ユーザー要望)。

■ 「完成」の判定方針(ユーザー方針: 単純な日数経過だけで判断しない)
  「バックテストに使える」と言うには、①first_seenから十分な日数が
  経過していること(TRACKABLE_MIN_DAYS)と、②その期間中に実際に意味の
  ある信号があったこと(独立情報源2件以上、またはmilestones2件以上=
  単発の言及で終わっていない)の両方が要る。日数だけ経過して信号が
  薄いテーマを「追跡可能」に数えると、後のバックテストが「差が無い
  データを比較しているだけ」になってしまうため。

■ 永久保存するもの(first_seenと同じsetdefault方式)
  各フェーズに「初めて到達した日」を1回だけ記録する
  (newssite/data/verification_status.json)。後から「いつデータが
  十分に集まって、いつバックテスト可能になったのか」を追跡できるように
  するため、一度到達した日付は絶対に上書きしない。
"""
import json
from datetime import datetime
from pathlib import Path

from . import theme_trend_history
from .config import JST

DATA_DIR = Path(__file__).resolve().parent / "data"
STATUS_PATH = DATA_DIR / "verification_status.json"

# 「経過日数だけで判断しない」の実装: この日数以上経過し、かつ実際に
# 意味のある信号(下記MIN_SOURCES_FOR_MEANINGFUL/MIN_MILESTONES_FOR_MEANINGFUL
# のいずれか)があったテーマだけを「追跡可能(trackable)」に数える。
TRACKABLE_MIN_DAYS = 30
MIN_SOURCES_FOR_MEANINGFUL = 2  # theme_trends.MIN_INDEPENDENT_SOURCESと同じ考え方
MIN_MILESTONES_FOR_MEANINGFUL = 2

# 「比較に値する最低限のサンプル数」。1〜2件では「たまたま」と区別できない。
MIN_TRACKABLE_THEMES_FOR_BACKTEST = 5

AGE_CHECKPOINTS = (7, 14, 30, 90)

PHASE_LABEL = {
    "accumulating": "データ蓄積中",
    "ready_for_backtest": "分析可能",
    "backtest_done": "バックテスト完了",
}


def _parse_day(day_str):
    return datetime.strptime(day_str, "%Y-%m-%d")


def _theme_summaries(history_rows):
    """theme_trend_history.jsonlのスナップショット行をtheme_id単位に
    まとめる。「過去のピーク時にどれだけ育ったか」を見るため、
    source_count/milestones件数はそのテーマの全期間の最大値を取る
    (最新のスナップショットだけだと、一時的に盛り上がって収束した
    テーマの実績を見落とすため)。
    """
    by_theme = {}
    for row in history_rows:
        tid = row.get("theme_id")
        if not tid:
            continue
        entry = by_theme.setdefault(tid, {
            "first_seen": row.get("first_seen"),
            "max_source_count": 0,
            "max_milestone_count": 0,
        })
        # first_seenはtheme_trends.py側でsetdefault済み(上書きされない)の
        # はずだが、念のため一番古い値を採用する。
        if row.get("first_seen") and (not entry["first_seen"] or row["first_seen"] < entry["first_seen"]):
            entry["first_seen"] = row["first_seen"]
        entry["max_source_count"] = max(entry["max_source_count"], row.get("source_count") or 0)
        entry["max_milestone_count"] = max(entry["max_milestone_count"], len(row.get("milestones") or []))
    return by_theme


def compute_status(history_rows=None, today=None):
    """ニュース判定には使わない、開発プロジェクトの進捗集計だけを返す。"""
    history_rows = theme_trend_history.load_snapshots() if history_rows is None else history_rows
    today_dt = _parse_day(today) if today else datetime.now(JST).replace(tzinfo=None)

    summaries = _theme_summaries(history_rows)
    total_themes = len(summaries)

    age_ge = {n: 0 for n in AGE_CHECKPOINTS}
    multi_source_themes = 0
    milestone_themes = 0
    trackable_for_backtest = 0

    for entry in summaries.values():
        first_seen = entry["first_seen"]
        age_days = (today_dt - _parse_day(first_seen)).days if first_seen else 0
        for n in AGE_CHECKPOINTS:
            if age_days >= n:
                age_ge[n] += 1
        has_multi_source = entry["max_source_count"] >= MIN_SOURCES_FOR_MEANINGFUL
        has_milestones = entry["max_milestone_count"] >= 1
        if has_multi_source:
            multi_source_themes += 1
        if has_milestones:
            milestone_themes += 1
        meaningful = has_multi_source or entry["max_milestone_count"] >= MIN_MILESTONES_FOR_MEANINGFUL
        if age_days >= TRACKABLE_MIN_DAYS and meaningful:
            trackable_for_backtest += 1

    phase = "ready_for_backtest" if trackable_for_backtest >= MIN_TRACKABLE_THEMES_FOR_BACKTEST else "accumulating"

    return {
        "phase": phase,
        "phase_label": PHASE_LABEL[phase],
        "total_themes": total_themes,
        "age_ge": age_ge,
        "multi_source_themes": multi_source_themes,
        "milestone_themes": milestone_themes,
        "trackable_for_backtest": trackable_for_backtest,
        "trackable_min_days": TRACKABLE_MIN_DAYS,
        "min_trackable_themes_for_backtest": MIN_TRACKABLE_THEMES_FOR_BACKTEST,
    }


def _load_status_file(path=STATUS_PATH):
    if not path.exists():
        return {"milestones": {}}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"milestones": {}}
    data.setdefault("milestones", {})
    return data


def _save_status_file(data, path=STATUS_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)


def record_and_get_status(history_rows=None, today=None, persist=True, status_path=STATUS_PATH):
    """compute_status()の結果に、永久保存された「各フェーズに初めて
    到達した日」(milestones)を合わせて返す。first_seenと全く同じ
    setdefault方式(一度到達した日付は絶対に上書きしない)。

    status_path: テスト用に差し替え可能(本番のverification_status.json
    を汚さずに単体テストできるようにするため)。
    """
    today_str = today or datetime.now(JST).strftime("%Y-%m-%d")
    computed = compute_status(history_rows, today_str)

    status_data = _load_status_file(status_path)
    milestones = status_data["milestones"]
    milestones.setdefault("accumulating", today_str)
    if computed["phase"] in ("ready_for_backtest", "backtest_done"):
        milestones.setdefault("ready_for_backtest", today_str)
    if computed["phase"] == "backtest_done":
        milestones.setdefault("backtest_done", today_str)

    if persist:
        _save_status_file(status_data, status_path)

    return {
        **computed,
        "milestones": dict(milestones),
        "last_computed_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
