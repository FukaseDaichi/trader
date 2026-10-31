# 既知の課題・運用計画・バックログ

更新日: 2026-10-25 JST

この文書は「現時点で直っていないこと」「いつ対応するか」「次へ進める条件」「今後実装する予定のもの」を扱う唯一の一覧です。解決済みの修正履歴はgit log、完了した計画・過去の観測地点・決定記録の全文（採用理由と不採用案）は[archive/06_issues_archive.md](archive/06_issues_archive.md)を参照してください。日付は最短の目安であり、条件未達なら延期します。

## 結論

- **2026-10-19 判断（判定者: 人間、記録者: agent）**: 2026-08-24の「`cs_ic_vs_phase1`の悪化が続けば畳む」方針を**取り下げ**。条件は成立しなかった（直近4週の`cs_ic_vs_phase1`は+0.30〜+0.51）。shadow継続は変わらない。portfolio KPI gateは`turnover>0.40`で不合格、`active_ready=false`。
- **Phase 2は観察継続（畳まない・作り直さない）**: 判断根拠は「Phase 2の継続判断」を参照。次は唯一の安定した不合格要因であるturnoverの再校正に着手できる。
- `TRADER_PORTFOLIO_MODE=shadow`を維持する。active化は全条件合格＋人間の明示承認があった場合のみ。

2026-07-19以前のportfolio/shadow指標はv2/schema v3移行前の履歴であり、以降のactive判断には使用しない。

## 現在地（2026-10-19）

2026-10-17の週次再学習（`cs-v1-20261017`）と2026-10-19の日次運用まで反映した最新値。

| 項目 | 状態 | 判断 |
| --- | --- | --- |
| Phase 2公式backtest | `cs-v1-20261017`: IR **+0.0017**、turnover **0.6908**、Sharpe 1.29、max DD -5.3%、alpha +6.7%、beta 0.44、benchmark coverage 1.0。gate failures=`["turnover>0.40"]` | gate不合格。IRは週ごとに±0.8振れるため単週では判断しない |
| Phase 2 shadow report | 2026-10-17時点で`shadow_days=31`、`n_paired_dates=31`、`n_paired_records=120`、`cs_ic_vs_phase1=**+0.4104**`、`active_ready=false`（reason: `portfolio_gate not passed`） | shadow維持 |
| Phase 1 gate | 2026-10-16は50銘柄中6銘柄が通過、actionable 3件（MILD_BUY）。2026-10-19は1銘柄通過、全HOLD | ゲートは仕様どおり機能 |
| drift | 2026-10-20〜22に`6098.JP`だけがbreach（IC −0.28、`n_outcomes`=30〜32）し、Issue #27〜#29が毎日起票された。stale-data再実行で同じ取引のoutcomeが二重計上され（`6098.JP`は直近29件中4組が重複）、`min_outcomes=30`へ水増しで到達していた。2026-10-22に`(ticker, entry_date)`単位の重複排除とIssueの集約を実装。残り49銘柄は`insufficient_sample`、PSI警告（47銘柄）はダッシュボード表示のみ。2026-10-30に重複排除後の`n_outcomes`=30で`6098.JP`が再breach（IC −0.29、Brier 0.26、`psi_worst`=`ma_20_60_cross` 0.59、Issue #46）。集計・重複排除は仕様どおりでコード起因ではない。ただし中身は「確信度の順位が逆」で損失ではない: 直近の重複除外27取引は勝ち19・平均超過リターン+2.3%、負けは確信度の高かった8月末〜9月中旬に集中。n=30のICは標準誤差が約0.18あり、−0.29は統計的に有意とは言えない。参考値`all_predictions`（HOLD込み48件）でもIC −0.17。なお`all_predictions`では十分標本46銘柄中28銘柄が閾値超過、銘柄別ICの中央値は+0.02（同日週次再学習のCV中央値0.13と乖離） | 実際の劣化として記録した。drift breachはKPIゲートに入らないため`6098.JP`のシグナルは引き続きゲート判定のみで出る。銘柄の除外やbreach標本の`all_predictions`への切替は人間が判断する（下の「全銘柄比較を判定の入力へ切り替えるか」と同じ判断） |
| 日次運用 | 2026-10-19のCoreがstooq全断で`timeout-minutes: 30`に達してキャンセル。Retryが救済し当日シグナルは出力済み。対策のStooqサーキットブレーカーはPR #25で2026-10-19にマージ済み | 2026-10-20以降のCoreで効果を確認 |

