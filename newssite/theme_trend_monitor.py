#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""萌芽シグナルの「監視」レポート(判定ロジックには一切影響しない)。

ユーザー方針(2026-09-20)「次にやるなら機能追加より監視」。しばらくは
以下を人間が確認できるようにする:
  ① 新興テーマがちゃんと検出されるか
  ② 誤検出が多くないか
  ③ 情報源の独立性が正しく判定されるか
  ④ milestonesが正しく蓄積されるか
  ⑤ 海外ソースがちゃんと入ってくるか
  ⑥ 同一ニュースの転載が重複カウントされないか(ユーザーが特に重要視)

このモジュールはdev.py monitorから手動で実行する読み取り専用のレポート
であり、CIの自動テストゲート(python3 -m unittest discover)には含めない
――疑わしいものを検出しても、それだけで本番pushを止めるべきではない
(判定条件そのものは変えない、というこれまでの方針と同じ理由)。
"""
from . import rss as rss_mod
from . import theme_trends


def audit_repost_collisions(registry):
    """⑥ 同一ニュースの転載が「異なる情報源」として重複カウントされて
    いないかを調べる。

    同じテーマ内で、情報源(source)が異なる2つのイベントの見出しが
    rss.py既存の同一トピック判定(_same_topic)で「同じ話題」と判定
    されるなら、それは実際には1つの出来事を複数のポータル/媒体が
    書き方を変えて転載しただけで、独立した2つの情報源とは言えない
    可能性がある――というフラグを立てるだけの読み取り専用チェック。
    (rss.collect()自体の同日内での同一トピック統合は既に効いている
    はずなので、ここで検出できるのは主に「別の日に別ポータルが同じ
    一次情報を書き直して再配信した」ような、既存の統合をすり抜けた
    ケース)。

    戻り値: [{"theme_id":, "source_a":, "source_b":, "title_a":, "title_b":}, ...]
    """
    flags = []
    for theme_id, entry in registry.get("themes", {}).items():
        events = [e for e in entry.get("events", []) if e.get("source") and e.get("title")]
        normed = [(e, rss_mod._normalize(e["title"])) for e in events]
        for i in range(len(normed)):
            event_a, norm_a = normed[i]
            for event_b, norm_b in normed[i + 1:]:
                if event_a["source"] == event_b["source"]:
                    continue  # 同じ情報源はそもそも独立性判定に1件しか寄与しない
                if rss_mod._same_topic(norm_a, norm_b):
                    flags.append({
                        "theme_id": theme_id,
                        "source_a": event_a["source"], "title_a": event_a["title"],
                        "source_b": event_b["source"], "title_b": event_b["title"],
                    })
    return flags


def summarize(registry, theme_stage_info):
    """①②③④⑤を人間が一目で確認できるテキストレポートを返す
    (行のリスト。判定条件はtheme_trends.py側のものをそのまま使うだけで
    ここでは何も計算し直さない)。
    """
    lines = []
    themes = sorted(
        theme_stage_info.items(),
        key=lambda kv: (theme_trends.STAGE_PRIORITY.get(kv[1].get("stage"), 9), -kv[1].get("source_count", 0)),
    )
    for theme_id, info in themes:
        stage_label = info.get("stage_label") or "(未到達)"
        foreign = sum(
            b["count"] for b in info.get("actor_type_breakdown", [])
            if b["label"] in ("海外政府・当局", "国際機関")
        )
        lines.append(
            f"{theme_id:20s} {stage_label:10s} "
            f"情報源{info.get('source_count', 0)}件 / シグナル{info.get('signal_count', 0)}種 / "
            f"milestones{len(info.get('milestones', []))}件 / 海外系{foreign}件 / "
            f"地域{','.join(info.get('regions', [])) or '-'}"
        )
    return lines


def print_report(registry=None, theme_stage_info=None):
    if registry is None:
        registry = theme_trends._load_registry()

    print("=== THEME_TREND_MONITOR ===")
    print(f"登録テーマ数: {len(registry.get('themes', {}))}")

    if theme_stage_info is not None:
        print("\n--- ①②③④⑤ テーマごとの状態 ---")
        for line in summarize(registry, theme_stage_info):
            print(line)
    else:
        print("\n(theme_stage_infoが無いため②③の一覧表示は省略。件数のみのレジストリ監査)")

    print("\n--- ⑥ 同一ニュースの転載が別情報源として重複カウントされていないか ---")
    flags = audit_repost_collisions(registry)
    if not flags:
        print("疑わしい重複は見つかりませんでした。")
    else:
        for f in flags:
            print(f"[要確認] {f['theme_id']}: 「{f['source_a']}」{f['title_a']}")
            print(f"                  ⇔「{f['source_b']}」{f['title_b']}")
