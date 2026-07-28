# Phase 1 KPI gate サンプル充足性改善 実装・ロールアウト計画

作成日: 2026-07-29 JST  
ステータス: コード実装・ローカル検証完了、本番artifact更新は未実施  
関連: `specification_document/06_issues_and_backlog.md`「Phase 1個別KPI gate」

## 1. 結論

旧方式との常時並走機能は作らず、5日保有戦略に合った
`independent_signal_cohorts`を直接実装した。現在の50銘柄を新方式で
ローカル再学習した結果、旧方式の0/50から7/50銘柄がKPI gateを通過した。

最新行に対する予測では、通過7銘柄のうち6銘柄がactionableだった。

| action | 件数 |
| --- | ---: |
| BUY | 1 |
| MILD_BUY | 4 |
| MILD_SELL | 1 |
| HOLD | 1 |

全銘柄を無条件に通してはいない。43銘柄はサンプル不足または収益・リスク条件で
引き続き不合格になったため、KPI安全弁は機能している。

## 2. 原因

旧gateは次の二重ロックになっていた。

- threshold tuning約120営業日で`round_trips >= 8`
- 最終holdout 60営業日で`round_trips >= 10`

`round_trips`は、連続した同方向の集約建玉を完全に手仕舞うまで1回と数える。
5日保有のシグナルが毎日重なると、複数のエントリーがあっても集約建玉は
連続し、round tripは1回のままになる。

2026-07-25モデルのholdoutではround tripsが中央値0、最大4だった。
一方、個別のsignal cohortsは最大60あった。損益エピソードの指標を
サンプル充足性に流用したことが、0/50固定の主因だった。

## 3. 実装した設計

### 3.1 independent signal cohorts

`independent_signal_cohorts`はエントリー行を日付順に走査し、直前に採用した
エントリーから`effective_horizon_days`以上離れたものだけを数える。

horizon 5で営業日index 0、1、4、5、10にエントリーがあれば、
0、5、10の3件として数える。

不変条件:

- 同日に複数sleeveがあっても1件とする。
- BUY/MILD_BUYなどの強度差で重複加算しない。
- 暦日差ではなく、market rowの営業日位置を使う。
- market rowが無いテスト用フレームでは行位置へ安全に縮退する。
- round tripsとexpectancyは損益診断用として残す。

### 3.2 新しい最低条件

| 用途 | 新方式 | 旧方式 |
| --- | ---: | ---: |
| threshold tuning | independent cohorts 8件 | round trips 8回 |
| holdout KPI gate | independent cohorts 5件 | round trips 10回 |

60営業日・horizon 5で完全非重複な機会の上限は概ね12件であり、holdoutでは
そのうち5件を要求する。サンプル条件以外のCAGR、平均日次net return、
最大drawdown、Sharpe条件は変更していない。

### 3.3 schemaと契約

- Phase 1 metrics schemaをv2からv3へ更新した。
- metricsに`independent_signal_cohorts`と定義を追加した。
- artifact schema v3、特徴量、ラベル、booster形式は変更していない。
- gate configとgate contractへsample metricと新しい最低値を含めた。
- gate contract hashが変わるため、旧artifactを新gateの証拠として再利用しない。
- metrics v2の旧gate evidenceは現行品質証拠としてfail-closeする。

## 4. 変更ファイル

### 実装

- `src/backtest.py`
  - 独立cohort計数
  - metrics schema v3
  - gateとthreshold optimizerのsample metric切替
- `src/config.py`
  - 新しいsample metricと最低値
- `src/model_store.py`
  - canonical gate contractとmetrics v3検証
- `src/phase1.py`
  - effective horizonをmetrics計算へ伝搬
- `.env.example`
  - 新しい環境変数を記載

### テスト

- `tests/test_backtest_metrics.py`
- `tests/test_model_store.py`
- `tests/test_phase1_artifact_contract.py`
- `tests/test_weekly_model_retrain.py`

## 5. ローカル検証結果

条件:

- 現在の50銘柄
- 現在のparquetとmacro panel
- triple barrier / horizon 5
- tuning 120行、embargo 5行、holdout 60行
- コスト・slippageを含む現行設定
- DB、active pointer、LINE、保存artifactを書き換えないread-only評価

結果:

| 項目 | 結果 |
| --- | ---: |
| 学習成功 | 50/50 |
| threshold最適化成立 | 21/50 |
| KPI gate通過 | 7/50 |
| 最新actionable | 6/50 |

通過銘柄:

| ticker | 銘柄 | holdout independent cohorts | 最新action |
| --- | --- | ---: | --- |
| 6723.JP | ルネサスエレクトロニクス | 6 | HOLD |
| 8035.JP | 東京エレクトロン | 12 | MILD_BUY |
| 4901.JP | 富士フイルムホールディングス | 6 | MILD_SELL |
| 6920.JP | レーザーテック | 12 | BUY |
| 6367.JP | ダイキン工業 | 11 | MILD_BUY |
| 8591.JP | オリックス | 12 | MILD_BUY |
| 8604.JP | 野村ホールディングス | 11 | MILD_BUY |

不合格理由件数:

| 理由 | 件数 |
| --- | ---: |
| `independent_signal_cohorts<5` | 34 |
| `cagr<3.0%` | 40 |
| `avg_daily_net_return<0.010%` | 39 |
| `sharpe<0.20` | 38 |

同じ銘柄が複数理由を持つため合計は50を超える。独立サンプルが十分でも
収益条件で落ちる銘柄があり、件数だけを増やす変更にはなっていない。

## 6. テスト結果

次の対象テストを個別に確認した。

```text
tests/test_backtest_metrics.py             17/17
tests/test_model_store.py                  18/18
tests/test_phase1_artifact_contract.py     15/15
tests/test_weekly_model_retrain.py          4/4
```

その後、`tests/test_*.py`を全件実行し、45ファイルすべて成功した。

## 7. 残るロールアウト作業

コードを本番へ出す前に、同じ変更を含むcommitから新しい週次artifactを生成する。
コードだけを先に出すと旧metrics-v2 artifactが拒否され、日次が50銘柄すべてを
ephemeral再学習するため、コードと新artifactは同じrollout単位で扱う。

- [ ] 一意なversionで全50銘柄を週次再学習する。
- [ ] manifest coverage 50/50、checksum、gate evidenceを検証する。
- [ ] 新artifactとコードを同じ変更セットへ含める。
- [ ] DB registry登録またはoutbox queue成功を確認する。
- [ ] active pointerが新gate contractを指すことを確認する。
- [ ] 本番相当dry runでsaved-model利用50/50、ephemeral fallback 0を確認する。
- [ ] 最初の5営業日はgate通過数、actionable数、settlement、driftを確認する。

Phase 2はshadowのまま維持し、この変更と同時にactive化しない。

## 8. ロールバック

次のいずれかで、旧コードと旧artifact pointerを同じ単位で戻す。

- artifact、manifest、gate contract、registry不一致
- 日次処理停止
- actionable signalが10件超の日が2営業日連続
- gate通過が再び0/50で2営業日連続
- settlement、DB write-through、LINE digestの契約不整合
- 事前評価を大きく超えるdrawdownまたはturnover異常

新gate evidenceを旧設定で再利用せず、旧contractに一致するartifactへ戻す。
