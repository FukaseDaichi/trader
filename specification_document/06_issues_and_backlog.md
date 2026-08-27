# 既知の課題・運用計画・バックログ

更新日: 2026-08-27 JST

この文書は「現時点で直っていないこと」「いつ対応するか」「次へ進める条件」「今後実装する予定のもの」を扱う唯一の一覧です。解決済みの修正履歴はgit log、完了した計画・過去の観測地点・決定記録の全文（採用理由と不採用案）は[archive/06_issues_archive.md](archive/06_issues_archive.md)を参照してください。日付は最短の目安であり、条件未達なら延期します。

## 結論

- **2026-08-24 最初の総合判定を実施（判定者: 人間、記録者: agent）**: 結果は**shadow継続**。portfolio KPI gate不合格（`ir<0.00`、`turnover>0.40`）と`active_readiness.active_ready=false`が残るため、active化はしない。
- **Phase 2は観察継続（畳まない・作り直さない）**: 判断根拠と次回判定は「Phase 2の継続判断」を参照。次回レビューは2026-08-29（土）の週次再学習後。
- `TRADER_PORTFOLIO_MODE=shadow`を維持する。active化は全条件合格＋人間の明示承認があった場合のみ。

2026-07-19以前のportfolio/shadow指標はv2/schema v3移行前の履歴であり、以降のactive判断には使用しない。

## 現在地（2026-08-24）

2026-08-22の週次再学習（`cs-v1-20260822`）と2026-08-24の日次運用まで反映した最新値。

| 項目 | 状態 | 判断 |
| --- | --- | --- |
| Phase 2公式backtest | `cs-v1-20260822`: IR **-0.0901**（前回-0.3905から改善）、turnover **0.9919**（前回0.9205から悪化）、Sharpe 2.03、max DD -4.7%、benchmark coverage 1.0。gate failures=`["ir<0.00","turnover>0.40"]` | gate不合格。IRは改善方向だが負 |
| Phase 2 shadow report | 2026-08-22時点で`shadow_days=24`、`n_paired_dates=24`、`n_paired_records=79`、`cs_ic_vs_phase1=**-0.2825**`（前回-0.2404から悪化）、`active_ready=false` | shadow維持 |
| 8766.JP旧履歴異常 | 2026-08-24確認: model_qualityに正常掲載（IC 0.3438、Brier 0.2256、calibration 120行）、drift `breached=false`（PSI warningはmacro `usdjpy_ret_60`由来で全銘柄共通傾向） | 即時対応条件（学習失敗・drift breach）に非該当。観察継続 |
| 日次運用 | 2026-08-24のcore daily・curation・publishまで正常コミット | 正常 |

## 週次レビュー（継続中）

4週間shadow監視（2026-07-27〜08-21、完了・詳細はarchive）から継続する週次確認。次回は2026-08-29（土）の週次再学習後。毎週、同じ観点で記録する。

| 観点 | 合格方向 | 即時停止条件 |
| --- | --- | --- |
| Phase 1 artifact | schema v3、全coverage、runtime互換、registry一致 | checksum/manifest/契約不一致 |
| Phase 1 gate | 通過銘柄とactionable観測が増え、失敗理由を説明できる | 0/50固定で観測が増えない |
| drift/reliability | outcome数が増え、sourceとversion provenanceが明確 | fallback急増、十分なサンプルで閾値breach |
| Phase 2 provenance | v2、exact CS version、paired coverageが増える | 旧契約・別version・欠損補完の混入 |
| portfolio KPI | gate合格、IR・DD・Sharpe・turnoverが有限で再現可能 | benchmark coverage不足、必須指標NULL、gate不合格 |
| 運用健全性 | DB/outbox/dead letter、ダイジェスト、dashboardが整合 | 書き込み停止、鮮度低下、通知とDBの不一致 |

休場・HOLD・データ欠損で観測が増えなかった週は、カレンダーだけ進めてもactive判断の証拠に数えない。

