# 既知の課題・運用チェックリスト・バックログ

更新日: 2026-07-24 JST

この文書は「現時点で直っていないこと」と「対応方針」を扱います。各項目は**かみくだき説明**を先頭に置きます。解決済みの修正履歴は git log を参照してください。

## 要対応

### P0運用移行 — Phase 1 schema v3モデルを再学習・検証する

**かみくだき**: 旧モデルは新しいラベル、特徴量、較正、約定、KPI証跡の完全性を証明できないため、意図的に互換性エラーとなる。日次`auto`は自身のOOS証跡を持つephemeral candidateへ縮退し、strict `phase1`は`HOLD`になる。新しい週次候補を一度作り、全銘柄の証跡が揃った版だけをactiveにする必要がある。

実施順:

1. `uv run python scripts/weekly_model_retrain.py --output docs/weekly_retrain_report.json`を実行する。
2. `deployment.candidate_validation.passed=true`、全対象coverage、artifact schema v3、manifest checksum、active pointerのversion一致を確認する。
3. `model_registry`登録成功、またはDB不通時にregistry eventが`data/outbox/`へ1件だけ安定IDで待機していることを確認する。
4. `docs/model_quality.json`と`docs/drift_report.json`が同じruntime契約のactive版を参照することを確認する。

### P0制約 — Phase 2 active化を継続禁止する

**かみくだき**: 現在のマクロパネルはTOPIX終値しか持たず、戦略と同じ「翌営業日寄付き→H営業日目終値」の比較リターンを作れない。これを終値同士のリターンで代用するとIRが別条件になるため、benchmarkは意図的にunavailableとなり、activeゲートは閉じる。

- `portfolio_backtest.json`は現行v2、net-vs-net、benchmark完全coverage、明示的なgate合格、かつ当日snapshotと同じCS `model_version`が揃う場合だけactive可とする。旧レポートで新モデルをactive化しない。
- 同一basisのTOPIX open系列を取得・検証するまでは`TRADER_PORTFOLIO_MODE=shadow`を維持する。
- TOPIX openを追加しない方針なら、Phase 2 activeは未提供のままとし、benchmarkなしshadow分析として明記する。

### P1運用移行 — 設定名と4週間shadow監視

- 実行環境から旧`TRADER_KPI_MIN_EXPECTANCY`、`TRADER_KPI_MIN_TRADES`、`TRADER_AUTO_THRESHOLD_MIN_TRADES`、objective=`expectancy`、`TRADER_LABEL_MODE=vol_norm`を除去し、`.env.example`のcanonical名へ移行する。旧値は警告付きalias／安全縮退としてしか残っていない。
- v2再集計とschema v3 active化後、新旧成績差、観測数、ephemeral fallback率、銘柄別ゲート通過率、reliability source内訳を最低4週間shadow監視する。
- Phase 2の`turnover`はv2で「旧book全決済＋新book全建て＋最終決済」の両側notionalへ意味が変わった。既定`TRADER_PORTFOLIO_BACKTEST_MAX_TURNOVER=0.40`は旧netted turnover由来なので、TOPIX open導入後のactive判定前にv2 shadow分布から再校正する。根拠が揃うまでは閾値を無変更のままfail-closeさせる。
- 4週間経過後も、Phase 2の同一basis benchmark coverageと`active_readiness`を満たした場合に限り、人間がactive化を別途判断する。

## 対応しない（方針）

### 週次レポートの品質検証は実装しない

AI が書く `reports/weekly_*.md` は内容チェックなしで URL が LINE 通知されますが、シグナルや売買判断には一切影響しないため、リスクは「レポートの読み味」だけです。品質はこだわらない方針。

## 低優先・観察

| 項目                        | 内容                                                                                                                                                                                                                                     |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| CS 較正の粗さ               | shadow の建玉で複数銘柄の `expected_ret`/`prob_up` が同値（score-bucket 較正の粒度）。gross も低め（0.24 前後）。バグではなく shadow 期間の観察対象。改善候補: isotonic 連続化                                                           |
| `8766.JP` の旧履歴異常      | Yahoo の調整済み系列にも 2005 年の株式分割級の不連続（最大 17295.3%）が残る。現行検証は警告して処理継続するため日次停止にはならないが、次回は学習用履歴の開始日カットまたは corporate action 補正を検討する             |
| `usdjpy` の行数が少ない     | 系列の歴史差によるもので異常ではない（参考情報）                                                                                                                                                                                         |
| 月次監査・stress testのモデル同一性 | `monthly_audit.py` / `stress_test.py`は独立`evaluate_kpi_gate()`シミュレーションで、配備Phase 1 exact candidateの保証ではない。日次action・active化は制御しない。将来はschema v3 `gate_evidence`を再評価する監査へ寄せる |

## 運用チェックリスト（時限・要人間判断）

- [ ] schema v3週次候補を全対象coverageでactive化し、registry同期状態を確認する
- [ ] canonical envへ移行し、deprecation warningが消えたことを確認する
- [ ] v2/schema v3移行後の4週間shadow監視を完了する
- [ ] v2 shadowのturnover分布から`TRADER_PORTFOLIO_BACKTEST_MAX_TURNOVER`を再校正する
- [ ] TOPIX同一basis open系列とexact CS model-version gateを含む全active条件を確認する
- [ ] active 化後 1 週間: ダイジェストの建玉と DB `signals.target_weight` の一致を毎朝確認

## Phase 4+ バックログ（未着手の将来案）

- **fills（約定）記録**: 手動約定の入力経路 → `fills` テーブル → 提案 vs 実約定の乖離計測
- 発注指示出力（証券会社 CSV / API、`src/execution.py`。不可逆処理は決定論コード限定の原則を維持）
- `signals.action` のポートフォリオ駆動化の再評価（現状は active でも action はモデル由来のまま）
- `active_readiness` の GitHub Issue 自動起票
- TOPIX open系列の取得元・調整方法・欠損時契約の決定（実装まではPhase 2 active不可）
- DB 長期アーカイブ自動化（`backtest_equity` の parquet 退避、400MB 警告は既存）と Alembic 導入判断
- ダッシュボードの認証・ユーザー管理
