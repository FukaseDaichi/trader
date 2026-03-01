# GitHub Actions 頻度設計（ポートフォリオ最適化向け・詳細版）

作成日: 2026-03-01 (JST)

## 1. 目的

- 目的は「銘柄単体判定」から「全銘柄ランキング + 配分最適化」へ拡張する際の、GitHub Actions 実行頻度を具体化すること。
- 前提は **重い処理を毎日フルユニバースに掛けない** 設計。

## 2. 現状と課題

- 現状ワークフロー: `.github/workflows/daily_job.yml`
  - `cron: "0 21 * * *"`（JST 06:00）
  - 1本のジョブで「データ更新 -> 予測 -> 通知 -> フロントビルド」を実行
- 課題:
  - 全処理が単一ジョブで失敗点が集中
  - 将来の候補銘柄増加時に実行時間が伸びやすい
  - リトライ戦略と段階的処理（候補絞り込み）が不足

## 3. 設計原則（頻度設計）

1. 日次ジョブは「当日意思決定に必要な範囲」に限定
2. 重いジョブは週次/夜間へ分離
3. 市場休場日（JPX）では重い日次処理を自動スキップ
4. 1ジョブ失敗で全体停止しないように分割
5. 失敗時は前日ポートフォリオ維持 + 通知

## 4. ワークフロー一覧（必須）

| Workflow | 用途 | 推奨トリガー (UTC cron) | JST時刻 | 頻度 | 想定時間 |
|---|---|---|---|---|---|
| `daily-preopen-core.yml` | 当日シグナル生成・ポートフォリオ配分・通知 | `0 21 * * 0-4` | 平日 06:00 | 毎営業日 | 10〜20分 |
| `daily-preopen-retry.yml` | データ遅延/一時失敗の再実行 | `20 21 * * 0-4`, `40 21 * * 0-4` | 平日 06:20/06:40 | 毎営業日 | 3〜10分 |
| `daily-publish-dashboard.yml` | `docs/` 反映とPages更新 | `workflow_run`（core成功時） | core直後 | 毎営業日 | 3〜8分 |
| `daily-watchdog.yml` | 成果物有無・鮮度・失敗検知 | `30 3 * * 1-5` | 平日 12:30 | 毎営業日 | 1〜3分 |
| `weekly-model-retrain.yml` | 候補銘柄向け再学習 | `0 23 * * 5` | 土曜 08:00 | 週1 | 30〜90分 |
| `weekly-universe-refresh.yml` | ユニバース更新・候補入替 | `0 22 * * 6` | 日曜 07:00 | 週1 | 20〜60分 |
| `monthly-calendar-sync.yml` | JPX営業日カレンダー更新 | `15 0 1 * *` | 毎月1日 09:15 | 月1 | 3〜10分 |
| `monthly-full-audit.yml` | フルバックテスト・KPI監査 | `0 0 1-7 * 0` | 第1日曜 09:00 | 月1 | 60〜180分 |

## 5. ワークフロー一覧（推奨オプション）

| Workflow | 用途 | 推奨トリガー (UTC cron) | JST時刻 | 頻度 |
|---|---|---|---|---|
| `nightly-rotating-refresh.yml` | 非コア銘柄のローテ更新 | `30 10 * * 1-5` | 平日 19:30 | 週5 |
| `nightly-feature-precompute.yml` | 翌朝向け軽量特徴量の事前計算 | `0 11 * * 1-5` | 平日 20:00 | 週5 |
| `quarterly-stress-test.yml` | ストレスシナリオ検証 | `0 1 1 1,4,7,10 *` | 四半期初日 10:00 | 四半期 |

## 6. データ層別の更新頻度（重さ対策の中核）

| データ層 | 対象規模 | 更新頻度 | 実行ジョブ |
|---|---:|---|---|
| `Core` | 現在保有 + 監視上位（例 40〜150） | 毎営業日 朝 | `daily-preopen-core.yml` |
| `Candidate` | 候補群（例 200〜600） | 毎営業日 夜（またはローテ） | `nightly-rotating-refresh.yml` |
| `Universe` | 全銘柄（例 3000〜4000） | 週次フル + 月次整合 | `weekly-universe-refresh.yml`, `monthly-full-audit.yml` |

