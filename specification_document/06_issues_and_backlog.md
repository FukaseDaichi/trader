# 既知の課題・運用計画・バックログ

更新日: 2026-07-26 JST

この文書は「現時点で直っていないこと」「いつ対応するか」「次へ進める条件」を扱います。解決済みの修正履歴はgit logを参照してください。日付は最短の目安であり、条件未達なら延期します。

## 結論

- **2026-07-27（月）から2026-08-21（金）まで行う**: 最低4週間、日次・週次のshadow運用を監視する。期間経過だけで完了扱いにせず、観測数が実際に増えていることを必要条件にする。
- **2026-08-22（土）に最初の総合判定を行う**: 全active条件が揃った場合だけ、人間がactive化の是非を判断する。条件未達なら日付に関係なくshadowを継続する。
- **最短でも2026-08-24（月）まではactive化しない**: `TRADER_PORTFOLIO_MODE=shadow`を維持する。これは予定日ではなく、全条件合格時の最短日である。

2026-07-19以前のportfolio/shadow指標はv2/schema v3移行前の履歴であり、以降のactive判断には使用しない。

## 現在地（2026-07-26）

| 項目 | 状態 | 判断 |
| --- | --- | --- |
| execution contract v2 | 本番DB移行・再集計・監査完了 | 完了 |
| Phase 1 schema v3 | 50/50銘柄を学習し、manifest・checksum・runtime契約・DB registryを検証してactive化済み | 完了 |
| Phase 1個別KPI gate | `gate_passed_tickers=0/50` | 要監視。新しいactionable signalと決済サンプルが増えない可能性がある |
| drift | 50銘柄すべて実績サンプル不足。breachは未判定 | 初期状態として正常だが、4週間後も増えなければ品質調査が必要 |
| Phase 2レポート | 公式`docs/portfolio_backtest.json`は2026-07-25生成・`cs-v1-20260725`のままで、gate不合格（`ir_unavailable_same_basis`・`turnover>0.40`）、`active_ready=false`。同モデルの`oos_predictions.parquet`を使い2026-07-26にローカルで`topix_open`込みの再バックテストを実施し、`benchmark_coverage.coverage_ratio=1.0`、`information_ratio=0.9461`（`alpha=0.1554`、`beta=0.5141`、`tracking_error=0.1070`）、`gate.failures=["turnover>0.40"]`を測定 | ローカル測定は`ir_unavailable_same_basis`解消の裏付け。公式backtestへの反映は次回の定期パイプライン実行（2026-08-01週次retrain）を待つ |
| TOPIX benchmark | `topix_open`をマクロパネルに実装済み（詳細は上記Phase 2レポート行を参照） | active化のP0制約から除外。以後は評価対象の指標として扱う |
| 実行モード | core/retry workflowとも`TRADER_PORTFOLIO_MODE=shadow`を明示 | 維持する |

## 実施計画

### 1. 初週 — 観測が増えるか確認する（2026-07-27〜08-01）

最初の営業週は、単にエラーがないことではなく、次の分母が増えていることを確認する。

- 保存済みschema v3モデルの利用率とephemeral fallback率
- 銘柄別KPI gate通過数、actionable signal数、HOLD縮退理由
- v2の1/5/10日決済件数と`realized_ret`欠損
- driftの`n_outcomes`と`insufficient_sample_tickers`
- shadow reportの`n_paired_dates`、`n_paired_records`、date coverage、除外理由
- Phase 1/Phase 2のdaily IC差、portfolio gate、IR、turnover

**2026-08-01の判定点**:

- `gate_passed_tickers=0`が続き、actionable signal・決済・paired dataが増えない場合、4週間ただ待つのをやめてPhase 1品質調査を開始する。
- 調査では銘柄別の失敗理由、round trips、CAGR、平均日次net return、Sharpe、threshold選択、calibrationを確認する。
- シグナル数を作るためだけにKPI閾値を緩めない。モデル・特徴量・ラベル・サンプル設計の根拠が先である。
- artifact不整合、registry不一致、日次ephemeral fallback急増があれば、その日のうちに調査する。active化の時計は停止する。

