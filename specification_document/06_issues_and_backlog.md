# 既知の課題・運用計画・バックログ

更新日: 2026-08-24 JST

この文書は「現時点で直っていないこと」「いつ対応するか」「次へ進める条件」「今後実装する予定のもの」を扱う唯一の一覧です。解決済みの修正履歴はgit logを参照してください。日付は最短の目安であり、条件未達なら延期します。

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

## 前回地点（2026-08-02、参考）

| 項目 | 状態 | 判断 |
| --- | --- | --- |
| execution contract v2 | 本番DB移行・再集計・監査完了 | 完了 |
| Phase 1 schema v3 | 50/50銘柄を学習し、manifest・checksum・runtime契約・DB registryを検証してactive化済み | 完了 |
| Phase 1個別KPI gate | independent cohort gate（metrics schema v3）をremoteへ反映済み。2026-08-01の週次再学習が`per-ticker-v1-20260801T090258-76bcfb375e42-9b960e8b`を生成。`data/outbox/`はディレクトリ自体が無く、registry eventの滞留なし。本番2026-07-31時点で50銘柄中7銘柄がgate通過、actionable 7件（MILD_BUY 6・MILD_SELL 1） | コード・artifact・観測は本番稼働。残るのは5営業日観測の完了（2026-08-03、08-04）のみ |
| drift | 50銘柄すべて`warning`（実績サンプル不足）。`breached=false` | 調査完了（2026-08-05）: 旧実装はactive `model_version`完全一致でoutcomeを数えており、週次バージョン更新×非HOLDのみ決済×5営業日ラグの掛け算で`n_outcomes`が構造的に常時0（全履歴で0を確認）。model lineage（`model_registry.kind`）でプールする方式へ修正済み（`db.fetch_prediction_outcomes_for_kind`、`drift_report.outcome_scope`で判別可）。修正後は46 outcomes/16銘柄を観測。ただし非HOLDシグナルが少ないため`min_outcomes=30`/銘柄への到達はなお時間を要する。今後サンプルは単調増加するので、増えない場合のみ再調査 |
| Phase 2レポート | 公式`docs/portfolio_backtest.json`が2026-08-01に`cs-v1-20260801`で再生成され、`benchmark_coverage.coverage_ratio=1.0`（35/35期間）。`information_ratio=-0.3905`、`alpha=0.0364`、`beta=0.3846`、`tracking_error=0.1210`、`turnover=0.9205`、`gate.failures=["ir<0.00","turnover>0.40"]` | `ir_unavailable_same_basis`は解消。公式artifactで初めてIRが測れた結果、**IRは負**。Phase 2を育てるか畳むかの判断材料が揃った（下記「Phase 2の継続判断」） |
| Phase 2 shadow report | 2026-08-01時点で`shadow_days=19`、`n_paired_dates=19`、`n_paired_records=54`、`cs_ic_vs_phase1=-0.2404`、`active_ready=false` | 維持する |
| TOPIX benchmark | `topix_open`をマクロパネルに実装済み・本番反映済み | active化のP0制約から除外。以後は評価対象の指標として扱う |
| 実行モード | core/retry workflowとも`TRADER_PORTFOLIO_MODE=shadow`を明示 | 維持する |

## 実施計画

### 1. Phase 1 independent cohort gateの本番観測（2026-07-29〜08-04）

2026-07-29に本番反映した。残作業は最初の5営業日（07-29、07-30、07-31、08-03、08-04）の観測完了のみで、07-31までの3営業日は正常である。

毎営業日、次を確認する。

- 銘柄別KPI gate通過数、actionable signal数、HOLD縮退理由
- independent cohorts、round trips、CAGR、平均日次net return、Sharpe、threshold選択、calibration
- 保存済みschema v3モデルの利用率とephemeral fallback率（fallbackは0が正常）
- v2の1/5/10日決済件数と`realized_ret`欠損
- driftの`n_outcomes`と`insufficient_sample_tickers`

**ロールバック条件**（1つでも該当したら、旧コードと旧artifact pointerを同じ単位で戻す。新gate evidenceを旧設定で再利用せず、旧contractに一致するartifactへ戻す）:

- artifact、manifest、gate contract、registryの不一致
- 日次処理の停止
- actionable signalが10件超の日が2営業日連続
- gate通過が再び0/50で2営業日連続
- settlement、DB write-through、LINE digestの契約不整合
- 事前評価を大きく超えるdrawdownまたはturnover異常

シグナル数を作るためだけにKPI閾値を緩めない。モデル・特徴量・ラベル・サンプル設計の根拠が先である。

### 2. 4週間shadow監視（2026-07-27〜08-21）

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

### 3. 最初の総合判定（2026-08-22予定 → 2026-08-24実施）

**判定結果（2026-08-24）: shadow継続。** 下記チェックリストのうちgate合格・turnover再校正・`active_ready=true`が未達（portfolio gate failures=`["ir<0.00","turnover>0.40"]`、`cs_ic_vs_phase1=-0.2825`）。次回判定は週次レビューの一環として継続する。

以下を**すべて**満たした場合だけ、active化を人間が検討できる。

- [ ] 4週間の週次記録があり、観測数とpaired coverageが実際に増加
- [ ] Phase 1 schema v3のruntime/manifest/registryが継続して整合
- [ ] Phase 1のKPI通過・actionable signal・決済サンプルが、判断に使える量まで増加
- [x] TOPIX同一basis open系列の契約・履歴・完全coverageを確認（2026-08-01の公式`docs/portfolio_backtest.json`で`coverage_ratio=1.0`を確認済み）
- [ ] portfolio backtestが現行v2、strategy net対benchmark net、必須指標有限、明示的gate合格
- [ ] v2 shadow分布を根拠に`TRADER_PORTFOLIO_BACKTEST_MAX_TURNOVER`を再校正し、変更理由を記録
- [ ] shadow reportの`active_readiness.active_ready=true`
- [ ] backtestと当日snapshotのCS `model_version`が完全一致
- [ ] 最終的なactive化を人間が明示承認

