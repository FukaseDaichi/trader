# 修正優先度マトリクス

## 凡例

- **影響度**: 障害時のビジネスインパクト (HIGH = シグナル配信停止/データ破損, MEDIUM = 表示不正/性能劣化, LOW = 保守性低下)
- **発生頻度**: 問題が顕在化する頻度 (HIGH = 毎日/毎回, MEDIUM = 条件付き/週次, LOW = まれ)
- **修正コスト**: 修正にかかる工数 (S = 数行, M = 数十行, L = 設計変更必要)

---

## Phase 1: 緊急対応 (1-2 日)

| # | 問題 | ファイル | 影響度 | 頻度 | コスト | 参照 |
|---|------|---------|--------|------|--------|------|
| 1 | HTTP タイムアウト未設定 | `data_loader.py` | HIGH | MEDIUM | S | 01-1.1 |
| 2 | fetch の `res.ok` チェック未実装 | `page.tsx`, `StockDetailContent.tsx` | HIGH | MEDIUM | S | 02-1.1 |
| 3 | `Math.min/max` 空配列クラッシュ | `StockChart.tsx` | HIGH | LOW | S | 02-3.1 |
| 4 | volatility NaN 時の表示崩れ | `predictor.py` | HIGH | MEDIUM | S | 01-3.1 |
| 5 | ティッカー毎の例外ハンドリング | `main.py` | MEDIUM | MEDIUM | S | 01-7.1 |

**推定工数**: 2-4 時間

---

## Phase 2: 安定性向上 (3-5 日)

| # | 問題 | ファイル | 影響度 | 頻度 | コスト | 参照 |
|---|------|---------|--------|------|--------|------|
| 6 | git push 前に pull --rebase 追加 | 全 9 ワークフロー | HIGH | MEDIUM | M | 03-1.1 |
| 7 | retry ワークフローの stale checkout | `daily-preopen-retry.yml` | HIGH | MEDIUM | S | 03-3.1 |
| 8 | rsync --delete の exclude 追加 | `daily-publish-dashboard.yml` | HIGH | LOW | S | 03-4.1 |
| 9 | JSON ファイルのアトミック書き込み | `dashboard.py` | MEDIUM | LOW | M | 01-6.2 |
| 10 | LINE API のリトライロジック | `notifier.py` | MEDIUM | MEDIUM | M | 01-5.1 |
| 11 | nightly ワークフローのコンカレンシーグループ | 2 ワークフロー | MEDIUM | MEDIUM | S | 03-7.1, 03-8.2 |
| 12 | `cmd_sync` のエラーハンドリング | `jpx_calendar.py` | MEDIUM | LOW | S | 03-9.2 |

**推定工数**: 1-2 日

---

## Phase 3: データ品質・性能改善 (1-2 週間)

| # | 問題 | ファイル | 影響度 | 頻度 | コスト | 参照 |
|---|------|---------|--------|------|--------|------|
| 13 | history_data.json の肥大化対策 | `dashboard.py` | HIGH | HIGH | M | 01-6.1 |
| 14 | 閾値最適化の過学習リスク軽減 | `backtest.py` | HIGH | HIGH | L | 01-4.1 |
| 15 | train/backtest パラメータ統一 | `model.py`, `config.py` | MEDIUM | HIGH | M | 01-2.2 |
| 16 | ダウンロードデータの整合性検証 | `data_loader.py` | MEDIUM | MEDIUM | M | 01-1.3 |
| 17 | Stooq エラー検知の改善 | `data_loader.py` | MEDIUM | LOW | M | 01-1.2 |
| 18 | 全ワークフロー失敗通知の追加 | 全ワークフロー | MEDIUM | MEDIUM | M | 03-1.2 |
| 19 | watchdog の鮮度検証強化 | `workflow_watchdog.py` | MEDIUM | LOW | S | 04-3.2 |

**推定工数**: 5-10 日

---

## Phase 4: コード品質・保守性 (2-4 週間)

| # | 問題 | ファイル | 影響度 | 頻度 | コスト | 参照 |
|---|------|---------|--------|------|--------|------|
| 20 | `logging` モジュール導入 | 全 Python ファイル | MEDIUM | — | M | 05-1 |
| 21 | タイムゾーン処理の統一 | 全スクリプト | MEDIUM | — | M | 05-5 |
| 22 | 型ヒントの追加 | 全 src/ | LOW | — | L | 05-2 |
| 23 | ユニットテスト基盤の構築 | 新規 `tests/` | LOW | — | L | 05-3 |
| 24 | TypeScript 型定義の修正 | `types/index.ts` | MEDIUM | — | S | 02-2.1 |
| 25 | signal.ts の default ケース追加 | `signal.ts` | MEDIUM | — | S | 02-2.2 |
| 26 | `error.tsx` / `not-found.tsx` 追加 | `web/src/app/` | MEDIUM | — | M | 02-6.2, 6.3 |
| 27 | FOUC 対策 (CSS 修正) | `globals.css` | MEDIUM | — | S | 02-5.1 |
| 28 | `config.py` 遅延初期化 | `config.py` | LOW | — | M | 05-8 |
| 29 | `numpy` 直接依存追加 | `pyproject.toml` | LOW | — | S | 05-4 |
| 30 | `.gitignore` 更新 | `.gitignore` | LOW | — | S | 05-6 |
| 31 | 未使用フォント/依存関係の削除 | `layout.tsx`, `package.json` | LOW | — | S | 02-5.2, 5.3 |
| 32 | feature_precompute の活用/削除判断 | ワークフロー, スクリプト | LOW | — | M | 04-7.1 |
| 33 | データフェッチ共通フック化 | `web/src/hooks/` | LOW | — | M | 02-1.3 |
| 34 | アクセシビリティ改善 | 複数コンポーネント | LOW | — | M | 02-4.x |

**推定工数**: 2-3 週間

---

## 修正しない/保留項目

| 問題 | 理由 |
|------|------|
| ティッカー処理の並列化 (`main.py`) | 現行6ティッカーでは不要。将来ティッカー数増加時に検討 |
| `int()` → `round()` (指値計算) | 実影響が1円以内。優先度最低 |
| Parquet マージの `keep='last'` | 代替策の複雑さに対して発生頻度が極めて低い。Phase 3 のデータ検証で間接的に対処 |
| `dynamicParams = false` 明示 | 静的エクスポートのデフォルト動作で問題なし |

---

## 全体の推定工数

| フェーズ | 工数 | 累計 |
|---------|------|------|
| Phase 1: 緊急対応 | 2-4h | 2-4h |
| Phase 2: 安定性向上 | 1-2日 | 2-3日 |
| Phase 3: データ品質 | 5-10日 | 1-2週 |
| Phase 4: コード品質 | 2-3週 | 3-5週 |
