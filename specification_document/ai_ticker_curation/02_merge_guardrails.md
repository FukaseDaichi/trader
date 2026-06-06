# マージ合成ロジックとガードレール

作成日: 2026-06-06 JST ／ 改訂: rev.2

`scripts/curation_merge.py`（新規・実装対象）は本システムの**安全要**。エージェントレポートを統合し、ガード下で `tickers.yml` を編集し、監査ログを残す。LLMを呼ばない決定論コードであり、ユニットテスト可能にする。

**頻度の非対称性（rev.2）**: 日次マージは「**当日のテクニカル** + **直近週のファンダ（キャッシュ）**」で合成する。テクニカルは平日毎日更新、ファンダは週次（土曜）更新で、平日は同じ `fundamental_latest.json` を参照し続ける。業績/開示は日次で動かないため、これで「毎日の判断にも業績の裏付け」を保ちつつOpus消費を週1に抑える。

## 1. 入出力

| 区分 | パス |
|---|---|
| 入力（当日） | `docs/curation/technical_latest.json` |
| 入力（週次キャッシュ） | `docs/curation/fundamental_latest.json`（最大6営業日前のものを許容） |
| 入力 | `tickers.yml`(`tickers`,`watchlist`,`settings.curation`), `data/*.parquet`, `data/watchlist/*.parquet` |
| 出力 | `tickers.yml`(更新), `docs/curation/decision_YYYY-MM-DD.json`(監査), `docs/curation/decision_latest.json` |
| 副作用 | 昇格時 `data/watchlist/{code}.parquet` → `data/{code}.parquet` 移管 |

CLI: `uv run python scripts/curation_merge.py --technical <t.json> [--fundamental <f.json>] --date <JST> [--apply] [--dry-run]`
- `--fundamental` は任意。指定が無い/古い場合は直近週のキャッシュを使用（`fundamental_latest.json` の `as_of` で鮮度を確認し、監査ログに `fundamental_age_days` を記録）。
- `--apply` 無し（既定）は **dry-run**：監査ログだけ書き `tickers.yml` は変更しない（段階導入で有効、`06`）。

## 2. 合成スコア

両スコアは 0-100。`tech_score` は当日値、`fund_score` は直近週キャッシュ値。設定 `settings.curation.fund_weight` / `tech_weight`（既定 0.5/0.5）で加重。

```
combined(code) = tech_weight * tech_score(code)        # 当日のテクニカル
               + fund_weight * fund_score(code)         # 直近週のファンダ（キャッシュ）
```

- **片軸のみの銘柄**: 欠側を中立50ではなく保守的に扱い、**昇格は両軸スコアを持つ銘柄に限定**（watchlist追加は片軸でも可）。
- **ファンダ・キャッシュが古い/未取得**: `fund_score` がキャッシュにある銘柄はそれを使用。キャッシュにも無い銘柄は「ファンダ未評価」とし**昇格対象外**（テクニカルだけで新規enabled化しない）。`fundamental_age_days` を監査ログに記録し、`settings.curation.max_fundamental_age_days`（既定14）を超える場合は**入替を保守化**（新規昇格を停止し既存維持。週次更新の遅延・失敗時の暴走防止）。
- **同点時の決定性**: `combined` 降順 → `fund_score` 降順 → `code` 昇順で安定ソート。

## 3. 状態機械（各銘柄の判定）

```
                   ┌─────────── reject (combined低 or ハードフィルタ不通過)
candidate ─────────┤
                   ├─ watch    (有望だが warmup未充足 or churn上限で見送り) → watchlist へ
                   └─ promote  (全ガード通過) → tickers.yml enabled へ

enabled 既存 ──────┬─ keep
                   └─ demote   (下位 & 昇格候補に明確に劣後) → enabled から外す
```

### 昇格(promote)の必要条件（すべて満たす）
1. `combined >= settings.curation.min_combined_to_promote`（既定 70）。
2. warmup充足: 当該銘柄の利用可能行数 `>= settings.curation.min_warmup_rows`（既定 200。既存 `main.py` がMA60等で最低60行、KPIゲートが `TRADER_BT_MIN_TRAIN_ROWS=200` を要求することに整合）。
3. 既存enabledの**最下位**より `min_gap` 以上高い（既定 `combined` で +5）。
4. churn上限内（§4）かつ `max_universe` 内（§4）。
5. セクター上限内（§5）。
6. JPX営業日（ワークフロー側ガード、`03`）。

### 降格(demote)の条件
- enabled の最下位群で、昇格候補に `min_gap` 以上劣後し、かつ `combined < keep_floor`（既定 50）。
- 1日の純入替が churn上限を超えないよう、降格は昇格とペアで上限内に制限。

### watch / reject
- promoteの2(warmup)や3-5(枠/上限)で見送った有望候補は **watchlist** に追加し、翌日以降のwarmup・再評価対象にする。
- `combined` が低い、またはハードフィルタ（ファンダ：直近90日一次情報なし／財務危機等、テクニカル：データ不足）で reject。watchlistからも除去（陳腐化防止）。

