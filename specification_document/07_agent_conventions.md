# エージェント作業規約

ルートの `AGENTS.md`（常時読み込みの索引）の詳細版。

## セッション開始

Actions が毎日 `main` にコミットするため、**状態を読む前・作業を始める前に** `git pull --rebase` **を実行する。指示を待たない。**

## ユーザーへの報告

fukase は1人運用で、クオンツ／インフラの専門家ではない。

- かみくだいた日本語の要約とたとえ → 「直さないとどうなる」 → おすすめアクションを**1つだけ**。
- 専門用語（drift warning、IR、PSI、basis、トリプルバリア など）を1行の定義なしにおすすめアクションで使わない。
- **go/no-go を変える発見は報告の先頭に**、平易な言葉で推奨とセットで置く。「補足」に埋めない。
- メモ・要約ファイルの作成を提案しない。頼まれたものを届ける。
- 専門用語・ハッシュ・パス・生の数値は末尾へ。

## 作業の進め方

- PR マージ後の後片付けは4つまとめて: worktree 削除、ローカル・リモートブランチ削除、`main` を pull。
- 実装計画は `plans/` へ（`docs/` 配下は日次 publish の `rsync --delete` で消える）。調査・作業用の中間物は依頼がない限りリポジトリに残さない。

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

`main.py` は `.env` なしでも動く（LINE と DB は未設定ならスキップ）。環境変数の正典は `.env.example`、既定値は `src/config.py`。

## 編集前に知っておくこと

概略のみ。as-built の詳細は各番号ファイルへ。

- `main.py` **の処理順は仕様そのもの**（順序が「通知1回・snapshot 後・目標ウェイト永続化」を保証）。詳細は [01_backend_python.md](01_backend_python.md)。
- **フロントエンド契約**: 必須 JSON は `dashboard_index.json` と `tickers/{code}.json` のみ、他は任意カード（欠損なら非表示）。取得は全て `web/src/lib/fetchJson.ts` 経由（[02_frontend_web.md](02_frontend_web.md)）。
- **CI/CD**: 時刻は全て JST。営業日・冪等性はガードスクリプト、コミットは `.github/scripts/commit-and-push.sh` 経由（[03_cicd_workflows.md](03_cicd_workflows.md)）。

## Skills（Claude Code / Codex 共有）

`SKILL.md` 形式の手順書。使う前にその `SKILL.md` を読み、`references/` は必要な分だけ読む。

### 配置（唯一の編集元は `.agents/skills/`）

- **正本は** `.agents/skills/<name>/SKILL.md`**。** 補助ファイル（`references/`、`scripts/` 等）も正本ディレクトリに置く。Codex はここを直接探索し `$<name>` で呼べる。
- **Claude Code へは** `.claude/skills/<name>/SKILL.md` **の参照スタブで公開。** スタブは通常ファイルで、frontmatter（`name` / `description` は正本と完全一致）＋「実体を読め」のみ:

  ```markdown
  ---
  name: <name>
  description: <正本と同じ description>
  ---

  このファイルは Claude Code 用の参照スタブ。スキルの実体は `.agents/skills/<name>/SKILL.md`。
  実体を読み、その手順に従って実行せよ。編集は実体側だけに行う。
  ```

- **正本から補助ファイルを指すパスはリポジトリルート相対で書く**（例: `.agents/skills/<name>/references/foo.md`）。スタブ経由起動時のベースディレクトリは `.claude/skills/<name>` になり、スキルディレクトリ相対パスは解決できないため。
- CI（`claude-code-action` の `prompt: "/<name> ..."`）もスタブ経由で正本を実行する。

### 禁止事項（過去に壊れた方式。再提案しない）

- **symlink**: この環境は `core.symlinks=false` のため、チェックアウトで「リンク先パスだけの通常ファイル」に展開され `SKILL.md` が消える。
- `.claude/skills/` **への一式コピー・同期**: 二重管理とドリフトだけが増える。
- **正本以外の直接編集**: 変更は必ず `.agents/skills/<name>/` へ。スタブ側の更新は `description` 変更時の frontmatter 同期のみ。

### 作成・更新・削除の手順

1. **作成/インストール**: 実体一式を `.agents/skills/<name>/` に置き、上のテンプレートでスタブを作る。
2. **更新**: 正本のみ編集。`description` を変えたときだけスタブの frontmatter も同期。
3. **削除**: 正本とスタブの両ディレクトリを消す。
4. **検証**（作成・更新・削除のたび）:

- `test -f .claude/skills/<name>/SKILL.md` が真で中身が読めること（「呼び出せた」は証拠にならない）。
- `git ls-files -s -- .claude/skills/ .claude/commands/` に `120000`（symlink モード）が残っていないこと。残っていたら `git rm --cached <path>` → `git add <path>`。
- スタブと正本の `name` / `description` が一致していること。
