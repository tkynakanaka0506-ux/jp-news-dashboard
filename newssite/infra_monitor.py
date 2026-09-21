#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""パイプライン基盤(GitHub Actions・ローカルwatchdog・永続化ファイル)の
稼働監視(2026-09-21ユーザー要望「機能追加より長期的な安定稼働の監視・
再発防止を優先」)。

[このモジュールが扱うのはインフラの稼働状況だけ] ニュース判定・萌芽
シグナル・つながっている材料・テーマ分析等の判定ロジックには一切関与
しない、読み取り専用のレポート(theme_trend_monitor.pyが扱う「テーマの
検出は正しいか」とは別の関心事)。

実測(2026-09-21): GitHub Actionsのschedule triggerは想定(日中30分おき・
夜間2時間おき)に対し、実際は26分〜462分とばらつき、想定トリガー数の
約35%しか発火していなかった(公式にも既知の制約として明記されている、
高負荷時の遅延・ドロップ)。再発防止としてローカルのlaunchd watchdog
(watchdog_check.sh・com.takuya.news-watchdog)を導入済みなので、ここでは
「両方合わせて実際に更新が続いているか」を人間が確認できるようにする。

重複監視を避けるための設計判断:
  - 「PERSISTENT_STATE_FILESに全ファイルが登録されているか」は
    tests/test_pipeline.pyのCIConfigTestが既にCIの毎回のテストで
    保証している(構成ミスは配線時点でテストが落ちて気づける)。ここで
    同じチェックを再実装しない。
  - このモジュールが追加で見るのは、テストでは検出できない「運用上
    実際に更新され続けているか」(コミット頻度・生成時刻・watchdogの
    実発火回数)という、実行時にしか分からない情報だけに絞る。
