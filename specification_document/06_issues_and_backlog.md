# 既知の課題・運用チェックリスト・バックログ

更新日: 2026-06-11 JST（ソースコード実レビューに基づく）

この文書は「現時点で直っていないこと」だけを扱います。解決済みの修正履歴は git log と本ファイルの履歴を参照してください（旧 `06_priority_matrix.md` の P0/P1 対応済み一覧、および Phase 0〜3 の実装計画は 2026-06-11 に削除済み。`README.md` の運用ルール参照）。

## P1: 計測・収益性に直結する課題

### 1. TOPIX・日経VI・JGB10y のマクロ系列が一度も取得できていない

**事象**: `data/macro/macro_panel.parquet` で `topix` / `nikkei_vi` / `jgb10y` が**全行 NaN**（2026-06-11 確認: topix 0/15570 行、usdjpy・nikkei は取得できている）。

**影響**:

- `scripts/settle_outcomes.py` の TOPIX ベンチマーク決済が常に NULL → `signal_outcomes.benchmark_ret` / `excess_ret` が蓄積されず、`/performance` ページの「資産曲線 vs TOPIX」のベンチマーク曲線と超過リターン指標が機能しない（Phase 3 の主要成果物が実質無効）
- マクロ 11 特徴量のうち TOPIX・日経VI・JGB10y 系が常時欠損 → レジーム特徴の実効性が大幅に低下（欠損許容設計なので停止はしない）

**原因の見立て**: `src/macro.py` の系列シンボル（Stooq: `^tpx` / `^nkvix` / `10jgby.b`、yfinance: `^TPX` / `^NIVI` / なし）が取得元で無効な可能性が高い。

**対応案**:

1. シンボルの再調査（Stooq の実シンボル確認。例: TOPIX 連動 ETF `1306.jp` を代理系列にする選択肢、日経VI・JGB10y は取得不能なら系列を無効化してログを静かにする選択肢）
2. 修正後に `uv run python scripts/update_macro_snapshots.py` → parquet の非NULL を確認 → `uv run python scripts/settle_outcomes.py --as-of <today> --refill-benchmark` で過去分の NULL を埋める（冪等）

### 2. ポートフォリオ KPI ゲートの結果が active 配線に未接続

**事象**: 週次の `evaluate_portfolio_kpi_gate()`（Sharpe/MaxDD/IR/turnover、`TRADER_PORTFOLIO_BACKTEST_*` 閾値）は計算され `docs/cs_model_quality.json` に `gate_passed` として記録されるが、`docs/portfolio_backtest.json`（`write_portfolio_backtest_report`）には gate キーが含まれない。その結果、active 可否を判定する `portfolio.read_portfolio_gate()` は「バックテストが `available: true` なら通過」という弱い判定になっている（コード内に明示コメントあり。`gate.passed` キーがあれば優先する拡張点は予約済み）。

**影響**: KPI ゲート不合格でも、バックテストが完走してさえいれば (a) `merge_target_weights` の active 配線が作動し得る、(b) `portfolio_shadow_report.json` の `active_readiness.portfolio_gate_passed` も true になる（同じ関数を使用）。shadow 運用中は実害なしだが、**active 化判断の前に直すべき**。

**対応案**: `src/portfolio_backtest.py` の `write_portfolio_backtest_report()` に `gate: {passed, failures}` を含める（`weekly_cross_section_retrain.py` は gate 評価済みなので渡すだけ）。`read_portfolio_gate()` 側は変更不要。

### 3. ポートフォリオ構築のレジームが常に "neutral" 固定

**事象**: `main.py` `_run_portfolio_snapshot()` が `regime = "neutral"` をハードコード（コメントで「qualitative regime の loader 未配線」と明示）。`docs/curation/macro_latest.json` の `market_bias` は日次ダイジェスト表示には使われるが、ポートフォリオ構築へは渡らない。

**影響**: `TRADER_PORTFOLIO_RISK_OFF_GROSS_MULT=0.50`（risk_off 時のグロス半減）が本番で一度も作動しない。リスクオフ局面の自動デレバレッジが無効。

