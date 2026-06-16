# 候補プール（母集団）リフレッシュ

更新日: 2026-06-16 JST

`curation_pool.yml` は日次テクニカルスクリーンが評価する候補母集団です（`pool ∪ enabled ∪ watchlist` の union）。プールに無い銘柄は enabled になり得ないため、母集団そのものの質がシステムの上限を決めます。この文書は、その母集団を **ファンダメンタル＋流動性** で **隔週** 見直す仕組み（pool refresh）を扱います。短期の値動き評価（`02_merge_guardrails.md` の日次 merge）とは別レイヤで、そちらは一切変更しません。

設計の核は日次キュレーションと同じ「LLM は提案 JSON を書くだけ、決定論スクリプトだけが母集団ファイルを書く」です。日次の構図を 1 段上（母集団）へ適用します。

- 日次: `technical_screen → technical_latest.json → curation_merge → tickers.yml`
- 隔週: `pool_screen → pool_candidates_latest.json → curation_pool_merge → curation_pool.yml`

## 1. 部品

| 区分 | 実体 |
|---|---|
| skill | `.claude/skills/jp-stock-pool-screen/`（`claude-sonnet-4-6`） |
| 決定論 merge | `scripts/curation_pool_merge.py`（`curation_pool.yml` の唯一の書き手、LLM 非使用） |
| 通知 | `scripts/curation_pool_notify.py`（LINE、best-effort） |
| テスト | `tests/test_curation_pool_merge.py`（plain script、`compute_pool_decision()` 等を単体検証） |
| 設定 | `tickers.yml settings.curation.pool`（未設定時はコードレベル既定） |
| workflow | `weekly-fundamental-report.yml`（土曜、隔週ステップ） |

## 2. agent（`/jp-stock-pool-screen`）

- ファンダメンタルが強く流動性のある日本の大型株候補を一次情報（IR・TDnet/EDINET・JPX・公式資料）で調査し、`docs/curation/pool_candidates_latest.json` と `pool_candidates_<as_of>.json` を書くだけ。
- `curation_pool.yml`・`tickers.yml`・`data/`・`src/`・`web/`・`.github/` は編集禁止、git 操作不可。
- 短期テクニカルは対象外（日次レイヤの担当）。`fund_score`（0-100、70 以上で追加目安・80 以上で高確信）と参考 `liquidity_jpy` を付ける。**流動性の権威は merge 側のローカル parquet 実測値**で、AI 提供値は文脈情報。
- rollout 初期は `action_hint: "add"` 中心。`drop` は明確に陳腐化／構造的に弱い名のみ（`max_drops_per_run: 0` の間は merge が無視）。

## 3. 決定論 merge（`curation_pool_merge.py`）

入出力:

| 区分 | パス |
|---|---|
| 入力 | `docs/curation/pool_candidates_latest.json`, `curation_pool.yml`, `tickers.yml`(enabled), `data/watchlist/*.parquet` / `data/*.parquet`（流動性） |
| 出力 | `curation_pool.yml`（**プールが実際に変わるときだけ** `--apply`）, `docs/curation/pool_decision_latest.json`, `pool_decision_<DATE>.json` |
| 副作用 | 提案候補の parquet を `data/watchlist/` へ best-effort 取得、stale な warmup parquet の掃除 |

CLI（`curation_merge.py` と対称）:

```bash
uv run python scripts/curation_pool_merge.py \
  --proposal docs/curation/pool_candidates_latest.json \
  --date <YYYY-MM-DD> --apply          # 既定は dry-run
uv run python scripts/curation_pool_merge.py --check-due [--force] [--github-output]
```

`compute_pool_decision()` は副作用のない純粋関数で、`tests/test_curation_pool_merge.py` が単体検証します。

## 4. ガードレール（`settings.curation.pool`）

| 設定 | 既定 | 意味 |
|---|---:|---|
| `enabled` | true | pool refresh の ON/OFF |
| `pool_target_size` | 60 | この数まで grow、以降は維持 |
| `pool_max_size` | 80 | ハード上限。超えない |
| `cadence_days` | 14 | 隔週で実行 |
| `max_adds_per_run` | 3 | 1 回の追加上限 |
| `max_drops_per_run` | 0 | 0 = add-only（rollout phase 1） |
| `min_fund_score_to_add` | 70 | 追加に必要な AI スコア下限 |
| `liquidity_floor_jpy` | 1,000,000,000 | ローカル parquet の中央値売買代金の下限（大型株ゲート） |
| `pool_sector_cap_pct` | 40 | 単一セクター比率の上限 |
| `pool_cooldown_days` | 30 | drop 後の再参入抑止日数 |

