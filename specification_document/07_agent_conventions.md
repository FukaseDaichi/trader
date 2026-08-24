# エージェント作業規約

更新日: 2026-08-24 JST

リポジトリルートの `AGENTS.md` が常に読み込まれる索引で、このファイルはその詳細版です。

## セッション開始

GitHub Actions が毎日 `main` にコミットします（キュレーション、publish、夜間リフレッシュ、
週次再学習）。そのためローカルのチェックアウトはほぼ常に古い状態です。**状態を読む前・作業を
始める前に `git pull --rebase` を実行してください。指示を待たないこと。**

## ユーザーへの報告

fukase はこのシステムを1人で運用しており、クオンツ／インフラの専門家ではありません。

- かみくだいた日本語の要約とたとえから始め、「直さないとどうなる」を述べ、おすすめアクションを
  **1つだけ**示す。専門用語・ハッシュ・パス・生の数値は末尾の `<details>技術メモ</details>`
  に押し込む。
- **専門用語（drift warning、IR、PSI、basis、トリプルバリア など）を、1行の定義なしに
  おすすめアクションの中で使わない。**
- **go/no-go の判断を変える発見は、報告の先頭に、平易な言葉で、推奨とセットで置く。**
  「補足」や「注意しておきたい発見」の下に埋めない。
- 長い選択肢の羅列ではなく、項目ごとの決定を提示する。
- メモ・ノート・要約ファイルの作成を提案しない。頼まれたものを届ける。

## 作業の進め方

- 依頼に含まれる範囲は一度の作業で完了させる。1つの依頼の途中で確認を挟まない。確認は最初に
  一度だけ、かつ本当に危険なとき・未マージのときに限る。
- PR をマージした後の「後片付け」は4つまとめて: worktree 削除、ローカルブランチ削除、
  リモートブランチ削除、`main` を pull。
- 2分以上かかりそうな処理の前に、何を実行していてどれくらいかかるかを伝える。黙り込まない。
- 自分が挙げたレビュー指摘は自分の担当: 直すか、「保留にした・どこに記録した」を明言する。
- 実装計画はリポジトリルートの `plans/` に置く。**`docs/` の下には絶対に置かない**
  （日次 publish が `docs/` を `rsync --delete` するため消える）。調査・作業用の中間物は、
  依頼がない限りリポジトリに残さない。

## コマンド

```bash
uv sync                                   # Python 依存を導入（Python 3.13）
uv run python main.py                     # 日次パイプラインを通しで実行
uv run python scripts/db_migrate.py       # DB スキーマ適用（DATABASE_URL 必須）
uv run python tests/test_<name>.py        # テストは素の Python スクリプト（pytest 不要）

cd web && npm install
cd web && npm run dev                     # 開発サーバ http://localhost:3000
cd web && npm run build:prod              # /trader ベースパスで静的エクスポート
cd web && npm run lint
```

`main.py` は `.env` なしでも動きます（LINE と DB は未設定ならスキップ）。全環境変数の正典は
コメント付きの `.env.example`、既定値は `src/config.py` です。

## 編集前に知っておくこと

概略のみです。as-built の詳細は各番号ファイルにあります。

- **`main.py` の処理順は load-bearing（順番そのものが仕様）。** 銘柄ごと: データ同期 →
  特徴量 → 推論＋exact KPI ゲート（証跡不一致・ゲート未達は `HOLD` へ強制）→ 5段階シグナル。
  そのあと run 単位で: Phase 2 推論 → active モードのマージ → 通知 → Phase 0 の DB
  write-through（失敗は `data/outbox/` へ退避して再送）→ ダッシュボード出力。この順序なので、
  通知は1回だけ・ポートフォリオ snapshot の後・目標ウェイトが永続化されます
  （[01_backend_python.md](01_backend_python.md)）。
- **フロントエンドの契約**（Next.js 静的エクスポート、日本語 UI、ダークテーマ）: 必須は
  `dashboard_index.json` と `tickers/{code}.json` だけ。その他の `docs/` JSON は任意カードで、
  欠損または `available: false` なら非表示になります。取得はすべて `web/src/lib/fetchJson.ts`
  経由で、HTTP・パース・ランタイムガードのいずれかが失敗すると `null` になります
  （[02_frontend_web.md](02_frontend_web.md)）。
- **CI/CD**: 時刻はすべて JST。`jpx_calendar.py` が営業日、`run_guard.py` /
  `curation_guard.py` が冪等性をガードし、全 workflow は
  `.github/scripts/commit-and-push.sh`（rebase＋最大3回リトライ）経由でコミットします。
  スケジュールと各 workflow の手順は [03_cicd_workflows.md](03_cicd_workflows.md)。

## Skills

`SKILL.md` 形式の手順書です。対話作業は `skills/`、CI の自動キュレーションは
`.claude/skills/` を使います。使う前にその skill の `SKILL.md` を読み、`references/` は
必要な分だけ読み込みます（例: ユーザーが名前を挙げたとき、または JP 株を調査して
`tickers.yml` を更新したいときの `jp-stock-ticker-curation`）。