## 週次レビュー（継続中）

4週間shadow監視（2026-07-27〜08-21、完了・詳細はarchive）から継続する週次確認。毎週土曜の週次再学習後に、同じ観点で記録する。

| 観点 | 合格方向 | 即時停止条件 |
| --- | --- | --- |
| Phase 1 artifact | schema v3、全coverage、runtime互換、registry一致 | checksum/manifest/契約不一致 |
| Phase 1 gate | 通過銘柄とactionable観測が増え、失敗理由を説明できる | 0/50固定で観測が増えない |
| drift/reliability | outcome数が増え、sourceとversion provenanceが明確 | fallback急増、十分なサンプルで閾値breach |
| Phase 2 provenance | v2、exact CS version、paired coverageが増える | 旧契約・別version・欠損補完の混入 |
| portfolio KPI | gate合格、IR・DD・Sharpe・turnoverが有限で再現可能 | benchmark coverage不足、必須指標NULL、gate不合格 |
| 運用健全性 | DB/outbox/dead letter、ダイジェスト、dashboardが整合 | 書き込み停止、鮮度低下、通知とDBの不一致 |

休場・HOLD・データ欠損で観測が増えなかった週は、カレンダーだけ進めてもactive判断の証拠に数えない。

### 週次記録（2026-08-29〜10-17、2026-10-19 にまとめて記録）

08-24 以降の週次レビューは記録が止まっていたため、各週の Weekly retrain refresh コミット時点の公式 artifact（`docs/portfolio_backtest.json`・`docs/portfolio_shadow_report.json`・`docs/state.json`）から再構成した。Phase 1 gate は各週最終営業日のシグナルで数えた。

| 週 | CS version | IR | turnover | Sharpe | max DD | `cs_ic_vs_phase1` | paired dates / records | Phase 1 gate 通過 | gate failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 08-29 | `cs-v1-20260829` | -0.615 | 0.928 | 0.93 | -4.7% | +0.024 | 25 / 106 | 5/50 | `ir<0.00`, `turnover>0.40` |
| 09-05 | `cs-v1-20260905` | -0.620 | 0.838 | 1.48 | -3.2% | -0.084 | 24 / 104 | 9/50 | `ir<0.00`, `turnover>0.40` |
| 09-12 | `cs-v1-20260912` | -0.697 | 0.947 | 0.97 | -4.6% | +0.041 | 28 / 124 | 7/50 | `ir<0.00`, `turnover>0.40` |
| 09-19 | `cs-v1-20260919` | -0.839 | 0.944 | 0.85 | -6.2% | +0.081 | 30 / 136 | 5/50 | `ir<0.00`, `turnover>0.40` |
| 09-26 | `cs-v1-20260926` | +0.799 | 0.863 | -0.03 | -6.7% | +0.079 | 32 / 143 | 4/50 | `sharpe<0.30`, `turnover>0.40` |
| 10-03 | `cs-v1-20261003` | +0.574 | 0.680 | -0.23 | -7.0% | +0.302 | 33 / 135 | 4/50 | `sharpe<0.30`, `turnover>0.40` |
| 10-10 | `cs-v1-20261010` | +0.351 | 0.698 | -0.66 | -9.7% | +0.509 | 33 / 131 | 7/50 | `sharpe<0.30`, `turnover>0.40` |
| 10-17 | `cs-v1-20261017` | +0.002 | 0.691 | 1.29 | -5.3% | +0.410 | 31 / 120 | 6/50 | `turnover>0.40` |

benchmark coverage は全週 1.0。

読み取り:

