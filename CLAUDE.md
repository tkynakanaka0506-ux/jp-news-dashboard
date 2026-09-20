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
python3 dev.py test            # テスト（tests/ 配下9ファイル・88件）
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
                        独立性(同じ記事が複数観点を満たしても1件の裏付け
                        にしかしない。別々のitem_idが最低2件そろって
                        初めてemerging/watch判定の対象にする)・時間軸
                        (直近7/30/90日の件数を並べて出す。単一の伸び率
                        スコアには合成しない)・国際的な広がり(見出しの
                        国名から地域数を推定)・前段階の保存(first_seenは
                        一度設定したら絶対に上書きしない)を持つ
                        （「重要になる」と予言はしない、観測事実の開示のみ）
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
- **日をまたいで状態を持つレジストリはCIでも永続化する**: `newssite/data/
  policy_event_registry.json`・`theme_trend_registry.json`は、書き込むだけでは
  不十分で、`.github/workflows/update.yml`の`git reset --hard`より前に退避し、
  戻した上で`git add`に含める必要がある(実測バグ2026-09-20: policy_event_registry.json
  がこれまで一度もコミットされておらず、CIのたびに空の登録簿に戻っていたため
  「続報」判定が本番で一度も機能していなかった)。新しいレジストリを追加する
  ときは、必ずこのワークフローに追加すること。

## 旧ダッシュボード

`render_dashboard.py` / `news_analyzer.py` / `notify_line.py` / `src/main/java/Main.java` は
旧デイトレードダッシュボード（`dashboard.html`）用。市況ヘッダーの指数は `data.json` 経由で
ニュースサイトも使っているため消さずに残している。