## 7. 営業日/休場日の分岐ルール

1. すべてのスケジュールジョブ先頭で `JPX営業日判定` を実行
2. 休場日:
   - `daily-preopen-core.yml`: `skip`
   - `daily-preopen-retry.yml`: `skip`
   - `daily-publish-dashboard.yml`: 実行しない
3. 休場日でも実行:
   - `weekly/monthly` のメンテ系ジョブは通常実行

## 8. 当日タイムライン（営業日）

1. `06:00 JST` core実行（本命）
2. `06:20 JST` retry 1（core失敗時のみ実質処理）
3. `06:40 JST` retry 2（retry 1失敗時のみ実質処理）
4. `06:50-07:00 JST` publish
5. `12:30 JST` watchdog（出力ファイル鮮度・通知実行有無を検証）

## 9. 当日タイムライン（休場日）

1. `06:00/06:20/06:40 JST` は営業日判定で即終了
2. Watchdogは「休場日スキップ判定が正しいか」だけ確認

## 10. リソース見積もり（private repo想定）

- 日次 core: `15分 x 22営業日 = 330分/月`
- 日次 retry: `2分 x 44回 = 88分/月`（大半は即終了）
- 日次 watchdog: `2分 x 22日 = 44分/月`
- 週次 retrain: `60分 x 4回 = 240分/月`
- 週次 universe refresh: `30分 x 4回 = 120分/月`
- 月次 audit: `120分 x 1回 = 120分/月`
- 合計目安: **約942分/月**

備考:
- public repository なら minutes 制約は緩いが、所要時間短縮は失敗率低減に有効。
- private repository では余裕を見て 1200〜1500分/月以内に収める設計を推奨。

## 11. 失敗時運用（頻度設計とセット）

1. core失敗 -> retry 1 へ自動委譲
2. retry 1失敗 -> retry 2 へ自動委譲
3. retry 2失敗 -> 前日配分維持 + アラート通知
4. watchdog で未更新を検知したら `workflow_dispatch` 手動実行案内

## 12. Concurrency / Timeout 推奨値

| Workflow | concurrency.group | cancel-in-progress | timeout-minutes |
|---|---|---|---:|
| daily-preopen-core | `daily-core-main` | `false` | 30 |
| daily-preopen-retry | `daily-core-main` | `false` | 30 |
| daily-publish-dashboard | `daily-publish-main` | `true` | 20 |
| weekly-model-retrain | `weekly-retrain-main` | `false` | 120 |
| weekly-universe-refresh | `weekly-universe-main` | `false` | 120 |
| monthly-full-audit | `monthly-audit-main` | `false` | 240 |

## 13. Phase別の導入順序

1. Phase 1（即導入）
   - core / retry / publish / watchdog の4本
2. Phase 2（次段階）
   - weekly retrain / weekly universe refresh
3. Phase 3（拡張）
   - monthly audit / nightly rotating refresh

## 14. 具体的な cron 設計サンプル

```yaml
# JST 平日 06:00 = UTC 日-木 21:00
schedule:
  - cron: "0 21 * * 0-4"
  - cron: "20 21 * * 0-4"
  - cron: "40 21 * * 0-4"
```

```yaml
# JST 土曜 08:00 = UTC 金曜 23:00
schedule:
  - cron: "0 23 * * 5"
```

```yaml
# JST 日曜 07:00 = UTC 土曜 22:00
schedule:
  - cron: "0 22 * * 6"
```

## 15. この設計での回答（質問への直接回答）

- 「全銘柄ダウンロードして解析はかなり重くならない？」  
  -> 重くなる。よって **頻度で制御** する。  
  - 全銘柄は週次/夜間中心  
  - 毎朝は上位候補のみ詳細計算  
  - リトライを朝の短い窓に限定  
  これで処理時間と実運用安定性の両立が可能。