- `cs_ic_vs_phase1` は 08-22 の -0.2825 から 7/8 週で正、直近4週は +0.30〜+0.51。08-24 判断の「悪化継続なら畳む」条件は成立しなかった。
- IR は週ごとに ±0.8 振れ、09-26 以降の正転も Sharpe が負の週（戦略も負けたが、ベンチマークがより大きく下げた局面）と重なる。単週の IR は合否の根拠にしない。
- turnover は 0.99 → 0.69 と下がったが全週で上限 0.40 を超え、唯一の安定した gate 不合格要因。
- paired dates は 24〜33、records は 104〜143 で推移し、8週で単調には増えていない。

### active化の総合判定チェックリスト

**最初の総合判定（2026-08-22予定 → 2026-08-24実施）: shadow継続。** gate合格・turnover再校正・`active_ready=true`が未達（portfolio gate failures=`["ir<0.00","turnover>0.40"]`、`cs_ic_vs_phase1=-0.2825`）。

**2026-10-19 再判定: shadow継続。** gate合格・turnover再校正・`active_ready=true`がなお未達（gate failures=`["turnover>0.40"]`、`cs_ic_vs_phase1=+0.4104`）。次回判定は週次レビューの一環として継続する。

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

公式`docs/portfolio_backtest.json`のportfolio KPI gateが不合格（2026-10-17時点は`turnover>0.40`。IR・Sharpeも週によって不合格になる。最新の実測値は「現在地」を参照）。`portfolio_backtest.json`が現行v2、strategy net対benchmark net、benchmark完全coverage、明示的gate合格、当日snapshotと同じCS `model_version`を満たす場合だけactive可とする。解消するまでは`TRADER_PORTFOLIO_MODE=shadow`を維持する。

### `execution.BENCHMARK_BASIS`を変更しない（2026-08-10 決定の要点）

- この定数（`unavailable_same_basis`）は`execution_contract_metadata()`経由で`gate_contract_sha256`に入る。値を変えると現行active modelの契約ハッシュが動き、**翌営業日から全50銘柄がephemeral candidateへ縮退**する。しかも`drift_check.py`はexit 0のまま監視が静かに止まる。
- 同一basis benchmarkを実際に算出できた利用側は、`SAME_BASIS_BENCHMARK`で自分の出力dictの`benchmark_basis`だけを上書きする（採用済み: `src/portfolio_backtest.py`・`src/performance.py`・`scripts/settle_outcomes.py`）。
- `execution_contract_metadata()`が返す内容を変える変更では、`model_store.build_phase1_gate_contract()`を`data/models/active_model.json`の保存値と突き合わせ、`gate_contract_sha256`の一致を必ず確認する。
- ベンチマーク原資産の採用判断（`1305.T`）と不採用案の全記録は[archive/06_issues_archive.md](archive/06_issues_archive.md)。列の契約は`05_cross_cutting.md`が正典。

### Phase 2の継続判断

TOPIX open実装の目的は「active化の実現」ではなく「IRという評価軸を初めて測定可能にすること」だった。2026-08時点の公式artifactではIRが負で、Phase 2を縮小・shadow-onlyに畳む根拠になりうるとされた。その後IRは週ごとに大きく振れながら、2026-09-26以降は正になっている。

**判断記録（2026-10-19、人間が決定、記録者: agent）: 「畳む」方針を取り下げ。観察継続（作り直さない）。**

- 根拠: 2026-08-24の条件「`cs_ic_vs_phase1`の悪化が続く場合はshadow-only縮小へ傾ける」は成立しなかった。-0.2825（08-22）以降の8週中7週で正、直近4週は+0.30〜+0.51（週次記録の表を参照）。
- 留保: IRは週ごとに±0.8振れ（09-19 -0.84 → 09-26 +0.80 → 10-17 +0.002）、正転した週の多くはSharpeが負だった。IRの改善はまだ合格の根拠にならない。
- 次: 全週で不合格が続く唯一の要因であるturnover（0.99 → 0.69、上限0.40）の再校正に着手する（バックログ参照）。学習設計の作り直し（育てる）は引き続き、原因分析を先に行ってからでないと着手しない。
- 前回の判断記録（2026-08-24「観察継続、悪化継続なら畳む方向」）の全文は[archive/06_issues_archive.md](archive/06_issues_archive.md)。

