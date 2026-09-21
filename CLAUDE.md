# 重要ニュース × 影響銘柄サイト

日本株に影響しうる重要ニュースを自動収集し、各ニュースで「どの銘柄に影響が出うるか」を
表示する静的サイト。GitHub Actions が定期生成し、GitHub Pages で公開している。

公開URL: https://tkynakanaka0506-ux.github.io/jp-news-dashboard/

## コマンド

ローカル作業は `dev.py` を使う。出力は `preview.html` / `preview.news.json` のみで、
公開される `index.html` / `news.json` は GitHub Actions だけが生成する（ローカルから上書きしない）。

```bash
python3 dev.py serve --watch   # プレビュー＋保存のたび自動再生成・自動リロード
python3 dev.py sample          # サンプルデータで生成（ネット接続不要）
python3 dev.py build           # 実ニュースを取得して生成（--llm でAI補強）
python3 dev.py check           # stocks.json / rules.json の整合性チェック
python3 dev.py test            # テスト（tests/ 配下9ファイル・141件）
python3 dev.py monitor         # 萌芽シグナルの監視レポート（本番実データ、読み取り専用）
```

**JSONデータを変えたら `dev.py check`、ロジックを変えたら `dev.py test` を必ず通してから終わる。**

## 構成

```
build_news_site.py   生成本体（Actionsが実行）
dev.py               ローカル開発コマンド
newssite/
  config.py            収集フィード(FEEDS)・定数
  rss.py               Google ニュースRSS取得・同じ話題の重複統合
  stocks.py            銘柄マスタの読み込み・検索
  impact.py            重要度/カテゴリ/影響銘柄のルール判定
  llm.py               Gemini/Groq 補強（任意）
  analyze.py           news.json の組み立て
  policy_lifecycle.py  政策の続報系列にpolicy_event_idを割り当て、ライフサイクル
                        状態(NEW/UPDATE/MATURED/CLOSED)を管理
  theme_trends.py      萌芽シグナル検知。テーマごとに8種類のシグナル
                        (政府一次情報/政策検討段階/研究開発/特許/設備投資/
                        海外の動き/関連企業発表/複数媒体報道)を記録する。
                        独立性の判定単位は「情報源(item['source'])の異なり
                        数」(記事数や記事内の観点数ではない。同じ省庁が
                        2回発表しても情報源は1つ)。情報源の種類も
                        政府(日本/海外)・企業・研究機関・国際機関・複数
                        メディアの6区分で可視化する。地域は「発信元」
                        (機関名から確度高く判定できる場合のみ)と「関連
                        する地域」(単なる国名の言及)を区別し、確信が
                        持てない場合は不明のままにする(推測で埋めない)。
                        milestones(各情報源種別を最初に観測した日)は
                        eventsの90日減衰とは別に永久保存し、テーマの
                        成長履歴を後から辿れるようにする
                        （「重要になる」と予言はしない、観測事実の開示のみ）
  theme_trend_history.py 萌芽シグナルの日次スナップショット記録（判定は追加せず、
                        analyze.build()から1日1テーマ1行だけ追記。dev.py monitorの
                        監視材料であり、将来の「Emerging Theme Backtest」
                        （初検知後7/14/30/90日の実際の結果を追跡する基盤）の
                        材料でもある）
  theme_trend_monitor.py 萌芽シグナルの監視レポート（dev.py monitorが呼ぶ。読み取り
                        専用でCIの自動テストゲートには含めない）。新興/要監視の
                        件数・情報源種別・地域に加え、同一ニュースの転載が別
                        情報源として重複カウントされていないかの監査(rss.pyの
                        既存の同一トピック判定を流用)、first_seen/milestonesが
                        意図せず巻き戻っていないかの監査(theme_trend_history.jsonl
                        の時系列比較)を行う
  catalyst_export.py   他プロジェクト(jp-stock-dashboard)へのシグナル書き出し
  backtest.py          政策材料シグナルのバックテスト基盤（記録のみ）
  rule_scorecard.py    ルール(rules.json)ごとの的中率スコアカード
  render.py            HTML生成（CSS・JSもこの中）
  sample.py            ネット接続なしの表示確認用データ（build_news()に委譲、判定ロジックを複製しない）
  data/stocks.json     銘柄マスタ
  data/rules.json      ニュース→影響銘柄のルール
tests/                 test_pipeline.py 他、機能ごとに分割（9ファイル・88件）
```

