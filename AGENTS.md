# AGENTS.md — このリポジトリのエージェント向け指示

`CLAUDE.md` は `@AGENTS.md` でこのファイルに委譲しています。ここに置くのは**索引**と
**絶対に外せないルール**だけです。担当する領域のリンク先を、作業を始める前に読んでください。

## 作業前に読むもの

| 場面                                         | 読むファイル                                                                                                                                   |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| 毎セッション、何よりも先に                   | [07_agent_conventions.md](specification_document/07_agent_conventions.md) — 作業規約（セッション開始、報告の作法、コマンド、編集前の注意）     |
| `main.py` / `src/` / `scripts/` を編集する前 | [08_invariants.md](specification_document/08_invariants.md) — 不変条件。`pipeline-safety-reviewer` サブエージェントがこのリストで審査する      |
| 実装済みの挙動を変える前                     | [specification_document/README.md](specification_document/README.md) — 領域別の as-built 正典（バックエンド、web、CI/CD、scripts、データ契約） |
| 状況や範囲を判断する前                       | [06_issues_and_backlog.md](specification_document/06_issues_and_backlog.md) — 未解決課題、決定記録、現在のゲート状況                           |
| キュレーションに触れる前                     | [ai_ticker_curation/00_overview.md](specification_document/ai_ticker_curation/00_overview.md) — 日次・週次・隔週キュレーションの設計           |
| システムの全体像を知りたいとき               | [README.md](README.md) — 日本株の自動予測・売買シグナルシステム（Phase 0〜3、`docs/` から GitHub Pages へ公開）                                |

## 絶対に外せない9項目

1. **まず `git pull --rebase`。** Actions が毎日 `main` にコミットするため、ローカルは
   ほぼ常に古い。
2. **日次シグナル生成は絶対に止めない。** DB・マクロ・保存モデル・Phase 2 のどれが落ちても、
   フォールバックまたはスキップ＋ログで縮退する。
3. **実行可能シグナルの前に必ず KPI ゲート。** 未達なら `HOLD`。
4. **Phase 2 は shadow のまま、出力はバイト単位で不変。** active への切替は、人間が意図的に
   行うゲート付きの手動 env 変更。
5. **`docs/` 配下にファイルやディレクトリを追加したら、`daily-publish-dashboard.yml` の
   `--exclude` に必ず追加する。** 漏れると次回 publish で削除される。
6. **エージェントは `tickers.yml` / `curation_pool.yml` を直接編集しない。** 書き手は
   `scripts/curation_merge.py` と `scripts/curation_pool_merge.py` だけ。
7. **銘柄の parquet は削除しない。** 無効化した銘柄は `data/archive/` へ退避する。
8. **報告はかみくだいた日本語で。** たとえ＋「直さないとどうなる」＋おすすめアクション1つ。
   専門用語・ハッシュ・パス・生の数値は末尾へ。
9. **実装計画はリポジトリルートの `plans/` へ。`docs/` の下には置かない**（日次 publish が
   `docs/` を `rsync --delete` するため）。

残りの不変条件（artifact/gate/manifest の互換検証、Phase 1 スキーマ版の bump、廃止済み
`docs/history_data.json` 契約、赤=上昇／青=下落）と、上記それぞれの詳細はリンク先2ファイルに
あります。