### active化の総合判定チェックリスト

**最初の総合判定（2026-08-22予定 → 2026-08-24実施）: shadow継続。** gate合格・turnover再校正・`active_ready=true`が未達（portfolio gate failures=`["ir<0.00","turnover>0.40"]`、`cs_ic_vs_phase1=-0.2825`）。次回判定は週次レビューの一環として継続する。

以下を**すべて**満たした場合だけ、active化を人間が検討できる。1項目でも未達なら`shadow`を継続する。

- [ ] 4週間の週次記録があり、観測数とpaired coverageが実際に増加
- [ ] Phase 1 schema v3のruntime/manifest/registryが継続して整合
- [ ] Phase 1のKPI通過・actionable signal・決済サンプルが、判断に使える量まで増加
- [x] TOPIX同一basis open系列の契約・履歴・完全coverageを確認（2026-08-01の公式`docs/portfolio_backtest.json`で`coverage_ratio=1.0`を確認済み）
- [ ] portfolio backtestが現行v2、strategy net対benchmark net、必須指標有限、明示的gate合格
- [ ] v2 shadow分布を根拠に`TRADER_PORTFOLIO_BACKTEST_MAX_TURNOVER`を再校正し、変更理由を記録
- [ ] shadow reportの`active_readiness.active_ready=true`
- [ ] backtestと当日snapshotのCS `model_version`が完全一致
- [ ] 最終的なactive化を人間が明示承認

### 条件合格後のみ — controlled active化

- coreとretryの`TRADER_PORTFOLIO_MODE`を同時に`active`へ変更する。片方だけ変更しない。
- 最初の1週間は毎朝、ダイジェストの建玉、`docs/portfolio_latest.json`、DB `signals.target_weight`を照合する。
- gate fail、snapshot欠損、CS version不一致時にtarget weightが反映されず、安全に縮退することを確認する。
- 異常時は`shadow`へ戻す。

## 継続中のP0制約

### Phase 2 active化を禁止する

公式`docs/portfolio_backtest.json`のportfolio KPI gateが不合格（`ir<0.00`、`turnover>0.40`。最新の実測値は「現在地」を参照）。`portfolio_backtest.json`が現行v2、strategy net対benchmark net、benchmark完全coverage、明示的gate合格、当日snapshotと同じCS `model_version`を満たす場合だけactive可とする。解消するまでは`TRADER_PORTFOLIO_MODE=shadow`を維持する。

### `execution.BENCHMARK_BASIS`を変更しない（2026-08-10 決定の要点）

- この定数（`unavailable_same_basis`）は`execution_contract_metadata()`経由で`gate_contract_sha256`に入る。値を変えると現行active modelの契約ハッシュが動き、**翌営業日から全50銘柄がephemeral candidateへ縮退**する。しかも`drift_check.py`はexit 0のまま監視が静かに止まる。
- 同一basis benchmarkを実際に算出できた利用側は、`SAME_BASIS_BENCHMARK`で自分の出力dictの`benchmark_basis`だけを上書きする（採用済み: `src/portfolio_backtest.py`・`src/performance.py`・`scripts/settle_outcomes.py`）。
- `execution_contract_metadata()`が返す内容を変える変更では、`model_store.build_phase1_gate_contract()`を`data/models/active_model.json`の保存値と突き合わせ、`gate_contract_sha256`の一致を必ず確認する。
- ベンチマーク原資産の採用判断（`1305.T`）と不採用案の全記録は[archive/06_issues_archive.md](archive/06_issues_archive.md)。列の契約は`05_cross_cutting.md`が正典。

### Phase 2の継続判断

TOPIX open実装の目的は「active化の実現」ではなく「IRという評価軸を初めて測定可能にすること」だった。公式artifactで測った結果はIR **負**であり、これはPhase 2を縮小・shadow-onlyに畳む根拠になりうる。

**判断記録（2026-08-24、人間が決定）: 観察継続（畳まない・作り直さない）。**