## 対応しない（方針）

### 週次レポートの品質検証は実装しない

AIが書く`reports/weekly_*.md`は内容チェックなしでURLがLINE通知されますが、シグナルや売買判断には影響しないため、リスクはレポートの読み味だけです。品質はこだわらない方針です。

## 今後の実装予定（統合バックログ）

未実装・未着手のものはすべてここに集約します。個別の実装計画ドキュメントは作らず、着手が決まった時点で`plans/YYYY-MM-DD-<topic>.md`を作成し、完了したら削除してこの表へ戻します。

### 着手条件が既に揃っているもの

| 項目 | 内容 | 備考 |
| --- | --- | --- |
| `TRADER_PORTFOLIO_BACKTEST_MAX_TURNOVER`の再校正 | 現行`0.40`に対し実測`0.6908`（2026-10-17、08-22は0.9919）。v2 shadow分布と同一basis benchmarkが揃ったので、根拠付きで再設定する | active総合判定の必須項目。数値合わせのために緩めない。2026-10-19判断で「畳む」方針を取り下げたため着手可。ただし2026-10-25に shadow 比較の標本が1日約3銘柄と判明したため、`full_universe` の結果を先に確認してから着手する |

### 観察中（条件が揃ったら判断）

| 項目 | いつ判断するか | 内容 |
| --- | --- | --- |
| 全銘柄比較を判定の入力へ切り替えるか | 最低2回の週次レビュー（migration 0005 は2026-10-25に`manual-db-migrate.yml`で適用済み。初回の直近90日 backfill は2026-10-26の Core）で既存値と `full_universe` / drift `all_predictions` を並べて記録した後 | `signal_outcomes` は actionable しか決済しないため、shadow 比較（`cs_ic_vs_phase1`・`shadow_days`）と drift は1日約3銘柄の偏った標本で測られていた（2026-10-24: `n_paired_records=99`／30日、`top_n=8` を下回るため top-N の delta は構造的に0、drift は50/50が `insufficient_sample`）。評価専用台帳 `market_outcomes` を追加し、参考値として併記している。`active_readiness` の入力、および drift breach の標本と閾値（`min_ic`・`max_brier`）を切り替えるかは人間が判断する。判定基準の変更なので決定記録を残す。HOLD を `signal_outcomes` に混ぜる案は、成績KPIの全読み手へフィルタが必要で漏れると静かに数値が変わるため不採用 |
| `cs_ic_vs_phase1`の持続性 | 週次レビューごと | 2026-09-12以降は6週連続で正（直近+0.41）。再び負へ戻り数週続く場合は、原因分析を先に行ってから特徴量・ラベル・学習設計を見直す |
| CS較正の粗さ | 次回shadowレビュー | 同値scoreや低grossが成績・分散を実際に阻害した場合だけ、isotonic連続化を検討する |
| `8766.JP`の旧履歴異常 | 学習失敗やdrift breachになれば即時（2026-08-24再確認済み: 学習正常・breachなしで非該当） | 2005年の株式分割級不連続が残る。学習開始日カットまたはcorporate action補正を検討する |
| `usdjpy`の行数が少ない | macro鮮度警告が出た時だけ | 系列の歴史差によるもので、現時点では異常ではない |
| `docs/tickers` 総量の残り余裕 | 有効銘柄が65前後に増えた時、または watchdog の `ticker_payloads_too_large` が再発した時 | `threshold_optimization` 除外後は 7.41MB （guard 10MB）。内訳は `data` 4.93MB / signals 2.38MB で、主因は 500行の価格系列（`MAX_DASHBOARD_ROWS`）に移った。約17銘柄ぶんの余裕しかないため、次は行数削減か数値精度の丸めを検討する |
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