- **grow / replace 自動切替**: `len(pool) < pool_target_size` は grow（最大 `max_adds_per_run` 追加）。`>= pool_target_size` は replace（追加は drop とペア、`max_drops_per_run` で制御、総数一定）。phase 1 は `max_drops_per_run: 0` のため target まで増えて保持。
- **enabled 保護**: `tickers.yml` で enabled の銘柄はランキング前に drop 候補から除外。
- **流動性フロア**: 直近約 60 日の `close × volume` 中央値をローカル parquet から算出。提案候補に parquet が無ければ `src.data_loader.update_data()` で `data/watchlist/` へ取得。取得・算出できなければ `missing_local_liquidity` で reject。
- **セクター上限・cooldown・サイズ上限** は決定論で、日次ガードと同じ思想。

各候補の `add`/`drop`/`keep`/`reject` と理由（発火したガード）は `pool_decision_*.json` の `ranking[].reason` に残ります（例: `add_limit_reached`, `fund_score_below_floor`, `liquidity_below_floor`, `missing_local_liquidity`, `sector_cap`, `pool_cooldown`, `replace_mode_drops_disabled`, `enabled_protected`）。

## 5. cadence ガード

`--check-due` が `docs/curation/pool_decision_latest.json` の `date` を見て、`cadence_days` 以上経過していれば due。前回判断なし、または前回が `proposal_valid: false` のときも due（**無効・欠落の提案は cadence を消費しない**＝翌週リトライ）。`workflow_dispatch.inputs.pool_refresh_force=true`（→ `--force`）で強制。`--github-output` で `due` を `$GITHUB_OUTPUT` へ書き出し、後続ステップが `if` で参照します。

## 6. warmup 掃除（データ保持）

`data/watchlist/` は gitignore の warmup キャッシュであり正典ではありません。プールから外れた名の parquet がローカル／self-hosted runner に残り続けないよう、merge が最終プール確定後に掃除します。

- `data/watchlist/{code}.parquet` は `final_pool ∪ tickers.yml enabled ∪ tickers.yml watchlist` のものだけ残す。
- それ以外は `--apply` 時のみ削除（`--dry-run` は削除予定のみ報告）。
- トップレベル `data/{code}.parquet` は触らない（`src.data_loader.sync_data_files()` の管轄）。
- 結果（`warmup_files_removed` / `warmup_bytes_removed` / errors）を `pool_decision_*.json` に記録。掃除失敗はログのみで継続し、週次 workflow を落とさない。

## 7. workflow 統合

週次 `weekly-fundamental-report.yml`（土曜）のファンダ agent の後に 3 ステップを追加:

1. **cadence ガード** `curation_pool_merge.py --check-due --github-output`（`continue-on-error: true`）
2. due のときだけ **`/jp-stock-pool-screen`**（Sonnet, `--max-turns 20`, WebSearch/WebFetch 可・Bash/Edit 禁止, `continue-on-error: true`）
3. due のときだけ **`curation_pool_merge.py --apply`**（`continue-on-error: true`）

その後 commit ステップに `curation_pool.yml` を追加し、due のときだけ `curation_pool_notify.py` で LINE 通知。

- `workflow_dispatch.inputs`: `pool_refresh_force`（既定 `false`）、`pool_refresh_apply`（既定 `true`→`--apply`、`false`→`--dry-run`）。
- 新しい 2 つの決定論ステップは `continue-on-error: true`。pool 処理の例外が週次レポート生成・commit・実績通知を巻き込まないための障害分離です。
- `docs/curation/` は publish workflow で `--exclude 'curation'` 済みのため publish 変更は不要。

## 8. 通知

`curation_pool_notify.py` が `pool_decision_latest.json` の `changes`（追加／除外）を LINE で要約通知します（銘柄名と一行理由）。変更なしはスキップ、LINE 未設定や送信失敗は非致命（`|| true` / `continue-on-error`）。

## 9. データ契約

スキーマは `04_data_contracts.md` の `pool_candidates_*.json`（AI 出力）と `pool_decision_*.json`（決定論監査）を正とします。両者とも publish 除外済みの `docs/curation/` 配下です。

## 10. 段階導入

`06_rollout_risks.md` を参照。phase 1 は add-only（`max_drops_per_run: 0`）で `pool_target_size: 60` まで grow し、数サイクル観測して信頼できたら drop を有効化します（target 到達後は replace-only へ自動切替）。
