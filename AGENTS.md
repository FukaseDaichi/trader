# AGENTS.md — このリポジトリのエージェント向け指示

`CLAUDE.md` は `@AGENTS.md` に委譲。ここは**索引**と**絶対ルール**のみ。担当領域のリンク先を作業前に読むこと。

指示が食い違ったときの優先順位は **ユーザーの直接の指示 > 本ファイルと `specification_document/` > Skill / サブエージェント定義**。ただし「絶対に外せない9項目」を破る変更はユーザーの明示的な承認なしには行わない。

## 作業前に読むもの

| 場面                                     | 読むファイル                                                                                                         |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| 毎セッション、何よりも先に               | [07_agent_conventions.md](specification_document/07_agent_conventions.md) — 作業規約（報告の作法、コマンド、Skills） |
| `main.py` / `src/` / `scripts/` の編集前 | [08_invariants.md](specification_document/08_invariants.md) — 不変条件（`pipeline-safety-reviewer` の審査基準）      |
| 実装済みの挙動を変える前                 | [specification_document/README.md](specification_document/README.md) — 領域別の as-built 正典                        |
| 状況や範囲を判断する前                   | [06_issues_and_backlog.md](specification_document/06_issues_and_backlog.md) — 未解決課題、決定記録、ゲート状況       |
| キュレーションに触れる前                 | [ai_ticker_curation/00_overview.md](specification_document/ai_ticker_curation/00_overview.md) — キュレーション設計   |
| システムの全体像                         | [README.md](README.md) — 日本株の自動予測・売買シグナルシステム（Phase 0〜3、GitHub Pages 公開）                     |

## 絶対に外せない9項目

1. **まず `git pull --rebase`。** Actions が毎日 `main` にコミットするためローカルは常に古い。
2. **日次シグナル生成は絶対に止めない。** どこが落ちてもフォールバックまたはスキップ＋ログで縮退。
3. **実行可能シグナルの前に必ず KPI ゲート。** 未達なら `HOLD`。
4. **Phase 2 は shadow のまま、出力はバイト単位で不変。** active への切替は人間によるゲート付きの手動 env 変更のみ。
5. **`docs/` 配下にファイル・ディレクトリを追加したら `daily-publish-dashboard.yml` の `--exclude` にも必ず追加。** 漏れると次回 publish で削除される。
6. **`tickers.yml` / `curation_pool.yml` を直接編集しない。** 書き手は `scripts/curation_merge.py` / `scripts/curation_pool_merge.py` のみ。
7. **銘柄の parquet は削除しない。** 無効化した銘柄は `data/archive/` へ退避。
8. **実装計画はリポジトリルートの `plans/` へ。`docs/` 配下は禁止**（日次 publish が `rsync --delete` するため消える）。
9. **Skill の正本は `.agents/skills/<name>/SKILL.md` のみ。** `.claude/skills/` は参照スタブ。symlink とファイル一式コピーは禁止。手順は 07 の Skills 節。

残りの不変条件（artifact/gate/manifest の互換検証、Phase 1 スキーマ版の bump、廃止済み `docs/history_data.json` 契約、赤=上昇／青=下落）と各項目の詳細はリンク先2ファイルにある。

## LEARNINGS.md ループ

各セッションの開始時に、リポジトリ直下の LEARNINGS.md を読め。
今回の作業に効く項目があればそれだけを本文で使い、無ければ触れなくてよい。
実質的なリポジトリ作業を完了して最終回答を返す前に、 `update-learnings` スキルを1回だけ実行せよ。 雑談、単純な質問、変更や再利用可能な学びがない作業では実行不要とする。
