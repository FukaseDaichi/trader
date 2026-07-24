# specification_document の構成

更新日: 2026-07-24 JST

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
| [06_issues_and_backlog.md](06_issues_and_backlog.md) | 既知の課題・運用チェックリスト・将来バックログ |
| [plans/2026-07-20-critical-high-priority-remediation.md](plans/2026-07-20-critical-high-priority-remediation.md) | 2026-07-20設計レビューのP0/P1一覧、解決方針、実装結果、運用移行チェック |
| [ai_ticker_curation/](ai_ticker_curation/) | AI銘柄キュレーションの設計・契約（スクリプトのコードコメントから参照される正典） |

## 運用ルール

1. **仕様はソースコードを正として書く。** コードと食い違いを見つけたら、コードに合わせて該当ドキュメントを更新し、`更新日` を変える。
2. **通常の実装計画は完了したら削除する。** 新しい改修を計画するときは `plans/YYYY-MM-DD-<topic>.md` を作成し、実装・検証が完了したら削除する。削除前に、積み残しの課題・運用タスク・将来バックログを `06_issues_and_backlog.md` へ移す。ただし、ユーザーが保存を求めた優先度一覧、意思決定記録、または運用移行中の計画は、状態と未完了項目を明記して保持する。
3. 削除済み計画は git 履歴で参照できる:

   ```bash
   git log --diff-filter=D --summary -- 'specification_document/plans/' 'specification_document/improvement_roadmap.md'
   ```

   Phase 0（計測基盤）/ Phase 1（シグナル品質）/ Phase 2（クロスセクション・ポートフォリオ）/ Phase 3（手動トレードUX・堅牢化）の各計画と、その大元の `improvement_roadmap.md` は **全フェーズ実装完了を確認のうえ 2026-06-11 に削除**した（当時のテスト20スイート全パス、成果物突合済み）。
4. `ai_ticker_curation/` 配下のファイル名は `scripts/curation_*.py` や `curation_pool.yml` のコメントから参照されているため、改名・削除しない。
