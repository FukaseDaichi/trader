# 既知の課題・運用チェックリスト・バックログ

更新日: 2026-06-11 JST（#1/#2/#3/#6 を実装対応。調査で発見した #8 を新規追加）

この文書は「現時点で直っていないこと」と「対応方針」を扱います。各課題は先頭に**かみくだき説明**、最後に開発者向けの技術メモを置いています。解決済みの修正履歴は git log を参照してください。

## 一覧（まずここだけ読めば OK）

| #   | 課題                          | ひとことで言うと                                                       | 状態                              |
| --- | ----------------------------- | ---------------------------------------------------------------------- | --------------------------------- |
| 1   | TOPIX 等の指数データが空      | 比較相手のデータが取れていなかった → ETF プロキシで復旧                | ✅ **対応済み（2026-06-11）**     |
| 2   | active 化の安全チェックが甘い | 「テストを受けたか」ではなく「合格点だったか」を見るよう修正           | ✅ **対応済み（2026-06-11）**     |
| 3   | 相場の警戒ブレーキが未配線    | リスクオフ判定がポートフォリオ計算に届くよう配線                       | ✅ **対応済み（2026-06-11）**     |
| 4   | 銘柄数 51 > 上限 50           | 設定上の上限を 1 銘柄だけ超えている                                    | ✅ 対応しない（許容と判断）       |
| 5   | 週次レポートの検品なし        | AI が書くレポートの内容チェックが無い                                  | ✅ 対応しない（品質は重視しない） |
| 6   | 毎晩の事前計算が空回り        | 使われていなかったジョブ一式を削除                                     | ✅ **対応済み（削除 2026-06-11）** |
| 7   | LINE 通知が多すぎる           | 個別通知をやめ、ダイジェスト 1 通に買い/売り銘柄をまとめる             | ✅ **対応済み（2026-06-11）**     |
| 8   | 本番 DB にテーブル不足        | Phase 1 のマイグレーション未適用で `macro_snapshots` への書込が毎日失敗 | 🔴 要対応（人手 1 コマンド）      |

---

## 🔴 要対応

### 8. 本番 DB に Phase 1 スキーマが未適用（`macro_snapshots` が存在しない）

**かみくだき**: マクロ環境の記録を毎日 DB に保存しようとしていますが、**保存先のテーブル自体が本番 DB に作られていません**（毎朝「relation "macro_snapshots" does not exist」で失敗 → 無視して続行、が続いています）。テーブル定義はリポジトリに入っているので、マイグレーションコマンドを 1 回流すだけで直ります。Phase 1 のスキーマ（`0002_phase1_quality_schema.sql`）がまるごと未適用の可能性が高く、その場合 **6/13（土）の週次再学習で `model_registry` への版登録も失敗**します（学習自体は best-effort 設計なので止まりません）。

**直し方**: 本番 `DATABASE_URL` を持つ環境で `uv run python scripts/db_migrate.py` を 1 回実行（冪等）。

> **技術メモ**: 2026-06-11 の `daily-preopen-core` ログで `macro: snapshot upsert failed (ignored): UndefinedTable: relation "macro_snapshots" does not exist` を確認（課題 #1 調査中に発見）。`migrations/0002_phase1_quality_schema.sql` に定義あり。どの workflow も `scripts/db_migrate.py` を実行していない（grep 確認済み）ため、スキーマ追加時は手動適用が必要。恒久対策として core workflow 冒頭への migrate ステップ追加も検討余地あり。

---

## ✅ 対応しない（判断済み 2026-06-11）

### 4. enabled 51 銘柄 > `settings.curation.max_universe: 50`

**判断**: このままで良い（50 超えは問題ない）。

**知っておくと良い挙動**: 上限を超えている間、キュレーションの自動入替は「新規追加なし・入替のみ」の保守モードで動きます（壊れたり暴走したりはしません）。もし将来「もっと銘柄を増やしたい」となったら、`tickers.yml` の `settings.curation.max_universe` の数字を実態に合わせて上げるのが正しい 1 行修正です。

### 5. 週次レポートの品質検証が未実装

**判断**: 対応しない（レポート品質はこだわらない）。AI が書く `reports/weekly_*.md` は内容チェックなしで URL が LINE 通知されますが、シグナルや売買判断には一切影響しないため、リスクは「レポートの読み味」だけです。

---

## ✅ 対応済み

### 1. TOPIX・日経VI・JGB10y の指数データが一度も取れていない（2026-06-11 修正）

