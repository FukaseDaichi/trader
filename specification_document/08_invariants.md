# 不変条件

更新日: 2026-08-24 JST

このリポジトリでの変更が絶対に破ってはならないルールです。`pipeline-safety-reviewer`
サブエージェントは `main.py`・`src/`・`scripts/` の変更をこのリストで審査し、`.claude/hooks/`
はこのうち3つを編集時に強制します。作業規約は
[07_agent_conventions.md](07_agent_conventions.md)。

- Python 3.13 を `uv` で管理する。テストは `tests/` 配下の素の Python スクリプト。
- **日次シグナル生成は絶対に止めない**: DB・マクロ・保存モデル・Phase 2 のどれが落ちても
  縮退する（フォールバック、またはスキップ＋ログ）。`main.py` とその依存を変更するときは
  この性質を必ず保つ。
- 実行可能シグナルの前に必ず KPI ゲートを通す。未達なら `HOLD`。
- 保存モデル・ドリフト・モデル品質の読み手は、同一の runtime artifact/gate/manifest 互換検証を
  共有する。古い、または壊れた artifact は fail-closed とし、現在の品質証跡として提示しない。
- Phase 1 の特徴量の意味を列名を変えずに変更した場合は、Phase 1 artifact のスキーマ版を bump
  して再学習する。順序付き feature hash だけでは、同名のまま意味が変わった変更を識別できない。
- **Phase 2 は shadow。** shadow の挙動はバイト単位で不変に保ち、ポートフォリオ側のコードは
  Phase 1 のシグナルと通知を変更しない。active 配線（`portfolio.merge_target_weights`）は
  実装済みだが、切替はポートフォリオ KPI ゲートと `docs/portfolio_shadow_report.json` の
  `active_readiness` を条件とする、**人間が意図的に行う手動の
  `TRADER_PORTFOLIO_MODE=active` 変更**のままとする。条件の全リストと現在の状況は
  [06_issues_and_backlog.md](06_issues_and_backlog.md) にあるので、ここには書き写さない。
- `daily-publish-dashboard.yml` は `web/out/` を `docs/` へ `rsync --delete` で同期する。
  **`docs/` 配下に新しいファイルやディレクトリを追加したら、必ずその workflow の `--exclude`
  リストにも追加する。** 漏れると次回 publish で削除される。
  `tests/test_publish_workflow.py` が守るのは docs 直下の JSON 出力だけなので、
  ディレクトリを追加したときは exclude リストを目で確認する。
- エージェントに `tickers.yml` と `curation_pool.yml` を直接編集させない。書き手は決定論
  スクリプトの `scripts/curation_merge.py` と `scripts/curation_pool_merge.py` だけで、
  ガードレール下で動き、PreToolUse フック
  `.claude/hooks/protect-deterministic-files.sh` がこれを強制する
  （[ai_ticker_curation/00_overview.md](ai_ticker_curation/00_overview.md)）。明示的に依頼
  されたユニバース選定では、決定論の `scripts/universe_select.py --apply` を使ってよい。
- 無効化した銘柄の parquet は `data/archive/` へ退避し、**削除しない**。銘柄の履歴はソース
  フィードから再生成できない。
- `docs/history_data.json` は廃止済みの契約: `src/dashboard.py` が削除し、フロントエンドは
  読まない。
- 日本語 UI の約束: 赤は上昇、青は下落。