### 2. TOPIX open方針の決定（2026-07-26、実装済み）

**決定: オプション1（Phase 2 activeを将来提供する）を選び、実装済み。** 同一basisのTOPIX open系列は既存のTOPIX連動ETF `1305.T`（現行の`topix`終値列と同一銘柄）から取得し、マクロパネルへ`topix_open`列として追加した。設計は`specification_document/plans/2026-07-26-topix-open-benchmark-design.md`（不採用にした案の理由も記載）、実装計画は`specification_document/plans/2026-07-26-topix-open-benchmark-plan.md`を参照。実装後、`cs-v1-20260725`の`oos_predictions.parquet`を用いてv2・net-vs-netでCSバックテストをローカル再実行し、`coverage_ratio=1.0`・`information_ratio=0.9461`を確認した（現在地表を参照）。公式`docs/portfolio_backtest.json`への反映は2026-08-01の週次retrainを待つ。

### 3. 4週間shadow監視（2026-07-27〜08-21）

週次確認日は2026-08-01、08-08、08-15、08-22とする。毎週、同じ観点で記録する。

| 観点 | 合格方向 | 即時停止条件 |
| --- | --- | --- |
| Phase 1 artifact | schema v3、全coverage、runtime互換、registry一致 | checksum/manifest/契約不一致 |
| Phase 1 gate | 通過銘柄とactionable観測が増え、失敗理由を説明できる | 0/50固定で観測が増えない |
| drift/reliability | outcome数が増え、sourceとversion provenanceが明確 | fallback急増、十分なサンプルで閾値breach |
| Phase 2 provenance | v2、exact CS version、paired coverageが増える | 旧契約・別version・欠損補完の混入 |
| portfolio KPI | gate合格、IR・DD・Sharpe・turnoverが有限で再現可能 | benchmark coverage不足、必須指標NULL、gate不合格 |
| 運用健全性 | DB/outbox/dead letter、ダイジェスト、dashboardが整合 | 書き込み停止、鮮度低下、通知とDBの不一致 |

4週間という期間は必要条件であって十分条件ではない。休場・HOLD・データ欠損で観測が増えなかった週は、カレンダーだけ進めてもactive判断の証拠に数えない。

### 4. 最初の総合判定（2026-08-22）

以下を**すべて**満たした場合だけ、active化を人間が検討できる。

- [ ] 4週間の週次記録があり、観測数とpaired coverageが実際に増加
- [ ] Phase 1 schema v3のruntime/manifest/registryが継続して整合
- [ ] Phase 1のKPI通過・actionable signal・決済サンプルが、判断に使える量まで増加
- [x] TOPIX同一basis open系列の契約・履歴・完全coverageを確認（2026-07-26、`cs-v1-20260725`によるローカル再バックテストで`coverage_ratio=1.0`を測定。公式`docs/portfolio_backtest.json`は未反映のため、2026-08-22のレビューで同じcoverageが公式artifactでも再現されることを確認する）
- [ ] portfolio backtestが現行v2、strategy net対benchmark net、必須指標有限、明示的gate合格
- [ ] v2 shadow分布を根拠に`TRADER_PORTFOLIO_BACKTEST_MAX_TURNOVER`を再校正し、変更理由を記録
- [ ] shadow reportの`active_readiness.active_ready=true`
- [ ] backtestと当日snapshotのCS `model_version`が完全一致
- [ ] 最終的なactive化を人間が明示承認

1項目でも未達なら`shadow`を継続する。TOPIX同一basis benchmarkは実装済みだが、現在の証拠ではportfolio gate不合格（`turnover>0.40`・`cs_ic_vs_phase1`が負）とPhase 1 gate 0/50のためactive化できない。

### 5. 条件合格後のみ — controlled active化（最短2026-08-24）