**真因（当初仮説より一段深い）**: 「シンボルの書き間違い」ではなく、**Stooq の CSV エンドポイントが全シンボルに対して 404 を返しており**（正常稼働中の `usdjpy`・個別銘柄含む。CI ログで確認）、システム全体が yfinance フォールバックだけで動いていた。その上で当該 3 系列は (a) `topix` の yf シンボル `^TPX` が日次履歴を持たない空スタブ、(b) `^NIVI` が Yahoo に不存在、(c) `jgb10y` は yf フォールバック自体が未設定、だったため全滅していた。

**実装内容**:

- `topix` → **TOPIX 連動 ETF（1306）をプロキシに採用**（stooq `1306.jp` / yf `1306.T`）。リターン・移動平均ベースの用途（ベンチマーク、マクロ特徴量）には指数と実質同等。なお `1306.T` は yfinance の `period="max"` 指定でライブラリ内部エラーになるため、`max` が空振りした場合に `10y` でリトライする汎用フォールバックを `fetch_market_series` に追加（取得できた実績: 2,461 行、2016〜当日）
- `nikkei_vi` / `jgb10y` → **無効化**（両ソース `None`）。Yahoo に上場がなく、Stooq はエンドポイント自体が死んでいて代替シンボル（`10jpy.b` 候補）の検証も不能なため。特徴量スキーマは不変（全行 NaN のまま = 従来挙動と同一）で、保存済みモデルとの互換性に影響なし
- 過去の `benchmark_ret`/`excess_ret` NULL の補填: `daily-preopen-core.yml` の settle ステップに **`--refill-benchmark` を常設**（冪等・NULL 行のみ補填）。マージ後の初回 CI 実行で自動バックフィルされ、以後も自己修復する

> **技術メモ**: 検証は本番同一経路（`macro.fetch_market_series`）+ CI 実ログ + Yahoo chart API 直叩きの 3 系統で実施。`data/macro/macro_panel.parquet` はローカル再生成済み（topix 非NaN 2,607/15,572 行、`macro_topix_ret_20/vol_20/above_200dma` 算出確認）。テスト: `tests/test_macro_features.py` に 4 件追加（シンボル契約・無効化系列の無通信・`max`→`10y` リトライ×2）。Stooq が復活した場合は `1306.jp` が優先されるが同一銘柄なので水準は連続。

### 2. active 化の安全チェックが「合格点」を見ていない（2026-06-11 修正）

**実装内容**: `write_portfolio_backtest_report()` に `gate: {passed, failures}` を埋め込み、`weekly_cross_section_retrain.py` が評価済み `evaluate_portfolio_kpi_gate()` 結果を渡すように配線。読み手（`read_portfolio_gate()`、shadow report の `active_readiness.portfolio_gate_passed` も同関数）は `gate.passed` 優先の拡張点が実装済みだったため、書き出し側のみの修正で完結。

> **技術メモ**: gate キーが付くのは次回の週次再学習（2026-06-13）以降に生成される `docs/portfolio_backtest.json` から。それまでの旧ファイルは従来どおり availability フォールバック（shadow 運用中なので実害なし）。active 化判断は 6/13 以降のレポートで行うこと。テスト: `tests/test_portfolio_backtest.py` に writer→reader 往復の 1 件追加。

### 3. 「危ない相場ではポジションを半分に」のブレーキが常に OFF（2026-06-11 修正）

**実装内容**: `main.py` に `_load_portfolio_regime()`（`docs/curation/macro_latest.json` の `market_bias` を読む薄い loader、`_build_macro_regime()` 流用）を追加し、`_run_portfolio_snapshot()` の `regime="neutral"` ハードコードを置換。`market_bias: "risk_off"` の週は `TRADER_PORTFOLIO_RISK_OFF_GROSS_MULT=0.50` が gross に効く（テストで 1.0 → 0.5 の半減を実測確認）。未知ラベル・ファイル欠損は `neutral` に縮退（never-raise）。

> **技術メモ**: 有効ラベルは `risk_on | neutral | risk_off`（global-macro-screen のスキーマ準拠）。snapshot の `constraints.regime / regime_multiplier` に反映されるため `docs/portfolio_latest.json` で毎日確認可能。テスト: `tests/test_portfolio_regime.py` 新設（3 件、risk brake の配線をスタブ付きで実走検証）。

### 6. 毎晩の特徴量事前計算（削除実施 2026-06-11）

**実装内容**: 判断（削除）に基づき 4 点セットを実施 — `nightly-feature-precompute.yml`・`scripts/feature_precompute.py`・`docs/feature_precompute_report.json` を削除、publish workflow の exclude 行と `tests/test_publish_workflow.py` の期待リスト、仕様書（01/03/04/05）・README の該当記述を整理。復活が必要になったら git 履歴から数分で戻せます。

### 7. LINE 通知をダイジェストのみに集約（2026-06-11 実装）

