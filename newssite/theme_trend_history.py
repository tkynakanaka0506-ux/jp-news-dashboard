#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""萌芽シグナルの日次スナップショット記録。

ユーザー方針(2026-09-20)「今は、余計な判定を追加せずデータを貯める
段階でいい」への対応。theme_trends.record_and_classify()が返す
テーマごとの診断情報(stage/source_count/signal_count/actor_type内訳/
milestones/window_counts等)を、判定ロジックには一切手を加えず、
1日1テーマにつき1行だけ追記する(同じ日に複数回ビルドしても2行目以降は
書かない。backtest.pyのevent_keyによる重複排除と同じ考え方)。

このログの用途は2つ:
  1. 「監視」(2026-09-20ユーザー要望): 新興テーマが実際に検出されて
     いるか・milestonesが正しく蓄積されているか・海外ソースが入って
     きているか等を、日をまたいで後から確認できるようにする。
  2. 「Emerging Theme Backtest」(2026-09-20ユーザー要望、次フェーズ):
     初検知から7/14/30/90日後の実際の状態(拡大/継続/停滞/消滅/大型化)
     を追跡するには、その時点その時点のスナップショットが要る。今の
     うちに貯めておかないと、後から遡って作ることはできない。

[PRESENTATION LAYER] ここで記録する内容はあくまで観測記録であり、
theme_trends.py側の判定条件(しきい値)には一切影響しない。
"""
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
HISTORY_PATH = DATA_DIR / "theme_trend_history.jsonl"

# スナップショットとして保存するフィールド。theme_trends.record_and_classify()
# の戻り値をそのまま流用する(判定ロジックを二重管理しない)。
_SNAPSHOT_FIELDS = (
    "stage", "signal_count", "event_count", "source_count",
    "actor_type_count", "actor_type_breakdown", "region_count", "regions",
    "origin_regions", "first_seen", "window_counts", "milestones", "growth",
)


def record_snapshot(theme_stage_info, day, path=HISTORY_PATH):
    """今日時点の各テーマの状態を1テーマ1行で追記する。

    同じ(day, theme_id)の組が既にあれば追記しない(1日に複数回ビルド
    しても、後からの分析で「1日=1点」として扱えるようにするため)。
    """
    existing_keys = _load_existing_keys(path)
    new_rows = []
    for theme_id, info in theme_stage_info.items():
        key = f"{day}|{theme_id}"
        if key in existing_keys:
            continue
        row = {"day": day, "theme_id": theme_id}
        row.update({field: info.get(field) for field in _SNAPSHOT_FIELDS})
        new_rows.append(row)

    if not new_rows:
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for row in new_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(new_rows)


def _load_existing_keys(path=HISTORY_PATH):
    if not path.exists():
        return set()
    keys = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            keys.add(f"{row.get('day', '')}|{row.get('theme_id', '')}")
    return keys


def load_snapshots(path=HISTORY_PATH):
    """蓄積したスナップショットを日付昇順で返す(監視・将来のバック
    テスト分析で読み込む入口をここに1つにまとめておく)。
    """
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    rows.sort(key=lambda r: (r.get("day", ""), r.get("theme_id", "")))
    return rows