"""
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
REPO_ROOT = DATA_DIR.parent.parent

NEWS_URL = "https://tkynakanaka0506-ux.github.io/jp-news-dashboard/news.json"
REPO = "tkynakanaka0506-ux/jp-news-dashboard"
WATCHDOG_LOG_PATH = Path.home() / "Library" / "Logs" / "jp-news-watchdog.log"

# watchdog_check.shのSTALE_THRESHOLD_MINと同じ値。2つの言語(bash/Python)
# にまたがるため関数としては共有できないが、意味としては同じ閾値を指す
# ことをコメントで明記しておく(値がズレたら両方直すこと)。
STALE_THRESHOLD_MIN = 180

# update.ymlのschedule:定義の転記(推測ではなく設定ファイルの値をそのまま
# 使う)。日中(JST 8:07〜15:37、30分おき)+夜間(8つの時刻、実質2時間おき)。
EXPECTED_TRIGGERS_PER_DAY = 16 + 8  # 24

# 日をまたいで状態を持つファイル一覧。.github/workflows/update.ymlの
# PERSISTENT_STATE_FILESと同じ対象を指す(値の重複は避けられないが、
# 「登録されているか」の検証自体はCIConfigTest側だけで行い、ここでは
# 実際のコミット鮮度だけを見る)。
PERSISTENT_DATA_FILES = (
    "policy_event_registry.json",
    "theme_trend_registry.json",
    "backtest_events.jsonl",
    "policy_catalyst_signals.json",
    "theme_trend_history.jsonl",
    "verification_status.json",
)

DEFAULT_STALE_HOURS = 48
# theme_trend_history.jsonlは「1日1テーマ1行」の重複排除設計なので、
# 新しい日付/テーマが現れない限り丸1日以上コミットが無くても正常
# (他の4ファイルとは更新頻度の前提が違う)。
# verification_status.jsonはさらに更新頻度が低い設計: 各フェーズに
# 「初めて到達した日」を一度だけ記録するsetdefault方式(first_seenと
# 同じ)なので、フェーズが変わらない限り数週間〜数ヶ月コミットが
# 無くても正常(2026-09-21ユーザー要望の検証ステータス機能)。
STALE_HOURS_OVERRIDE = {
    "theme_trend_history.jsonl": 30,
    "verification_status.json": 24 * 60,  # 60日
}


def fetch_live_generated_at(timeout=15):
    """本番サイトのnews.jsonからgenerated_isoを取る。取得できなければNone
    (取得失敗を「停止している」と決めつけず、不明として扱う)。

    urllib.request(標準ライブラリ)ではなくcurlをsubprocess経由で使う。
    python.org配布のPythonはOS標準の信頼ストアを見ないため、
    CERTIFICATE_VERIFY_FAILEDで失敗する環境が実際にあった(実測)。
    curlはOSの信頼ストアを使うため確実に通る。watchdog_check.sh
    (bash側)も同じ理由で最初からcurlを使っている。
    """
    out = _run(["curl", "-fsSL", "--max-time", str(timeout), NEWS_URL])
    if out is None:
        return None
    try:
        return json.loads(out).get("generated_iso")
    except json.JSONDecodeError:
        return None


def _minutes_since(iso_str, now=None):
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
    except ValueError:
        return None
    now = now or datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    return (now - dt).total_seconds() / 60


def _run(cmd, cwd=None, timeout=15):
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
        if result.returncode != 0:
            return None
        return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def fetch_recent_workflow_runs(limit=200):
    """`gh` CLI経由で直近の実行履歴を取る。ghが無い/失敗したらNone
    (実測できないだけで、判定条件を推測で埋めない方針と同じ)。
    """
    out = _run([
        "gh", "run", "list", "--workflow=update.yml", "-R", REPO, "--limit", str(limit),
        "--json", "createdAt,event,conclusion,status",
    ])
    if out is None:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def count_schedule_triggers_within(runs, hours):
    if runs is None:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    count = 0
    for r in runs:
        if r.get("event") != "schedule":
            continue
        try:
            created = datetime.strptime(r["createdAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        if created >= cutoff:
            count += 1
    return count


def _tail_watchdog_log(path=WATCHDOG_LOG_PATH):
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return f.readlines()
    except OSError:
        return []


def watchdog_status(path=WATCHDOG_LOG_PATH):
    """ローカルwatchdog(watchdog_check.sh)のログから、最終確認時刻・
    直近24時間/7日間の手動発火(workflow_dispatch)回数を集計する。
    """
    lines = _tail_watchdog_log(path)
    if not lines:
        return {"last_check": None, "dispatch_count_24h": 0, "dispatch_count_7d": 0, "log_found": False}

    last_line = lines[-1].strip()
    last_check = last_line[:19] if len(last_line) >= 19 else None

    now = datetime.now()
    dispatch_24h = dispatch_7d = 0
    for line in lines:
        if "workflow_dispatch成功" not in line:
            continue
        ts_str = line[:19]
        try:
            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        age_hours = (now - ts).total_seconds() / 3600
        if age_hours <= 24:
            dispatch_24h += 1
        if age_hours <= 24 * 7:
            dispatch_7d += 1

    return {
        "last_check": last_check,
        "dispatch_count_24h": dispatch_24h,
        "dispatch_count_7d": dispatch_7d,
        "log_found": True,
    }


def persistent_file_git_freshness(repo_root=REPO_ROOT, files=PERSISTENT_DATA_FILES):
    """各永続化ファイルが直近いつコミットされたかをgit logで確認する。
    CIが動いていても「このファイルだけ更新されなくなった」を検出する
    (実測バグ再発防止: policy_event_registry.json等が一度もコミット
    されておらず「続報」判定が本番で機能していなかった過去の事故と
    同種の再発を、テストではなく運用時に検出する)。
    """
    results = {}
    for name in files:
        rel_path = f"newssite/data/{name}"
        out = _run(["git", "log", "-1", "--format=%ct", "--", rel_path], cwd=repo_root)
        if not out or not out.strip():
            results[name] = {"last_commit": None, "hours_ago": None}
            continue
        try:
            last_ts = int(out.strip())
        except ValueError:
            results[name] = {"last_commit": None, "hours_ago": None}
            continue
        hours_ago = (datetime.now().timestamp() - last_ts) / 3600
        results[name] = {
            "last_commit": datetime.fromtimestamp(last_ts).strftime("%Y-%m-%d %H:%M"),
            "hours_ago": hours_ago,
        }
    return results


def classify_status(age_min):
    """更新の新しさから🟢🟡🟠🔴を機械的に決めるだけの表示用分類。
    投資判断・ニュース判定のロジックには一切流用しない(このモジュール
    専用の、判定基準が全く別物であることを明記しておく)。
    """
    if age_min is None:
        return "🟠", "本番サイトへの到達確認自体に失敗(ネットワーク/サイト側の問題の可能性)"
    if age_min <= 90:
        return "🟢", "正常"
    if age_min <= STALE_THRESHOLD_MIN:
        return "🟡", "遅延(想定30分〜2時間おきに対しやや間隔が開いている)"
    if age_min <= STALE_THRESHOLD_MIN * 2:
        return "🟠", "異常の兆候(watchdogの閾値を超過。次のwatchdog実行で自動発火するはず)"
    return "🔴", "停止(watchdogも含めて長時間更新が無い。手動確認が必要)"


def build_report():
    """dev.py monitorから呼ぶ、①ニュースボードのインフラ稼働状況の
    レポート一式(表示用の辞書)。
    """
    generated_iso = fetch_live_generated_at()
    age_min = _minutes_since(generated_iso)
    status_emoji, status_label = classify_status(age_min)

    runs = fetch_recent_workflow_runs()
    last_run = runs[0] if runs else None
    actual_24h = count_schedule_triggers_within(runs, 24)
    actual_7d = count_schedule_triggers_within(runs, 24 * 7)

    return {
        "generated_iso": generated_iso,
        "age_min": age_min,
        "status_emoji": status_emoji,
        "status_label": status_label,
        "last_gh_run": last_run,
        "gh_cli_available": runs is not None,
        "expected_triggers_24h": EXPECTED_TRIGGERS_PER_DAY,
        "expected_triggers_7d": EXPECTED_TRIGGERS_PER_DAY * 7,
        "actual_schedule_triggers_24h": actual_24h,
        "actual_schedule_triggers_7d": actual_7d,
        "watchdog": watchdog_status(),
        "persistent_files": persistent_file_git_freshness(),
    }


def print_report(report=None):
    report = report or build_report()
    print("=== NEWS_INFRA_MONITOR ===")
    print(f"{report['status_emoji']} {report['status_label']}")
    if report["age_min"] is not None:
        print(f"最終更新(本番): {report['generated_iso']} (現在から{report['age_min']:.0f}分)")
    else:
        print("最終更新(本番): 取得できませんでした")

    if report["gh_cli_available"]:
        run = report["last_gh_run"]
        if run:
            print(f"最終GitHub Actions実行: {run['createdAt']} event={run['event']} conclusion={run['conclusion']}")
        print(
            f"schedule実測回数: 直近24h={report['actual_schedule_triggers_24h']}"
            f"(想定{report['expected_triggers_24h']}) / "
            f"直近7d={report['actual_schedule_triggers_7d']}(想定{report['expected_triggers_7d']})"
        )
    else:
        print("GitHub Actions実行履歴: `gh` コマンドが使えないため取得できませんでした")

    wd = report["watchdog"]
    if wd["log_found"]:
        print(
            f"ローカルwatchdog: 最終確認={wd['last_check']} / "
            f"手動発火 直近24h={wd['dispatch_count_24h']}件・直近7d={wd['dispatch_count_7d']}件"
        )
    else:
        print("ローカルwatchdog: ログが見つかりません(com.takuya.news-watchdogが未起動の可能性)")

    print("\n--- 永続化ファイルの更新鮮度 ---")
    for name, info in report["persistent_files"].items():
        if info["last_commit"] is None:
            print(f"{name:35s} コミット履歴が見つかりません")
        else:
            # theme_trend_history.jsonlは「1日1テーマ1行」の設計上(重複
            # 排除)、新しい日付/テーマが出ない限り丸1日以上コミットが
            # 無くても正常。他の4ファイルとは更新頻度の前提が違うため、
            # 許容時間を分ける(誤検知の再発防止)。
            threshold_hours = STALE_HOURS_OVERRIDE.get(name, DEFAULT_STALE_HOURS)
            flag = " ⚠️" if info["hours_ago"] > threshold_hours else ""
            print(f"{name:35s} 最終コミット {info['last_commit']} ({info['hours_ago']:.1f}時間前){flag}")
