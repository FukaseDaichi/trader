# 06 アーカイブ — 完了した計画・過去地点・決定記録の詳細

更新日: 2026-08-27 JST

[06_issues_and_backlog.md](../06_issues_and_backlog.md) を軽く保つため、**完了した計画**・**過去の観測地点**・**決定記録の詳細（採用理由と不採用案の全文）**をここへ移す。現役の課題・ゲート状況・バックログは 06 が唯一の一覧。決定記録の「今も守るべき要点」は 06 の「継続中のP0制約」に残してあり、本ファイルは再検討時に同じ議論を繰り返さないための全文記録。

## 前回地点（2026-08-02、参考）

| 項目 | 状態 | 判断 |
| --- | --- | --- |
| execution contract v2 | 本番DB移行・再集計・監査完了 | 完了 |
| Phase 1 schema v3 | 50/50銘柄を学習し、manifest・checksum・runtime契約・DB registryを検証してactive化済み | 完了 |
| Phase 1個別KPI gate | independent cohort gate（metrics schema v3）をremoteへ反映済み。2026-08-01の週次再学習が`per-ticker-v1-20260801T090258-76bcfb375e42-9b960e8b`を生成。`data/outbox/`はディレクトリ自体が無く、registry eventの滞留なし。本番2026-07-31時点で50銘柄中7銘柄がgate通過、actionable 7件（MILD_BUY 6・MILD_SELL 1） | コード・artifact・観測は本番稼働。残るのは5営業日観測の完了（2026-08-03、08-04）のみ |
| drift | 50銘柄すべて`warning`（実績サンプル不足）。`breached=false` | 調査完了（2026-08-05）: 旧実装はactive `model_version`完全一致でoutcomeを数えており、週次バージョン更新×非HOLDのみ決済×5営業日ラグの掛け算で`n_outcomes`が構造的に常時0（全履歴で0を確認）。model lineage（`model_registry.kind`）でプールする方式へ修正済み（`db.fetch_prediction_outcomes_for_kind`、`drift_report.outcome_scope`で判別可）。修正後は46 outcomes/16銘柄を観測。ただし非HOLDシグナルが少ないため`min_outcomes=30`/銘柄への到達はなお時間を要する。今後サンプルは単調増加するので、増えない場合のみ再調査 |
| Phase 2レポート | 公式`docs/portfolio_backtest.json`が2026-08-01に`cs-v1-20260801`で再生成され、`benchmark_coverage.coverage_ratio=1.0`（35/35期間）。`information_ratio=-0.3905`、`alpha=0.0364`、`beta=0.3846`、`tracking_error=0.1210`、`turnover=0.9205`、`gate.failures=["ir<0.00","turnover>0.40"]` | `ir_unavailable_same_basis`は解消。公式artifactで初めてIRが測れた結果、**IRは負**。Phase 2を育てるか畳むかの判断材料が揃った |
| Phase 2 shadow report | 2026-08-01時点で`shadow_days=19`、`n_paired_dates=19`、`n_paired_records=54`、`cs_ic_vs_phase1=-0.2404`、`active_ready=false` | 維持する |
| TOPIX benchmark | `topix_open`をマクロパネルに実装済み・本番反映済み | active化のP0制約から除外。以後は評価対象の指標として扱う |
| 実行モード | core/retry workflowとも`TRADER_PORTFOLIO_MODE=shadow`を明示 | 維持する |

## 完了した実施計画

### Phase 1 independent cohort gateの本番観測（2026-07-29〜08-04、完了）

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

### 4週間shadow監視（2026-07-27〜08-21、完了）

週次確認日は2026-08-01、08-08、08-15、08-22とし、全4回を実施した。週次の観測観点表は継続利用するため 06 の「週次レビュー（継続中）」に残した。2026-08-22の4週目観測を経て、2026-08-24に最初の総合判定（結果: shadow継続）を実施した。

## 決定記録の全文

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

上の決定記録で据え置いた`scripts/settle_outcomes.py`の`benchmark_ret`を実装した際の判断。再検討時に同じ議論と同じ事故を繰り返さないための記録。**今も守るべき要点は 06 の「継続中のP0制約」に転記済み。**

- **`execution.BENCHMARK_BASIS`（`unavailable_same_basis`）は変更しない**。この定数は`execution_contract_metadata()`経由で`model_store.build_phase1_gate_contract()`の`gate_contract_sha256`に入る。実測で、値を変えると現行active modelのハッシュが`0f5300ca…e2f0`から`bbb3f965…c218ac`へ動き、`compare_phase1_gate_contract`の厳密一致比較が落ちることを確認した。その結果は**翌営業日から全50銘柄がephemeral candidate（特徴量もしきい値も異なる別モデル）へ縮退**し、次の週次再学習まで最大6営業日continueする。さらに`drift_check.py`は`available:false / active_model_incompatible`になるがexit 0のため、watchdogのIssueも出ずに監視が静かに止まる。
- **代わりに`SAME_BASIS_BENCHMARK`を追加し、実際にbenchmarkを算出できた利用側が自分の出力dictの`benchmark_basis`だけ上書きする**。`src/portfolio_backtest.py`が既に採用していたパターンで、ハッシュ対象は不変のまま。現在の利用側は`src/performance.py`と`scripts/settle_outcomes.py`。
- **不採用: ゲート契約のハッシュ対象から`benchmark_basis`を除外する** — フィールドを除いてもハッシュは同じく変わるため、縮退の問題は解決しない。
- **不採用: 定数変更と同時に週次再学習を強制実行する** — 再学習の成否に日次シグナルの正常性が依存し、失敗時は同じ全銘柄縮退に落ちる。運用手順で守る種類のリスクではない。
- 回帰の検出方法: `model_store.build_phase1_gate_contract()`を`data/models/active_model.json`の保存値と突き合わせ、`gate_contract_sha256`の一致を確認する。`execution_contract_metadata()`が返す内容を変更する変更では必ず実行する。
