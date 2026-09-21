#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ローカル開発用のコマンドまとめ(Cursor/ターミナル両用)。

  python3 dev.py setup     環境チェック → テスト → サンプル生成(最初に1回)
  python3 dev.py sample    サンプルデータでページ生成(ネット接続不要)
  python3 dev.py build     実際にニュースを取得して生成(--llm でAI補強あり)
  python3 dev.py render    news.json からHTMLだけ作り直す
  python3 dev.py serve     ローカルサーバを立ててブラウザで開く(--watch で自動再生成)
  python3 dev.py test      テストを実行
  python3 dev.py check     stocks.json / rules.json の整合性チェック
  python3 dev.py open      生成済み index.html をブラウザで開く
  python3 dev.py score     ルール判定の品質スコアカード(tests/golden_headlines.json 集計)
  python3 dev.py backtest  Policy Impact Score バックテストの蓄積状況(BACKTEST_STATUS)を表示

Python 3.9 以上があれば動きます(pip install 不要)。
"""
import argparse
import json
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
# dev.py はプレビュー専用ファイルに書き出す。
# 公開される index.html / news.json は GitHub Actions だけが生成するので、
# ローカル作業でうっかり上書き・コミットしてしまうことがない。
PREVIEW_HTML = BASE_DIR / "preview.html"
PREVIEW_JSON = BASE_DIR / "preview.news.json"
PREVIEW_ARGS = ["--out", str(PREVIEW_HTML), "--news-json", str(PREVIEW_JSON)]
PKG_DIR = BASE_DIR / "newssite"
MIN_PYTHON = (3, 9)

OK = "✅"
NG = "❌"
INFO = "→"


def say(msg=""):
    print(msg, flush=True)


def run_python(args, **kwargs):
    """このスクリプトと同じPythonで別スクリプトを実行する。"""
    cmd = [sys.executable] + args
    say(f"{INFO} {' '.join(cmd[1:])}")
    return subprocess.run(cmd, cwd=BASE_DIR, **kwargs)


# ---------------- 各コマンド ----------------

def cmd_test(args):
    result = run_python(["-m", "unittest", "discover", "-s", "tests"] + (["-v"] if args.verbose else []))
    return result.returncode


def cmd_sample(args):
    return run_python(["build_news_site.py", "--sample"] + PREVIEW_ARGS).returncode


def cmd_build(args):
    cmd = ["build_news_site.py"] + PREVIEW_ARGS
    if not args.llm:
        cmd.append("--no-llm")
    return run_python(cmd).returncode


def cmd_render(args):
    if not PREVIEW_JSON.exists():
        say(f"{NG} {PREVIEW_JSON.name} がありません。先に `python3 dev.py sample` か `build` を実行してください。")
        return 1
    return run_python(["build_news_site.py", "--render-only"] + PREVIEW_ARGS).returncode


def cmd_open(args):
    if not PREVIEW_HTML.exists():
        say(f"{NG} {PREVIEW_HTML.name} がありません。先に `python3 dev.py sample` を実行してください。")
        return 1
    webbrowser.open(PREVIEW_HTML.as_uri())
    say(f"{OK} ブラウザで {PREVIEW_HTML.name} を開きました。")
    return 0


def _watch_sources(interval=1.0):
    """newssite/ 配下の更新を検知したら render-only で作り直し続ける。"""
    def snapshot():
        return {p: p.stat().st_mtime for p in PKG_DIR.rglob("*") if p.is_file() and p.suffix in (".py", ".json")}

    last = snapshot()
    say(f"{INFO} newssite/ の変更を監視中(保存するたびに作り直します。Ctrl+C で終了)")
    while True:
        time.sleep(interval)
        try:
            now = snapshot()
        except FileNotFoundError:
            continue
        if now != last:
            last = now
            changed = "変更を検知"
            say(f"\n{INFO} {changed} → 再生成します")
            run_python(["build_news_site.py", "--render-only"] + PREVIEW_ARGS)
            say(f"{OK} 再生成しました。")


# --watch のときだけプレビューHTMLに差し込む、自動リロード用の小さなスクリプト。
# 生成物そのものは汚さず、ローカル配信時にだけ付け足す。
RELOAD_SNIPPET = """
<script>
(function(){
  var current = null;
  setInterval(function(){
    fetch('/__mtime', { cache: 'no-store' }).then(function(r){ return r.text(); }).then(function(t){
      if(current === null){ current = t; return; }
      if(t !== current){ location.reload(); }
    }).catch(function(){});
  }, 1000);
})();
</script>
"""


def _make_handler(inject_reload):
    class PreviewHandler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(BASE_DIR), **kw)

        def log_message(self, fmt, *a):  # アクセスログは出さない
            pass

        def do_GET(self):
            if self.path.startswith("/__mtime"):
                body = str(PREVIEW_HTML.stat().st_mtime if PREVIEW_HTML.exists() else 0).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            path = self.path.split("?")[0]
            if inject_reload and path in ("/", "/" + PREVIEW_HTML.name) and PREVIEW_HTML.exists():
                html_bytes = PREVIEW_HTML.read_bytes().replace(
                    b"</body>", RELOAD_SNIPPET.encode("utf-8") + b"</body>"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(html_bytes)))
                self.end_headers()
                self.wfile.write(html_bytes)
                return

            super().do_GET()

    return PreviewHandler


def cmd_serve(args):
    if not PREVIEW_HTML.exists():
        say(f"{INFO} {PREVIEW_HTML.name} が無いので、まずサンプルデータで生成します。")
        if cmd_sample(args) != 0:
            return 1

    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), _make_handler(args.watch))
    except OSError as e:
        say(f"{NG} ポート {args.port} を使えません({e})。--port で別の番号を指定してください。")
        return 1

    url = f"http://127.0.0.1:{args.port}/{PREVIEW_HTML.name}"
    say(f"{OK} {url} で配信中(Ctrl+C で終了)")
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    if args.watch:
        say(f"{INFO} --watch: 保存すると自動で作り直し、ブラウザも自動で再読み込みします。")
        threading.Thread(target=_watch_sources, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        say(f"\n{OK} サーバを終了しました。")
    finally:
        server.server_close()
    return 0


def cmd_check(args):
    """JSONデータの整合性チェック。Cursorで編集したあとに実行する。"""
    from newssite import impact as impact_mod, stocks as stocks_mod

    errors, warnings = [], []

    try:
        master = stocks_mod.load()
    except Exception as e:
        say(f"{NG} stocks.json を読めません: {e}")
        return 1
    try:
        rules = impact_mod.load()
    except Exception as e:
        say(f"{NG} rules.json を読めません: {e}")
        return 1

    # 銘柄マスタ
    seen = {}
    for stock in master.stocks:
        code = stock.get("code", "")
        if not code.isdigit() or len(code) != 4:
            errors.append(f"証券コードが4桁の数字ではありません: {code!r}({stock.get('name')})")
        if code in seen:
            errors.append(f"証券コードが重複しています: {code}({seen[code]} と {stock.get('name')})")
        seen[code] = stock.get("name")
        if not stock.get("name"):
            errors.append(f"銘柄名が空です: {code}")
        if not stock.get("themes"):
            warnings.append(f"themes が空の銘柄(ニュースに紐づきません): {code} {stock.get('name')}")

    stock_themes = set()
    for stock in master.stocks:
        stock_themes.update(stock.get("themes", []))

    # ルール
    used_themes = set()
    for theme in rules.themes:
        if theme.get("category") not in rules.category_label:
            errors.append(f"テーマ {theme.get('id')} の category が categories にありません: {theme.get('category')}")
        if not theme.get("keywords"):
            errors.append(f"テーマ {theme.get('id')} に keywords がありません")
        for rule in theme.get("impacts", []):
            if rule.get("direction") not in impact_mod.DIRECTION_LABEL:
                errors.append(f"テーマ {theme.get('id')} の direction が不正です: {rule.get('direction')}")
            if not rule.get("reason"):
                warnings.append(f"テーマ {theme.get('id')} の impacts に reason がありません(表示が寂しくなります)")
            for tag in rule.get("themes", []):
                used_themes.add(tag)
                if tag not in stock_themes:
                    errors.append(
                        f"テーマ {theme.get('id')} が使っているタグ「{tag}」は stocks.json のどの銘柄にもありません"
                    )
            for code in rule.get("codes", []):
                if code not in master.by_code:
                    errors.append(f"テーマ {theme.get('id')} が指定した証券コード {code} は stocks.json にありません")

    unused = sorted(stock_themes - used_themes)
    if unused:
        shown = "、".join(unused[:12]) + ("…" if len(unused) > 12 else "")
        warnings.append(
            f"どのルールからも使われていないタグが{len(unused)}件あります(誤字か、まだルール未作成): {shown}"
        )

    say(f"銘柄: {len(master.stocks)}件 / テーマ: {len(rules.themes)}件 / カテゴリ: {len(rules.categories)}件")
    for w in warnings:
        say(f"⚠️  {w}")
    for e in errors:
        say(f"{NG} {e}")
    if errors:
        say(f"\n{NG} {len(errors)}件のエラーがあります。修正してください。")
        return 1
    say(f"\n{OK} 整合性チェックOK({len(warnings)}件の注意)")
    return 0


def cmd_setup(args):
    say("=" * 56)
    say(" 重要ニュース×影響銘柄サイト ローカル開発セットアップ")
    say("=" * 56)

    version = sys.version_info
    say(f"{INFO} Python {version.major}.{version.minor}.{version.micro} ({sys.executable})")
    if (version.major, version.minor) < MIN_PYTHON:
        say(f"{NG} Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 以上が必要です。python.org から入れ直してください。")
        return 1
    say(f"{OK} Pythonのバージョンは問題ありません(追加インストールは不要です)")

    missing = [p for p in ("build_news_site.py", "newssite/data/stocks.json", "newssite/data/rules.json")
               if not (BASE_DIR / p).exists()]
    if missing:
        say(f"{NG} ファイルが足りません: {', '.join(missing)}")
        say("   リポジトリのルートで実行しているか確認してください。")
        return 1
    say(f"{OK} 必要なファイルが揃っています")

    say("\n--- データの整合性チェック ---")
    if cmd_check(args) != 0:
        return 1

    say("\n--- テスト ---")
    if cmd_test(args) != 0:
        say(f"{NG} テストが失敗しました。上のログを確認してください。")
        return 1
    say(f"{OK} テストは全部通りました")

    say("\n--- サンプルデータでページを生成 ---")
    if cmd_sample(args) != 0:
        return 1

    say("\n" + "=" * 56)
    say(f"{OK} 準備完了です。次にやること:")
    say("")
    say("  python3 dev.py serve --watch   # ブラウザで見ながら編集(おすすめ)")
    say("  python3 dev.py build           # 実際のニュースを取得して生成")
    say("  python3 dev.py check           # JSONを編集したあとの確認")
    say("")
    say("  編集する場所:")
    say("    銘柄を増やす            newssite/data/stocks.json")
    say("    ニュース→銘柄のルール   newssite/data/rules.json")
    say("    集めるニュースの種類    newssite/config.py の FEEDS")
    say("    見た目                  newssite/render.py の CSS")
    say("=" * 56)
    return 0


def cmd_score(args):
    """ルール判定の品質スコアカード(tests/golden_headlines.json 集計)。"""
    from newssite import rule_scorecard
    rule_scorecard.run()
    return 0


def cmd_backtest(args):
    """Policy Impact Score バックテストの蓄積状況(BACKTEST_STATUS)を表示する。"""
    from newssite import backtest
    backtest.print_status()
    return 0


MONITOR_HEARTBEAT_PATH = Path.home() / "Library" / "Logs" / "jp-news-dev-monitor-heartbeat.txt"


def _read_heartbeat(path):
    """③'監視そのものが止まっていないか」用のハートビート読み取り。
    ネットワーク等に依存しない純粋な関数として切り出し、単体テストしやすく
    しておく(cmd_monitor全体はgh/curlを呼ぶため単体テストに向かない)。
    """
    return path.read_text(encoding="utf-8").strip() if path.exists() else None


def _write_heartbeat(path, now_str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(now_str, encoding="utf-8")


def cmd_monitor(args):
    """パイプラインの稼働監視レポート(本番の実データに対する読み取り専用
    チェック)。

    2026-09-20ユーザー要望「次にやるなら機能追加より監視」、2026-09-21
    ユーザー要望「長期的な安定稼働の監視・再発防止を優先」への対応。
    判定条件は一切変更しない。

    まず①インフラ稼働状況(GitHub Actionsのschedule実測・ローカル
    watchdogの発火状況・永続化ファイルの更新鮮度)を出し、続けて
    ②③④⑤⑥(新興テーマの検出状況・情報源/シグナルの独立性・
    milestonesの蓄積・海外ソースの有無・同一ニュースの転載の重複
    カウント有無)を出す。

    ③'監視そのものが止まっていないか(2026-09-21ユーザー要望「システム→
    監視→監視の監視、と無限に作る必要はないが、少なくともdev.py monitor
    について最後に正常に動いたのはいつかくらいは分かるようにしておく」)。
    「監視の監視」を新設せず、このコマンド自身が完走するたびに簡単な
    ハートビートファイルを書き換えるだけに留める。例外で処理が止まった
    場合は書き換わらないので、「最後に正常完走したのはいつか」がそのまま
    分かる。
    """
    import copy
    import json
    from datetime import datetime
    from newssite import impact as impact_mod
    from newssite import infra_monitor, theme_trend_monitor, theme_trends
    from newssite.config import JST

    previous_heartbeat = _read_heartbeat(MONITOR_HEARTBEAT_PATH)
    print(f"(前回 dev.py monitor が正常に完走した時刻: {previous_heartbeat or '記録なし(初回実行)'})")
    print()

    infra_monitor.print_report()
    print()

    registry = theme_trends._load_registry()
    news_path = BASE_DIR / "news.json"
    theme_stage_info = None
    if news_path.exists():
        with open(news_path, encoding="utf-8") as f:
            news_data = json.load(f)
        news = news_data if isinstance(news_data, list) else news_data.get("news", [])
        rules = impact_mod.load()
        today = datetime.now(JST).strftime("%Y-%m-%d")
        # レジストリは読み取り専用で見たいだけなので、複製に対して判定を
        # 走らせる(_save_registryは呼ばない=本番の登録簿には触れない)。
        theme_stage_info = theme_trends.record_and_classify(copy.deepcopy(registry), news, rules, today)
    theme_trend_monitor.print_report(registry, theme_stage_info)

    # ここまで例外無く到達できた=このmonitor自身が正常に完走した、という
    # 意味でハートビートを書き換える(このタイミングでのみ更新することで、
    # 例外で途中終了した回はheartbeatが更新されず、「最後に正常完走したのは
    # いつか」が正しく反映される)。
    _write_heartbeat(MONITOR_HEARTBEAT_PATH, datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"))
    return 0


COMMANDS = {
    "setup": cmd_setup,
    "sample": cmd_sample,
    "build": cmd_build,
    "render": cmd_render,
    "serve": cmd_serve,
    "test": cmd_test,
    "check": cmd_check,
    "open": cmd_open,
    "score": cmd_score,
    "backtest": cmd_backtest,
    "monitor": cmd_monitor,
}


def main(argv):
    parser = argparse.ArgumentParser(
        description="重要ニュース×影響銘柄サイトのローカル開発コマンド",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("command", nargs="?", default="setup", choices=sorted(COMMANDS), help="実行するコマンド")
    parser.add_argument("--port", type=int, default=8000, help="serve のポート番号(既定: 8000)")
    parser.add_argument("--watch", action="store_true", help="serve 中に newssite/ の変更を監視して再生成する")
    parser.add_argument("--no-open", action="store_true", help="serve でブラウザを自動で開かない")
    parser.add_argument("--llm", action="store_true", help="build で生成AIの補強を使う(APIキーが必要)")
    parser.add_argument("-v", "--verbose", action="store_true", help="test の詳細出力")
    args = parser.parse_args(argv)
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
