# 既知の課題・運用チェックリスト・バックログ

更新日: 2026-07-13 JST

この文書は「現時点で直っていないこと」と「対応方針」を扱います。各項目は**かみくだき説明**を先頭に置きます。解決済みの修正履歴は git log を参照してください。

## 要対応

（現在、要対応の項目はありません）

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
  dead letter を failure として検知（Issue 起票）
- `scripts/db_migrate.py`: `LEGACY_MODEL_VERSION` の AttributeError 修正
- 銘柄マスタは manual-db-migrate 再実行でシード済み

**復旧確認（2026-07-13）**: PR #3 マージ（2026-07-09）後、2026-07-10 の preopen core 実行で
34ファイルの outbox backlog が全量再送・削除され（commit `4b549035`）、`data/outbox/dead/` は
一度も生成されず（= 全件正常再送）、`docs/signal_outcomes_recent.json`（2026-07-13 生成）に
停止期間だった 6/16〜7/2 エントリーの 5日決済が 27件、`realized_ret`/`benchmark_ret`/
`excess_ret` 欠損なしで復帰した。performance_detail の equity curve も更新済み。
**残る follow-up は shadow 評価の仕切り直し（下記チェックリスト）のみ**。

## 対応しない（方針）

### 週次レポートの品質検証は実装しない

AI が書く `reports/weekly_*.md` は内容チェックなしで URL が LINE 通知されますが、シグナルや売買判断には一切影響しないため、リスクは「レポートの読み味」だけです。品質はこだわらない方針。

## 低優先・観察

| 項目                        | 内容                                                                                                                                                                                                                                     |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| リトライ実行日の計測欠け    | 06:20/06:40 の retry workflow は env が最小構成（DB・ポートフォリオ・マクロ更新・決済なし）。core が失敗し retry で救済された日は、シグナルは出るが DB 台帳・建玉 snapshot が欠ける。頻発するようなら core と同じ env/後続ステップを足す |
| CS 較正の粗さ               | shadow の建玉で複数銘柄の `expected_ret`/`prob_up` が同値（score-bucket 較正の粒度）。gross も低め（0.24 前後）。バグではなく shadow 期間の観察対象。改善候補: isotonic 連続化                                                           |
| `generated_at` の TZ 不統一 | 監査系 7 スクリプトが timezone naive（キュレーション系は `+09:00` 付き）。実害は小さい                                                                                                                                                   |
| `usdjpy` の行数が少ない     | 系列の歴史差によるもので異常ではない（参考情報）                                                                                                                                                                                         |

## 運用チェックリスト（時限・要人間判断）

- [x] ~~**DB 復旧確認（次の営業日朝）**: preopen core 実行後に `data/outbox/*.jsonl` が消えて
  いること（= backlog 全量再送成功）、`docs/signal_outcomes_recent.json` に 6/16 以降の
  エントリーが決済され始めること、`data/outbox/dead/` が空か極少であることを確認。
  dead letter があれば中身を見て、修正後に outbox 直下へ戻して再送するか削除する~~
  → **2026-07-13 確認済み**: outbox は 2026-07-10 実行（commit `4b549035`）で 34ファイル全量再送・
  削除、dead letter は生成なし、6/16〜7/2 エントリーの決済が復帰（27件・欠損なし）
- [ ] **shadow 評価の仕切り直し**: DB 停止期間（6/16〜7/9）は Phase 1 vs Phase 2 比較の
  計測データが欠けている。復旧後に shadow 日数を実質リセットして `active_readiness` を
  再評価（現状は portfolio gate 不合格 + CS IC が Phase 1 比 -0.25 で明確に NO-GO）
- [x] ~~**2026-06-24 目安**（shadow 開始 2026-06-10 から 10 営業日）: `active_readiness` を見て
  active 化を判断~~ → 2026-07-09 判断: **見送り**（gate 不合格・IC 大差負け・計測欠損）。
  切替手順自体は変わらず `daily-preopen-core.yml` の `TRADER_PORTFOLIO_MODE` 1 行
- [ ] active 化後 1 週間: ダイジェストの建玉と DB `signals.target_weight` の一致を毎朝確認

## Phase 4+ バックログ（未着手の将来案）

- **fills（約定）記録**: 手動約定の入力経路 → `fills` テーブル → 提案 vs 実約定の乖離計測
- 発注指示出力（証券会社 CSV / API、`src/execution.py`。不可逆処理は決定論コード限定の原則を維持）
- `signals.action` のポートフォリオ駆動化の再評価（現状は active でも action はモデル由来のまま）
- `active_readiness` の GitHub Issue 自動起票
- DB 長期アーカイブ自動化（`backtest_equity` の parquet 退避、400MB 警告は既存）と Alembic 導入判断
- ダッシュボードの認証・ユーザー管理