**決定と実装内容**: 個別シグナル通知（1 銘柄 1 通）を**既定で無効**にし、朝のダイジェスト 1 通に「どの銘柄が買い/売りか」の銘柄名リストを追加しました。

```text
📊 朝のダイジェスト (2026-06-11)
レジーム: 中立 / ドル円 160.4
──────────
🧺 今日の建玉 [shadow / cs-v1-20260610]
...
──────────
📨 個別シグナル: 買い1 / やや買い2 / 売り1
🔴 買い: 三菱重工業
🟠 やや買い: トヨタ自動車 / ホンダ
🟢 売り: 日産自動車
🎯 直近実績(5日): 的中 58% (n=35) / 平均 +0.6%
詳細: https://...
```

- 表示はゲート通過シグナルのみ。各アクション最大 4 銘柄 + 「ほかN件」（LINE の文字数制限対策）
- これで通常運用の送信数は **日次 1 通 + 週次 2 通**程度になり、無料枠 200 通/月に対し大幅な余裕
- 個別通知を復活させたい場合は `TRADER_NOTIFY_PER_TICKER_ENABLED=true`（コードはそのまま残してある）

> **技術メモ**: `src/digest.py` `_signal_name_lines()` 追加、`main.py` の既定値を false へ、`daily-preopen-core.yml` / `.env.example` を false へ。テスト `tests/test_digest.py` に 4 件追加（38/38 pass）。

---

## 低優先・観察

| 項目                        | 内容                                                                                                                                                                                                                                     |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| リトライ実行日の計測欠け    | 06:20/06:40 の retry workflow は env が最小構成（DB・ポートフォリオ・マクロ更新・決済なし）。core が失敗し retry で救済された日は、シグナルは出るが DB 台帳・建玉 snapshot が欠ける。頻発するようなら core と同じ env/後続ステップを足す |
| CS 較正の粗さ               | shadow の建玉で複数銘柄の `expected_ret`/`prob_up` が同値（score-bucket 較正の粒度）。gross も低め（0.24 前後）。バグではなく shadow 期間の観察対象。改善候補: isotonic 連続化                                                           |
| `generated_at` の TZ 不統一 | 監査系 7 スクリプトが timezone naive（キュレーション系は `+09:00` 付き）。実害は小さい                                                                                                                                                   |
| `usdjpy` の行数が少ない     | 系列の歴史差によるもので異常ではない（参考情報）                                                                                                                                                                                         |

## 運用チェックリスト（時限・要人間判断）

- [ ] **課題 #8（最優先・6/13 より前に）**: 本番 `DATABASE_URL` で `uv run python scripts/db_migrate.py` を 1 回実行（冪等）。実行後の朝ランで `macro: upserted macro_snapshots` が出ることを確認
- [ ] **マージ後の翌営業日朝**: 課題 #1 修正の本番初回実行を確認。CI ログに `macro: fetched topix (…rows)` が出ること、`settle_outcomes` の `Refill benchmark: updated N/M rows` で過去の `benchmark_ret` NULL が埋まること、`/performance` の資産曲線に TOPIX 線が出ること
- [ ] **2026-06-13（土）**: 週次再学習の初回実走を確認。`data/models/active_model.json` と `per-ticker-v1-*` が commit され、翌営業日から日次が保存モデル推論に切り替わること（現状はポインタ不在のため全銘柄 legacy 学習で動作中）。`docs/model_quality.json` が `available: true` になること、`docs/portfolio_backtest.json` に `gate` キー（課題 #2 修正分）が付くことも確認
- [ ] `scripts/backfill_state_signals.py` を本番 DB に対して実行済みか確認（未実行なら 1 回。冪等）
- [ ] **2026-06-24 目安**（shadow 開始 2026-06-10 から 10 営業日）: `docs/portfolio_shadow_report.json` の `active_readiness` を見て active 化を判断。前提だった課題 #2 は修正済み（2026-06-11）。切替は `daily-preopen-core.yml` の `TRADER_PORTFOLIO_MODE` を `"active"` にする 1 行、戻すのも同じ 1 行
- [ ] active 化後 1 週間: ダイジェストの建玉と DB `signals.target_weight` の一致を毎朝確認

## Phase 4+ バックログ（未着手の将来案）

- **fills（約定）記録**: 手動約定の入力経路 → `fills` テーブル → 提案 vs 実約定の乖離計測
- 発注指示出力（証券会社 CSV / API、`src/execution.py`。不可逆処理は決定論コード限定の原則を維持）
- `signals.action` のポートフォリオ駆動化の再評価（現状は active でも action はモデル由来のまま）
- `active_readiness` の GitHub Issue 自動起票
- DB 長期アーカイブ自動化（`backtest_equity` の parquet 退避、400MB 警告は既存）と Alembic 導入判断
- ダッシュボードの認証・ユーザー管理