## 編集する場所

| やりたいこと | ファイル |
| --- | --- |
| 銘柄を増やす・テーマタグを変える | `newssite/data/stocks.json` |
| ニュース→銘柄の関係、重要度キーワード | `newssite/data/rules.json` |
| 集めるニュースの種類 | `newssite/config.py` の `FEEDS` |
| 見た目（配色・レイアウト） | `newssite/render.py` の `CSS` |
| ページ構成・文言 | `newssite/render.py` の `build_html` |
| AIへの指示文 | `newssite/llm.py` の `PROMPT_HEADER` |

`stocks.json` の `themes` と `rules.json` の `impacts[].themes` は同じタグ語彙。
新しいタグを使うときは両方に入れる（`dev.py check` とテストが不整合を検出する）。

## 体制: 完全無人の自動push（ユーザー承認済み・2026-09-20）

GitHub Actions (`update.yml`) が定期実行され、以下の順でチェックしてから
**人の確認無しで直接 `index.html`/`news.json` をpush**する。

1. `python3 -m py_compile` で対象.pyファイル全部の構文チェック
2. `python3 -m unittest discover -s tests`（回帰テスト、失敗したらジョブごと失敗しpush省略）
3. `build_news_site.py` 本体実行
4. 差分があれば強制Push

### 取得スケジュール(実際の挙動・2026-09-21実測)

`.github/workflows/update.yml`の`schedule:`が意図している頻度:

| 時間帯(JST) | 頻度 | cron(UTC) |
| --- | --- | --- |
| 8:07〜15:37(日本市場時間帯、morning) | 30分おき | `7,37 23,0-6 * * *` |
| 16:07〜翌6:07(それ以外、evening) | 2時間おき | `7 7,9,11,13,15,17,19,21 * * *` |

**ただし、GitHub Actionsの`schedule`トリガーは公式にも「高負荷時に遅延・ドロップ
されうる」と明記されている既知の制約があり、このリポジトリでは実際にかなり
深刻な影響が出ている。** 2026-09-21、ユーザー報告「更新が止まっている気がする」
の調査で、直近3.5日分(2026-09-17〜20)の実行間隔を計測したところ:

- 想定の間隔(30分/2時間)に対し、実測は**26分〜462分(7.7時間)**とばらつく
- 想定トリガー数に対し、実際に発火したのは**約35%のみ**(残り約65%は
  GitHub側で未発火。実行された分は全て成功しており、コード側の問題ではない)

**再発防止(2026-09-21導入): ユーザー自身のMac上のlaunchdで補う。**
`watchdog_check.sh`を`com.takuya.news-watchdog`(launchd、30分おき)から実行し、
本番サイト(`news.json`の`generated_iso`)が180分以上更新されていなければ
`gh workflow run update.yml`で手動発火して補う。ワークフローYAML側の設定を
いくら変えてもGitHub側の未発火自体は解決しないため、外部からの補完トリガーが
唯一の実効的な対策(`jp-stock-dashboard`が既に同じ考え方でlaunchdに依存している
のと同じパターン)。ログは`~/Library/Logs/jp-news-watchdog.log`。

```bash
launchctl list | grep news-watchdog          # 稼働確認(PID "-" は正常、次回起動待ち)
tail -f ~/Library/Logs/jp-news-watchdog.log  # 動作ログ
launchctl unload ~/Library/LaunchAgents/com.takuya.news-watchdog.plist  # 停止したい場合
```