- coreとretryの`TRADER_PORTFOLIO_MODE`を同時に`active`へ変更する。片方だけ変更しない。
- 最初の1週間は毎朝、ダイジェストの建玉、`docs/portfolio_latest.json`、DB `signals.target_weight`を照合する。
- gate fail、snapshot欠損、CS version不一致時にtarget weightが反映されず、安全に縮退することを確認する。
- 最初の週次レビューは2026-08-29を目安とする。異常時は`shadow`へ戻す。

## 今後の流れの妥当性検証

全体の順序は妥当だが、次の補強を入れた。

1. **旧shadowレポートを基準にしない**: v2/schema v3移行前の2026-07-19レポートは条件が違うため、2026-07-25を新しい基準日にする。
2. **観測数の増加を初週に判定する**: 現在0/50 gateのため、期間だけ待っても証拠が増えないリスクがある。2026-08-01に品質調査への分岐を置く。
3. **turnover再校正を最後にする**: 現行`0.40`を今変更せず、v2 shadow分布と同一basis benchmarkが揃ってから決める。
4. **active化を日付で自動実行しない**: 2026-08-24は最短日であり、全ゲートと人間承認が優先する。

## 継続中のP0制約

### Phase 2 active化を禁止する

同一basisのTOPIX open系列は`topix_open`として実装済みであり、`ir_unavailable_same_basis`はもはやactive化を禁止する理由ではない（詳細は現在地表を参照）。ただし次の2件は本設計の対象外として明示的に残っている（`specification_document/plans/2026-07-26-topix-open-benchmark-design.md`の期待結果表を参照）。

- `portfolio_backtest.json`のgateが`turnover>0.40`で不合格のまま
- `cs_ic_vs_phase1`が負のまま（Phase 2のCS ICがPhase 1を下回る）

これに加えてPhase 1個別KPI gateも`gate_passed_tickers=0/50`のままである。`portfolio_backtest.json`が現行v2、net-vs-net、benchmark完全coverage、明示的gate合格、当日snapshotと同じCS `model_version`を満たす場合だけactive可とする。

turnoverの再校正、`cs_ic_vs_phase1`の改善、Phase 1 gate通過数の回復のいずれも確認できるまでは`TRADER_PORTFOLIO_MODE=shadow`を維持する。

## 対応しない（方針）

### 週次レポートの品質検証は実装しない

AIが書く`reports/weekly_*.md`は内容チェックなしでURLがLINE通知されますが、シグナルや売買判断には影響しないため、リスクはレポートの読み味だけです。品質はこだわらない方針です。

## 低優先・観察

| 項目 | いつ判断するか | 内容 |
| --- | --- | --- |
| CS較正の粗さ | 2026-08-22のshadowレビュー | 同値scoreや低grossが成績・分散を実際に阻害した場合だけ、isotonic連続化を検討する |
| `8766.JP`の旧履歴異常 | 2026-08-22までに再確認。学習失敗やdrift breachになれば即時 | 2005年の株式分割級不連続が残る。学習開始日カットまたはcorporate action補正を検討する |
| `usdjpy`の行数が少ない | macro鮮度警告が出た時だけ | 系列の歴史差によるもので、現時点では異常ではない |
| 月次監査・stress testのモデル同一性 | P1運用移行完了後 | 配備Phase 1 exact candidateの`gate_evidence`を再評価する監査へ寄せる。日次action・active化は制御しない |

## Phase 4+バックログ

| 項目 | 着手条件・時期 |
| --- | --- |
| fills（手動約定）記録 | active pilotを行う方針が決まった後。提案と実約定の乖離計測が必要になった時 |
| 発注指示出力（証券会社CSV/API） | fillsとリスク管理を先に整備し、不可逆処理を決定論コードに限定できた後 |
| `signals.action`のポートフォリオ駆動化再評価 | controlled active pilotの結果を確認した後 |
| `active_readiness`のGitHub Issue自動起票 | TOPIX coverageを実装し、active readinessが実際の運用判断に使える段階 |
| DB長期アーカイブとAlembic | DBサイズ警告（既定400MB）が近づいた時 |
| ダッシュボードの認証・ユーザー管理 | 公開範囲を限定する要件または複数ユーザー要件が生じた時 |
