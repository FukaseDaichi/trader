# マージ合成ロジックとガードレール

更新日: 2026-06-06 JST

`scripts/curation_merge.py` は AI 銘柄キュレーションの安全要です。Claude agent の JSON を統合し、guardrail 下で `tickers.yml` を編集し、監査ログを残します。LLM は呼ばず、`compute_decision()` はユニットテスト可能な純粋関数です。

## 1. 入出力

| 区分 | パス |
|---|---|
| 入力（当日） | `docs/curation/technical_latest.json` |
| 入力（週次キャッシュ） | `docs/curation/fundamental_latest.json` または `--fundamental` |
| 入力 | `tickers.yml`, `data/watchlist/*.parquet`, `settings.curation` |
| 出力 | `docs/curation/decision_latest.json`, `docs/curation/decision_YYYY-MM-DD.json` |
| `--apply` 時の出力 | `tickers.yml` |
| 副作用 | 昇格時に `data/watchlist/{code}.parquet` を `data/{code}.parquet` へ移動 |

CLI:

```bash
uv run python scripts/curation_merge.py \
  --technical docs/curation/technical_latest.json \
  --date <YYYY-MM-DD> \
  --apply
```

`--fundamental` を省略すると `docs/curation/fundamental_latest.json` を読みます。`--apply` なし、または `--dry-run` では監査ログのみを書き、`tickers.yml` は変更しません。

## 2. 合成スコア

両スコアは 0-100 です。

```text
combined = tech_weight * tech_score + fund_weight * fund_score
```

現行実装では、片軸のみの銘柄にも `combined` はその片軸スコアで設定されます。ただし、新規昇格 eligibility は `both == true`、つまり tech/fund の両方のスコアがある銘柄に限定されます。片軸のみの銘柄は watchlist には入り得ますが、enabled へ昇格しません。

`fundamental_latest.json` の `as_of` が欠落している、または `settings.curation.max_fundamental_age_days`（既定 14 日）を超える場合、conservative mode になります。この場合、enabled ユニバースの追加・入替は停止します。

## 3. 状態とアクション

`decision_*.json` の `ranking[].action` は以下です。

- `keep`: enabled 継続
- `promote`: enabled へ昇格
- `demote`: enabled から無効化
- `watch`: watchlist へ保持
- `reject`: 採用しない

## 4. 昇格条件

新規昇格候補は、以下を満たす必要があります。

- enabled ではない
- tech/fund の両スコアがある
- `combined >= min_combined_to_promote`（既定 70）
- `rows_available >= min_warmup_rows`（既定 200）
- cooldown 対象ではない
- conservative mode ではない
- セクター上限を超えない

`max_universe` 未満の空き枠に追加する add phase では、既存 enabled 最下位との差分 `min_gap` は要求されません。満杯時の swap phase では、候補が最下位 enabled を `min_gap` 以上上回り、かつ最下位 enabled が `keep_floor` 未満の場合だけ入替します。

## 5. 降格条件

降格は swap phase で昇格とペアになります。

- 候補が最下位 enabled を `min_gap` 以上上回る
- 最下位 enabled の score が `keep_floor` 未満
- `max_daily_swaps` 内
- conservative mode ではない

降格された ticker は `enabled: false` と `disabled_on: <date>` が設定されます。次回 `main.py` の `sync_data_files()` がトップレベル `data/{code}.parquet` を `data/archive/` へ退避します。現行実装は archive からの自動復元は行いません。再昇格には pool/watchlist 側で warmup データが必要です。

## 6. watchlist

enabled ではなく、`combined >= keep_floor` の銘柄は watchlist 候補になります。

- `status: "ready"`: 両軸スコアあり、warmup OK、昇格閾値以上、cooldown 外
- `status: "warming"`: 上記を満たさないが watchlist floor 以上

watchlist は score 降順で最大 20 件に制限されます。conservative mode でも watchlist は更新され得ます。

## 7. 設定値

| 設定(`settings.curation`) | 既定 | 意味 |
|---|---:|---|
| `enabled` | true | キュレーション機能の ON/OFF |
| `max_universe` | 10 | enabled 銘柄数の上限 |
| `max_daily_swaps` | 2 | 1営業日の入替上限 |
| `max_daily_adds` | 2 | 空き枠への追加上限 |
| `min_combined_to_promote` | 70 | 昇格スコア閾値 |
| `min_gap` | 5 | swap 時の最小差 |
| `keep_floor` | 50 | enabled がこれ未満なら降格対象になり得る |
| `min_warmup_rows` | 200 | 昇格に必要な最小データ行数 |
| `sector_cap_pct` | 40 | 単一セクターの最大比率 |
| `fund_weight` / `tech_weight` | 0.5 / 0.5 | 合成重み |
| `cooldown_days` | 5 | 降格後の再昇格抑止日数 |
| `max_fundamental_age_days` | 14 | ファンダキャッシュ許容日数 |

## 8. フェイルセーフ

| 事象 | 現行挙動 |
|---|---|
| `settings.curation.enabled=false` | 何も変更せず exit 0 |
| technical が欠落/壊れている | conservative mode。enabled 追加・入替なし |
| fundamental が欠落/古い | conservative mode。enabled 追加・入替なし |
| warmup 不足 | 昇格せず watchlist に残す |
| churn 上限到達 | 上位から上限まで適用し、残りは watch/reject |
| `tickers.yml` 検証失敗 | workflow の validate step が失敗し push 中止 |

## 9. 監査ログ

`decision_latest.json` と `decision_YYYY-MM-DD.json` には以下を記録します。

- `date`, `as_of`, `applied`, `tickers_written`
- `inputs.technical`, `inputs.fundamental`
- `weights`
- `fundamental_age_days`, `conservative_mode`
- `ranking`
- `changes.promoted`, `promoted_add`, `promoted_swap`, `demoted`, `watchlist`
- `guardrails`
- `data_moves`
- `universe_before`, `universe_after`
- `generated_at`