**このリポジトリでは、上記の自動テストゲートだけが「壊れたコードを本番に出さない」
唯一の防波堤。** そのため：

- **バグを直したら、そのたびに必ず `tests/` へ回帰テストを追加すること。**
  ユーザーから「再発防止して」と毎回言われなくても、これはデフォルトの作業手順にする。
  テストを足さない修正は、次に同じバグが再発しても誰も気づけない（pushが自動で
  進んでしまうため）。jp-stock-dashboard（`sync_and_push.sh`）でも同じ運用にしている。
- モバイルUI（`render.py`内の`mobile_app_html`や埋め込みJS）のように、生成HTML
  文字列の中に埋め込まれたクライアントJSは、DOM環境の無いPythonのunittestでは
  実際には実行されない。この手のコードの回帰テストは、生成したHTML文字列や
  `render.py`自身のソーステキストに対して、期待するパターンが含まれる/
  含まれないことを文字列一致・正規表現で確認する形になる。

## 守ること

- **依存を増やさない**: Python標準ライブラリのみ（`pip install` 不要を維持）。
  フレームワーク・npmパッケージ・ビルドツールは入れない。
- **APIキー無しでも動く**: 生成AI（`newssite/llm.py`）は補強にすぎない。キーが無い/失敗しても
  ルール判定だけでページが完成する状態を壊さない。
- **ネットワーク失敗で落とさない**: RSS取得やAPI呼び出しの失敗はログに残して処理を続ける。
  取得できなかったときは前回値を使わず空表示にする（古い情報で判断させない）。
- **断定表現を書かない**: 表示文・プロンプトとも「必ず上がる」等の確約表現は使わず、
  「〜の可能性がある」「〜が意識されやすい」に留める。投資助言にしない。
- **数字を創作しない**: 見出しに無い株価・業績数値をコードやプロンプトで作らない。
- **「重要になる」と予言しない**: 萌芽シグナル(theme_trends.py)は、観測できた
  独立したシグナルの種類・件数を事実として提示するだけにする。統計的な
  伸び率などから「今後大きくなる」と予測する設計にはしない(ユーザー方針
  2026-09-20)。
- **生成物を手で編集しない**: `index.html` / `news.json` / `preview.html` は自動生成物。
  直すのは生成元のコード。
- **日をまたいで状態を持つファイルはCIでも永続化する**: `.github/workflows/
  update.yml`の「差分があれば強制Push」ステップは、`PERSISTENT_STATE_FILES`
  (状態)と`GENERATED_FILES`(生成物)の配列2つを1箇所で定義し、
  `git reset --hard`の前後で退避・復元・`git add`をループで一括処理する形に
  一元化してある。**新しいレジストリ/履歴ファイルを追加するときは、
  `PERSISTENT_STATE_FILES`配列に1行足すだけでよい**(cp/mv/git addを個別に
  書き足す必要はない・書いてはいけない)。
  実測バグ2026-09-20: 最初はpolicy_event_registry.jsonが一度もコミットされて
  おらず「続報」判定が本番で機能していなかった。個別ファイル名を並べる方式の
  まま気づかずbacktest_events.jsonl・policy_catalyst_signals.jsonも同じ穴に
  落ちていたため、二度目の再発を機に一元化した。
  `tests/test_pipeline.py`の`CIConfigTest.test_every_runtime_data_file_is_registered_for_persistence`
  が、`newssite/data/`配下に新しい`.json`/`.jsonl`を置いたのに配列へ追加し
  忘れた場合に機械的に検出する(人間が編集するマスタデータは
  `HUMAN_EDITED_DATA_FILES`へ追加)。

## 旧ダッシュボード

`render_dashboard.py` / `news_analyzer.py` / `notify_line.py` / `src/main/java/Main.java` は
旧デイトレードダッシュボード（`dashboard.html`）用。市況ヘッダーの指数は `data.json` 経由で
ニュースサイトも使っているため消さずに残している。
