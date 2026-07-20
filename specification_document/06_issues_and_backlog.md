# 既知の課題・運用チェックリスト・バックログ

更新日: 2026-07-20 JST

この文書は「現時点で直っていないこと」と「対応方針」を扱います。各項目は**かみくだき説明**を先頭に置きます。解決済みの修正履歴は git log を参照してください。

## 要対応

### P0運用移行 — execution contract v2を本番DBへ反映する

**かみくだき**: コードは「判断日の次の営業日の寄付きで入り、H営業日目の終値で出る」契約へ直ったが、本番DBの列追加と過去結果の再計算は自動では完了しない。この2作業が終わるまで、旧`close_to_close_v1`の成績と新`next_session_open_to_close_v2`の成績を混ぜて判断してはいけない。

実施順:

1. `uv run python scripts/db_migrate.py`で`migrations/0004_execution_contract.sql`を本番Neonへ適用する。
2. `uv run python scripts/settle_outcomes.py --restate-execution-contract`を一度実行する。
3. `signal_outcomes.contract_version`、entry/exit basis、1/5/10営業日の件数、`realized_ret`欠損を確認する。
4. 旧新の成績差、観測数、再集計不能行を監査記録へ残す。通常settlementもlegacy行を段階的にv2へ置換するが、一括移行の代わりにはしない。

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

## 解決済み（直近）

### ✅ DB 書き込み停止インシデント（2026-06-10〜、2026-07-13 復旧確認・解決）

**かみくだき**: 6/10 のユニバース拡大以降、成績記録（DB）への書き込みが毎日失敗して
`data/outbox/` に溜まり続けていた（約1ヶ月・34ファイル）。原因は2段構え:
(a) キュレーションが銘柄を増やしても DB の銘柄マスタは手動シードのみで追随せず
外部キー違反、(b) outbox 再送が「全件一括・1件失敗で全部やり直し」だったため、
不良イベント1件が再送全体を永遠にブロック。watchdog は docs の鮮度しか見ておらず
3週間以上グリーンのままだった。

対応済み（2026-07-09）:

- `src/db.py`: 書き込み前に FK 親（tickers / model_registry スタブ行）を自動確保、
  outbox 再送を SAVEPOINT による1件ずつ適用+失敗イベントの `data/outbox/dead/` 隔離に変更
- `scripts/workflow_watchdog.py`: outbox 滞留（5日超のファイル / 10ファイル超）と
  dead letter、および日次ループ内で HOLD に縮退した銘柄処理失敗を failure として検知（Issue 起票）
- `scripts/db_migrate.py` / `main.py`: `LEGACY_MODEL_VERSION` の参照先を
  `src.db_records` に統一して AttributeError を修正
- 銘柄マスタは manual-db-migrate 再実行でシード済み

**復旧確認（2026-07-13）**: PR #3 マージ（2026-07-09）後、2026-07-10 の preopen core 実行で
34ファイルの outbox backlog が全量再送・削除され（commit `4b549035`）、`data/outbox/dead/` は
一度も生成されず（= 全件正常再送）、`docs/signal_outcomes_recent.json`（2026-07-13 生成）に
停止期間だった 6/16〜7/2 エントリーの 5日決済が 27件、当時の
`close_to_close_v1`契約では`realized_ret`/`benchmark_ret`/`excess_ret`欠損なしで復帰した。
これはDB復旧の履歴であり、現在のv2契約の同一basis benchmarkが利用可能という意味ではない。
既存行は上記P0移行でv2へ再集計する。

## 対応しない（方針）

### 週次レポートの品質検証は実装しない

AI が書く `reports/weekly_*.md` は内容チェックなしで URL が LINE 通知されますが、シグナルや売買判断には一切影響しないため、リスクは「レポートの読み味」だけです。品質はこだわらない方針。

## 低優先・観察

| 項目                        | 内容                                                                                                                                                                                                                                     |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| CS 較正の粗さ               | shadow の建玉で複数銘柄の `expected_ret`/`prob_up` が同値（score-bucket 較正の粒度）。gross も低め（0.24 前後）。バグではなく shadow 期間の観察対象。改善候補: isotonic 連続化                                                           |
| `8766.JP` の旧履歴異常      | Yahoo の調整済み系列にも 2005 年の株式分割級の不連続（最大 17295.3%）が残る。現行検証は警告して処理継続するため日次停止にはならないが、次回は学習用履歴の開始日カットまたは corporate action 補正を検討する             |
| `generated_at` の TZ 不統一 | 監査系 7 スクリプトが timezone naive（キュレーション系は `+09:00` 付き）。実害は小さい                                                                                                                                                   |
| `usdjpy` の行数が少ない     | 系列の歴史差によるもので異常ではない（参考情報）                                                                                                                                                                                         |
| 月次監査・stress testのモデル同一性 | `monthly_audit.py` / `stress_test.py`は独立`evaluate_kpi_gate()`シミュレーションで、配備Phase 1 exact candidateの保証ではない。日次action・active化は制御しない。将来はschema v3 `gate_evidence`を再評価する監査へ寄せる |

## 運用チェックリスト（時限・要人間判断）

- [x] ~~**DB 復旧確認（次の営業日朝）**: preopen core 実行後に `data/outbox/*.jsonl` が消えて
  いること（= backlog 全量再送成功）、`docs/signal_outcomes_recent.json` に 6/16 以降の
  エントリーが決済され始めること、`data/outbox/dead/` が空か極少であることを確認。
  dead letter があれば中身を見て、修正後に outbox 直下へ戻して再送するか削除する~~
  → **2026-07-13 確認済み**: outbox は 2026-07-10 実行（commit `4b549035`）で 34ファイル全量再送・
  削除、dead letter は生成なし、6/16〜7/2 エントリーの決済が復帰（27件・欠損なし）
- [x] **旧v1時点のshadow評価仕切り直し**: DB 停止期間（6/16〜7/9）は Phase 1 vs Phase 2 比較の
  計測データが欠けている。`portfolio_shadow_report.py` は欠損期間を除いた paired 日だけを
  `active_readiness.shadow_days` に数え、両 Phase の指標も同じ日・銘柄母集団で比較するよう
  修正済み。2026-07-19 再集計では paired 12日 / 33銘柄、CS IC 差 +0.0329 だが、portfolio
  gate は IR -2.17・turnover 0.43 で不合格だった。この判断は履歴として残すが、v2移行後の
  active判定へ流用しない
- [x] ~~**2026-06-24 目安**（shadow 開始 2026-06-10 から 10 営業日）: `active_readiness` を見て
  active 化を判断~~ → 2026-07-09 判断: **見送り**（gate 不合格・IC 大差負け・計測欠損）。
  v2移行後も見送りを維持し、TOPIX同一basis benchmarkがない間は切替不可
- [ ] 本番DBへmigration 0004を適用し、v2再集計と件数・差分監査を完了する
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
