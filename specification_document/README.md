# specification_document の構成

更新日: 2026-08-24 JST

このディレクトリは、**ソースコードを正とする現行仕様（as-built）**と、**未解決の課題・バックログ**を管理します。通常の実装計画は完了後に削除しますが、ユーザー指定の優先度一覧や運用移行を伴う計画は、判断記録として明示的に保持できます。

## 構成

| パス | 内容 |
|---|---|
| [00_overview.md](00_overview.md) | システム全体像・レイヤ構成・現在地 |
| [01_backend_python.md](01_backend_python.md) | 日次パイプライン（`main.py`）と `src/` モジュール仕様 |
| [02_frontend_web.md](02_frontend_web.md) | Next.js ダッシュボード（`web/`）仕様 |
| [03_cicd_workflows.md](03_cicd_workflows.md) | GitHub Actions ワークフロー仕様 |
| [04_scripts.md](04_scripts.md) | 補助スクリプト（`scripts/`）仕様 |
| [05_cross_cutting.md](05_cross_cutting.md) | データ契約（docs/ JSON・DB・parquet）と横断仕様 |
| [06_issues_and_backlog.md](06_issues_and_backlog.md) | 既知の課題・運用チェックリスト・決定記録・**今後の実装予定（統合バックログ）** |
| [07_agent_conventions.md](07_agent_conventions.md) | エージェント作業規約（セッション開始・報告の作法・コマンド・編集前の注意）。ルート `AGENTS.md` の詳細版 |
| [08_invariants.md](08_invariants.md) | 不変条件の全リスト（`pipeline-safety-reviewer` と `.claude/hooks/` が参照する正典） |
| [ai_ticker_curation/](ai_ticker_curation/) | AI銘柄キュレーションの設計・契約（スクリプトのコードコメントから参照される正典） |

`plans/` ディレクトリは現在存在しません（進行中の実装計画があるときだけ作成します）。未実装の予定はすべて `06_issues_and_backlog.md` の「今後の実装予定（統合バックログ）」に集約しています。

## 運用ルール

1. **仕様はソースコードを正として書く。** コードと食い違いを見つけたら、コードに合わせて該当ドキュメントを更新し、`更新日` を変える。
2. **実装計画は完了したら削除する。** 新しい改修を計画するときは `plans/YYYY-MM-DD-<topic>.md` を作成し、実装・検証が完了したら削除する。削除前に、(a) as-built仕様（`01`〜`05`）へ確定した契約を反映し、(b) 残った課題・運用タスク・将来バックログ・繰り返したくない意思決定記録を `06_issues_and_backlog.md` へ移す。plans配下に長期保管するファイルは作らない。
3. 削除済み計画は git 履歴で参照できる:

   ```bash
   git log --diff-filter=D --summary -- 'specification_document/plans/' 'specification_document/improvement_roadmap.md'
   ```

   Phase 0（計測基盤）/ Phase 1（シグナル品質）/ Phase 2（クロスセクション・ポートフォリオ）/ Phase 3（手動トレードUX・堅牢化）の各計画と、その大元の `improvement_roadmap.md` は **全フェーズ実装完了を確認のうえ 2026-06-11 に削除**した（当時のテスト20スイート全パス、成果物突合済み）。
4. `ai_ticker_curation/` 配下のファイル名は `scripts/curation_*.py` や `curation_pool.yml` のコメントから参照されているため、改名・削除しない。