## 4. churn上限とユニバースサイズ

| 設定(`settings.curation`) | 既定 | 意味 |
|---|---|---|
| `enabled` | true | キュレーション機能のON/OFF |
| `max_universe` | 10 | enabled銘柄数の上限 |
| `max_daily_swaps` | 2 | 1営業日の**入替**上限（promote数=demote数の対）。新規枠埋め(空きへのpromote)は別途 `max_daily_adds`(既定2)。 |
| `min_combined_to_promote` | 70 | 昇格スコア閾値 |
| `min_gap` | 5 | 昇格候補が最下位enabledを上回る最小差 |
| `keep_floor` | 50 | これ未満のenabledは降格対象になりうる |
| `min_warmup_rows` | 200 | 昇格に必要な最小データ行数 |
| `sector_cap_pct` | 40 | 単一セクターの最大比率(%) |
| `fund_weight`/`tech_weight` | 0.5/0.5 | 合成重み |
| `cooldown_days` | 5 | 降格直後の同一銘柄の再昇格を抑止する日数（往復防止） |
| `max_fundamental_age_days` | 14 | ファンダ・キャッシュ許容鮮度。超過時は新規昇格停止（週次更新の遅延/失敗時の暴走防止） |

- `max_universe` 未満なら、入替なしで上位候補を**追加**(add)できる（`max_daily_adds` 上限）。これで初期はユニバースを育て、満杯後は入替主体になる。
- **whipsaw防止**: `cooldown_days` と `min_gap` により、僅差での日次往復入替を抑止。

## 5. セクター/分散・流動性ガード

- `selection-framework.md` の分散規則を踏襲：単一セクター比率 `<= sector_cap_pct`（既定40%）、可能なら3セクター以上。
- セクター情報はファンダレポートの `sector` を一次ソースとし、欠落時は簡易対応表で補完。
- 流動性: 候補プール `curation_pool.yml` を流動性ある銘柄に限定（テクニカル側の足切り）。

## 6. フェイルセーフ（安全側のデフォルト）

| 事象 | 挙動 |
|---|---|
| 当日テクニカル(`technical_latest.json`)が欠落/壊れている | **ユニバース変更なし**（現状維持）。監査ログに理由記録。exit 0（差分なしで正常終了）。 |
| ファンダ・キャッシュが未取得/古すぎる(`> max_fundamental_age_days`) | **新規昇格を停止**し既存ユニバースを維持（降格も保守化）。週次更新の回復を待つ。 |
| 両方欠落 | 現状維持。 |
| スキーマ検証失敗（`tickers.yml`） | 変更を破棄し push 中止（`03`の検証ステップで担保）。 |
| warmup未充足の昇格候補のみ | 昇格せず watchlist 維持。データが貯まるのを待つ。 |
| churn上限到達 | 上位優先で上限まで入替、残りは watchlist へ。 |

## 7. データwarmupと昇降格時のデータ移管

既存 `sync_data_files()` は**トップレベル `data/*.parquet` のみ**を走査し、enabled外を `data/archive/` へ退避する（サブディレクトリは対象外）。これを利用する。

- **warmup**: 候補プール+watchlist の銘柄データは `scripts/curation_warmup.py` が `data/watchlist/{code}.parquet` に取得する。サブディレクトリなので `main.py` の `sync_data_files()` に退避されない。`data/watchlist/` は **gitignore（非コミット）**。`update_data` は毎回フル履歴を再取得するため蓄積コミットは不要で、リポジトリ肥大化も避けられる。
- **昇格時**: `curation_merge.py` が `data/watchlist/{code}.parquet` を `data/{code}.parquet` へ**移動**し、同一コミットで `enabled: true` にする。これにより 06:00 の `main.py` 実行時には「enabled かつ トップレベルにデータあり」で履歴を保ったまま処理される（退避されない）。
- **降格時**: `enabled: false` にするのみ。次回 `main.py` の `sync_data_files()` が `data/{code}.parquet` を `data/archive/` へ退避する。再昇格時は archive から復元を試みる（再ダウンロード回避）。

> 実装メモ: `update_data()` は出力先がトップレベル固定のため、warmup用に「出力ディレクトリ指定」か専用 `warmup_candidate(code, dest=data/watchlist)` ヘルパを追加する。`update_data` の本体ロジック（Stooq→yfinanceフォールバック、正規化、検証）は流用する。

## 8. 監査ログ（`decision_YYYY-MM-DD.json`）

入力レポートの参照、全候補のランキングと判定理由、適用した入替、ガード適用状況、`universe_before`/`universe_after` を記録（スキーマは `04`）。これにより**毎日の意思決定が後から完全再現・説明可能**になり、誤判定時の `git revert` と原因追跡が容易になる。