- 根拠: shadowはPhase 1の本番シグナル・通知に一切影響せず、維持コストは週次再学習が回るだけでほぼゼロ。IRは-0.3905（08-01）→-0.0901（08-22）と明確に改善方向。
- 懸念: `cs_ic_vs_phase1`は-0.2404→-0.2825、turnoverは0.9205→0.9919と悪化。
- 次回判定: 週次レビュー（次回2026-08-29）で同じ3指標（IR、cs_ic_vs_phase1、turnover）を確認する。`cs_ic_vs_phase1`の悪化が続く場合はshadow-only縮小（畳む）方向へ傾ける。学習設計の作り直し（育てる）は、ICが悪い原因の分析を先に行ってからでないと着手しない。

## 対応しない（方針）

### 週次レポートの品質検証は実装しない

AIが書く`reports/weekly_*.md`は内容チェックなしでURLがLINE通知されますが、シグナルや売買判断には影響しないため、リスクはレポートの読み味だけです。品質はこだわらない方針です。

## 今後の実装予定（統合バックログ）

未実装・未着手のものはすべてここに集約します。個別の実装計画ドキュメントは作らず、着手が決まった時点で`plans/YYYY-MM-DD-<topic>.md`を作成し、完了したら削除してこの表へ戻します。

### 着手条件が既に揃っているもの

| 項目 | 内容 | 備考 |
| --- | --- | --- |
| `TRADER_PORTFOLIO_BACKTEST_MAX_TURNOVER`の再校正 | 現行`0.40`に対し実測`0.9919`（2026-08-22）。v2 shadow分布と同一basis benchmarkが揃ったので、根拠付きで再設定する | active総合判定の必須項目。数値合わせのために緩めない。2026-08-24判断でPhase 2は観察継続のため、着手は畳む/育てるの方向が定まってから |
| `cs_ic_vs_phase1`の改善 | Phase 2のCS ICがPhase 1を下回る（`-0.2825`、悪化傾向）。まず原因分析、その上で特徴量・ラベル・学習設計の見直し | 改善見込みが立たない場合はPhase 2縮小の判断材料（2026-08-24判断: 悪化継続ならshadow-only縮小へ傾ける） |

### 観察中（条件が揃ったら判断）

| 項目 | いつ判断するか | 内容 |
| --- | --- | --- |
| CS較正の粗さ | 次回shadowレビュー（2026-08-29以降） | 同値scoreや低grossが成績・分散を実際に阻害した場合だけ、isotonic連続化を検討する |
| `8766.JP`の旧履歴異常 | 学習失敗やdrift breachになれば即時（2026-08-24再確認済み: 学習正常・breachなしで非該当） | 2005年の株式分割級不連続が残る。学習開始日カットまたはcorporate action補正を検討する |
| `usdjpy`の行数が少ない | macro鮮度警告が出た時だけ | 系列の歴史差によるもので、現時点では異常ではない |
| 月次監査・stress testのモデル同一性 | P1運用移行完了後 | 配備Phase 1 exact candidateの`gate_evidence`を再評価する監査へ寄せる。日次action・active化は制御しない |

### Phase 4+

| 項目 | 着手条件・時期 |
| --- | --- |
| fills（手動約定）記録 | active pilotを行う方針が決まった後。提案と実約定の乖離計測が必要になった時 |
| 発注指示出力（証券会社CSV/API） | fillsとリスク管理を先に整備し、不可逆処理を決定論コードに限定できた後 |
| `signals.action`のポートフォリオ駆動化再評価 | controlled active pilotの結果を確認した後 |
| `active_readiness`のGitHub Issue自動起票 | active readinessが実際の運用判断に使える段階（TOPIX coverageは実装済み） |
| DB長期アーカイブとAlembic | DBサイズ警告（既定400MB）が近づいた時 |
| ダッシュボードの認証・ユーザー管理 | 公開範囲を限定する要件または複数ユーザー要件が生じた時 |