1項目でも未達なら`shadow`を継続する。

### 4. 条件合格後のみ — controlled active化（日付制約は消化済み。以後は条件合格のみが要件）

- coreとretryの`TRADER_PORTFOLIO_MODE`を同時に`active`へ変更する。片方だけ変更しない。
- 最初の1週間は毎朝、ダイジェストの建玉、`docs/portfolio_latest.json`、DB `signals.target_weight`を照合する。
- gate fail、snapshot欠損、CS version不一致時にtarget weightが反映されず、安全に縮退することを確認する。
- 最初の週次レビューは2026-08-29を目安とする。異常時は`shadow`へ戻す。

## 継続中のP0制約

### Phase 2 active化を禁止する

同一basisのTOPIX open系列は`topix_open`として実装・本番反映済みであり、`ir_unavailable_same_basis`はもはやactive化を禁止する理由ではない。代わりに公式artifactで測れるようになった実数値が、次の2件でgate不合格を出している。

- `information_ratio=-0.3905` → gate `ir<0.00`（同一basis benchmark比でマイナス）
- `turnover=0.9205` → gate `turnover>0.40`
- 併せて`cs_ic_vs_phase1=-0.2404`（Phase 2のCS ICがPhase 1を下回る）

`portfolio_backtest.json`が現行v2、net-vs-net、benchmark完全coverage、明示的gate合格、当日snapshotと同じCS `model_version`を満たす場合だけactive可とする。上記が解消するまでは`TRADER_PORTFOLIO_MODE=shadow`を維持する。

### TOPIX同一basis benchmarkの決定記録（2026-07-26 決定、2026-08-01 本番反映）

将来の再検討時に同じ議論を繰り返さないための記録。

- **採用**: ベンチマークの原資産は既存の`topix`終値列と同一のTOPIX連動ETF `1305.T`。始値と終値が同一銘柄・同一調整係数・同一応答から得られ、基準が原理的にズレない。1305は寄付きで実際に買えるため、コスト控除後の比較対象として妥当。
- **不採用: TOPIX指数`^TPX`** — Yahooで空（2026-07-26再確認）。
- **不採用: `1306.T`** — Yahooが未調整の10:1不連続を残す既知問題。
- **不採用: TOPIX OHLCを別ソースから取得** — TOPIXの定義が2つになり、マクロ特徴量との整合も別途必要になる。
- **不採用: ベンチマーク専用parquetを別に持つ** — 同一銘柄のデータが2箇所に分かれ、取得タイミング差で始値と終値の日付が食い違いうる。回避したい基準ズレを自ら作り込む。
- **不採用: TOPIX終値同士のリターンで代用** — 戦略と比較条件が変わり、IRの意味が壊れる。
- **スコープ外として意図的に据え置いた**: Phase 1特徴量（`topix_open`はモデルが読まない生データ列。artifact schema上げと再学習を回避）、DBスキーマ（`macro_snapshots`と`latest_snapshot_row()`は無変更）、既存`topix`終値の前日埋め挙動。決済側の`benchmark_ret`はここで据え置いた項目だったが、2026-08-10に実装済み（下記）。

列の契約（前日埋めしない、非有限・非正は当該日付のみNaN、不連続や始値終値の基準ズレは始値列のみ破棄）は`05_cross_cutting.md`が正典。

### 決済側同一basis benchmarkの決定記録（2026-08-10 実装）

上の決定記録で据え置いた`scripts/settle_outcomes.py`の`benchmark_ret`を実装した際の判断。再検討時に同じ議論と同じ事故を繰り返さないための記録。

- **`execution.BENCHMARK_BASIS`（`unavailable_same_basis`）は変更しない**。この定数は`execution_contract_metadata()`経由で`model_store.build_phase1_gate_contract()`の`gate_contract_sha256`に入る。実測で、値を変えると現行active modelのハッシュが`0f5300ca…e2f0`から`bbb3f965…c218ac`へ動き、`compare_phase1_gate_contract`の厳密一致比較が落ちることを確認した。その結果は**翌営業日から全50銘柄がephemeral candidate（特徴量もしきい値も異なる別モデル）へ縮退**し、次の週次再学習まで最大6営業日continueする。さらに`drift_check.py`は`available:false / active_model_incompatible`になるがexit 0のため、watchdogのIssueも出ずに監視が静かに止まる。
- **代わりに`SAME_BASIS_BENCHMARK`を追加し、実際にbenchmarkを算出できた利用側が自分の出力dictの`benchmark_basis`だけ上書きする**。`src/portfolio_backtest.py`が既に採用していたパターンで、ハッシュ対象は不変のまま。現在の利用側は`src/performance.py`と`scripts/settle_outcomes.py`。
- **不採用: ゲート契約のハッシュ対象から`benchmark_basis`を除外する** — フィールドを除いてもハッシュは同じく変わるため、縮退の問題は解決しない。
- **不採用: 定数変更と同時に週次再学習を強制実行する** — 再学習の成否に日次シグナルの正常性が依存し、失敗時は同じ全銘柄縮退に落ちる。運用手順で守る種類のリスクではない。
- 回帰の検出方法: `model_store.build_phase1_gate_contract()`を`data/models/active_model.json`の保存値と突き合わせ、`gate_contract_sha256`の一致を確認する。`execution_contract_metadata()`が返す内容を変更する変更では必ず実行する。

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