**対応案**: `market_bias`（risk_on/neutral/risk_off）を `build_portfolio_snapshot(regime=...)` へ渡す薄い loader を追加（`_build_macro_regime()` の流用で可）。ただし課題1の解消とあわせ、レジーム判定の入力品質を先に確保すること。

## P2: 運用品質

### 4. enabled 51 銘柄 > `settings.curation.max_universe: 50`

ユニバース上限と 1 件不整合（2026-06-11 確認）。curation/universe の次回実行で収束するか監視し、収束しなければ `uv run python scripts/universe_select.py --target-size 50 --apply` で是正。

### 5. 週次レポートの品質検証が未実装

`reports/weekly_*.md` の免責・front matter・銘柄コード実在チェックは agent 手順頼みで、`scripts/curation_notify.py` は検証なしで URL を通知する。通知前の軽量 validator（不合格時は通知スキップ）が望ましい。

### 6. `data/features/*.parquet` が運用効果を持たない

`nightly-feature-precompute.yml` は毎晩生成するが、commit もされず日次処理も読まない。レポート生成以上の効果がないため、workflow 停止か日次パイプラインの入力契約への組み込みかを決める。

### 7. LINE 無料枠（200 push/月）と通知件数

51 銘柄体制で actionable シグナルが増えると、個別通知 + 日次ダイジェスト + 週次系で無料枠を圧迫し得る。`TRADER_NOTIFY_PER_TICKER_ENABLED=false`（ダイジェストのみ運用）への切替が設計済みの緩和策。月の送信数の目安を watchdog 等で可視化できるとなお良い。

## 低優先・観察

| 項目 | 内容 |
|---|---|
| CS 較正の粗さ（旧 I11） | shadow snapshot で複数銘柄の `expected_ret` / `prob_up` が完全同値（score-bucket 較正）。gross も低位（0.24 前後）。バグではなく shadow 期間の観察対象。改善候補: bucket → isotonic 連続化 |
| `generated_at` の TZ 不統一 | 監査系 7 スクリプトが timezone naive の `datetime.now()`（キュレーション系は `+09:00` 付き）。実害は小さいが揃えると比較が楽 |
| `usdjpy` の歴史が短い | パネル 15570 行中 7723 行。系列の歴史差によるもので異常ではない（参考情報） |

## 運用チェックリスト（時限・要人間判断）

- [ ] **2026-06-13（土）**: `weekly-model-retrain.yml` の初回実走を確認。`data/models/active_model.json` と `per-ticker-v1-*` バンドルが commit され、翌営業日から日次が保存モデル推論に切り替わること（現状 `active_model.json` 不在のため、全銘柄が legacy 学習フォールバックで動いている）。`docs/model_quality.json` が `available: true` になることも確認
- [ ] `scripts/backfill_state_signals.py` を本番 DB に対して実行済みか確認（未実行なら 1 回実行。冪等）
- [ ] **2026-06-24 目安（shadow 開始 2026-06-10 から 10 営業日）**: `docs/portfolio_shadow_report.json` の `active_readiness` を確認し、active 化を判断。**ただし課題 2（ゲート未接続）を先に解消すること**。切替は `.github/workflows/daily-preopen-core.yml` の `TRADER_PORTFOLIO_MODE` を `"active"` へ変える 1 行 PR、ロールバックは同じ 1 行を戻すだけ
- [ ] active 化後 1 週間: 日次ダイジェストの建玉と DB `signals.target_weight` の一致を毎朝確認

## Phase 4+ バックログ（未着手の将来案）

- **fills（約定）記録**: 手動約定の入力経路 → `fills` テーブル → 提案 vs 実約定の乖離計測
- 発注指示出力（証券会社 CSV / API、`src/execution.py`。不可逆処理は決定論コード限定の原則を維持）
- `signals.action` のポートフォリオ駆動化の再評価（現状は active でも action はモデル由来のまま）
- `active_readiness` の GitHub Issue 自動起票
- DB 長期アーカイブ自動化（`backtest_equity` の parquet 退避、400MB 警告は既存）と Alembic 導入判断
- ダッシュボードの認証・ユーザー管理
