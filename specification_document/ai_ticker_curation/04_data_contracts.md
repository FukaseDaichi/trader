# データ契約とファイルスキーマ

更新日: 2026-06-16 JST

AI 銘柄キュレーションの成果物は UTF-8 / `ensure_ascii=False` の JSON または Markdown です。日付は原則 JST の絶対日付を使います。

## 1. ディレクトリ構成

```text
docs/curation/
  warmup_report.json
  technical_features.json
  technical_latest.json
  technical_YYYY-MM-DD.json
  fundamental_latest.json
  fundamental_YYYY-MM-DD.json
  macro_latest.json
  macro_YYYY-MM-DD.json
  decision_latest.json
  decision_YYYY-MM-DD.json
  pool_candidates_latest.json
  pool_candidates_YYYY-MM-DD.json
  pool_decision_latest.json
  pool_decision_YYYY-MM-DD.json

reports/
  weekly_latest.md
  weekly_YYYY-MM-DD.md

data/watchlist/
  {code}.parquet

curation_pool.yml
```

`docs/curation/` と `reports/` は workflow 実行後に作られるため、未実行の checkout では存在しない場合があります。`data/watchlist/` は `.gitignore` 対象です。

## 2. `tickers.yml` 拡張

```yaml
tickers:
  - code: "7011.JP"
    name: "三菱重工業"
    enabled: true
    source: "manual"
    sector: "機械・重工"
    added_on: "2026-06-06"
    disabled_on: "2026-06-10"
    combined: 81.5
    tech_score: 80
    fund_score: 83

watchlist:
  - code: "6920.JP"
    name: "レーザーテック"
    sector: "半導体製造装置"
    fund_score: 72
    tech_score: 80
    combined: 76.0
    status: "warming"
    rows_available: 140
    added_on: "2026-06-04"

settings:
  max_tickers: null
  curation:
    enabled: true
    max_universe: 10
    max_daily_swaps: 2
    max_daily_adds: 2
    min_combined_to_promote: 70
    min_gap: 5
    keep_floor: 50
    min_warmup_rows: 200
    sector_cap_pct: 40
    fund_weight: 0.5
    tech_weight: 0.5
    cooldown_days: 5
    max_fundamental_age_days: 14
    report:
      persona_name: "あおい"
      tone: "casual_kawaii"
    pool:
      enabled: true
      pool_target_size: 60
      pool_max_size: 80
      cadence_days: 14
      max_adds_per_run: 3
      max_drops_per_run: 0
      min_fund_score_to_add: 70
      liquidity_floor_jpy: 1000000000
      pool_sector_cap_pct: 40
      pool_cooldown_days: 30
```

`load_tickers()` は `tickers` と `settings.max_tickers` を検証・利用します。`watchlist` と `settings.curation` は `scripts/curation_*` 用です。`settings.curation.pool` は隔週プールリフレッシュ用で、未設定時は `curation_pool_merge.py` のコードレベル既定（上記値）を使います。

## 3. `curation_pool.yml`

```yaml
pool:
  - code: "7011.JP"
    name: "三菱重工業"
    sector: "機械・重工"
```

`technical_screen.py` は pool + enabled + watchlist の union を評価します。`curation_pool.yml` を書き換えてよいのは決定論 `curation_pool_merge.py` のみです（隔週、`07_pool_refresh.md`）。

## 4. `warmup_report.json`

```json
{
  "generated_at": "2026-06-06T04:32:00+09:00",
  "out_dir": "data/watchlist",
  "targets": 30,
  "succeeded": 28,
  "entries": [
    {"code": "7203.JP", "rows": 5000, "latest_date": "2026-06-05"}
  ]
}
```

## 5. `technical_features.json`

`scripts/technical_screen.py` の中間生成物です。

```json
{
  "generated_at": "2026-06-06T04:34:00+09:00",
  "data_through": "2026-06-05",
  "min_warmup_rows": 200,
  "entries": [
    {
      "code": "6920.JP",
      "name": "レーザーテック",
      "sector": "半導体製造装置",
      "rows": 740,
      "data_through": "2026-06-05",
      "close": 21340.0,
      "ret_5d": 0.031,
      "ret_20d": 0.078,
      "ret_60d": 0.12,
      "ma_5": 21000.0,
      "ma_20": 20100.0,
      "ma_60": 19500.0,
      "ma_200": 17800.0,
      "div_ma_60": 0.094,
      "rsi14": 61.2,
      "macd_hist": 35.2,
      "macd_hist_change": 3.1,
      "atr_pct": 0.021,
      "vol_ratio": 1.4,
      "high_20d": 21500.0,
      "high_60d": 21800.0,
      "price_position_20d": 0.93
    }
  ]
}
```

## 6. `technical_latest.json`

```json
{
  "schema_version": 1,
  "agent": "technical",
  "model": "deterministic-baseline",
  "generated_at": "2026-06-06T04:35:40+09:00",
  "as_of": "2026-06-06",
  "data_through": "2026-06-05",
  "candidates": [
    {
      "code": "6920.JP",
      "name": "レーザーテック",
      "sector": "半導体製造装置",
      "score": 80.0,
      "signals": {
        "trend": "up",
        "ma_stack": "MA5>MA20>MA60",
        "rsi14": 61.2,
        "macd": "bull",
        "atr_pct": 0.021,
        "vol_ratio": 1.4,
        "breakout_20d": true,
        "ret_20d": 0.078,
        "ret_5d": 0.031
      },
      "horizon_days": 5,
      "rationale": "上昇トレンド・MACD強気・RSI61",
      "rows_available": 740,
      "warmup_ok": true
    }
  ],
  "universe_evaluated": ["6920.JP"],
  "notes": "Deterministic baseline from technical_screen.py. May be refined by the technical agent."
}
```

`model` は baseline では `deterministic-baseline`、agent 精査後は `claude-sonnet-4-6` になり得ます。

## 7. `fundamental_latest.json`

Claude fundamental agent が生成します。

```json
{
  "schema_version": 1,
  "agent": "fundamental",
  "model": "claude-opus-4-8",
  "cadence": "weekly",
  "generated_at": "2026-06-06T07:20:10+09:00",
  "as_of": "2026-06-06",
  "candidates": [
    {
      "code": "6920.JP",
      "name": "レーザーテック",
      "sector": "半導体製造装置",
      "score": 84,
      "subscores": {
        "earnings": 26,
        "guidance": 17,
        "valuation": 12,
        "balance_sheet": 13,
        "shareholder_return": 8,
        "catalyst": 5,
        "risk_penalty": -3
      },
      "thesis": "日付と数値を含む簡潔な根拠",
      "sources": [
        {"title": "決算短信", "url": "https://...", "date": "2026-05-12", "type": "primary"}
      ],
      "confidence": "high"
    }
  ],
  "universe_reviewed": ["7011.JP"],
  "notes": "週次フル採点",
  "limitations": "将来の値動きを保証しない。データは急変しうる。"
}
```

必須: `schema_version`, `agent`, `generated_at`, `as_of`, `candidates[].code/name/score`。merge は `score`、`sector`、`as_of` を使います。

## 8. `macro_latest.json`

Claude global-macro agent が生成します（週次）。`curation_merge.py` は参照しません。消費者はファンダ agent・レポートライターに加え、コアパイプライン側の `update_macro_snapshots.py`（qualitative bias）、日次ダイジェスト（`market_bias` 表示）、Phase 2 ポートフォリオのリスクブレーキ（`main.py` `_load_portfolio_regime()`、`market_bias=risk_off` でグロス半減。2026-06-11〜）です。

```json
{
  "schema_version": 1,
  "agent": "macro",
  "model": "claude-opus-4-8",
  "cadence": "weekly",
  "generated_at": "2026-06-06T07:05:00+09:00",
  "as_of": "2026-06-06",
  "market_bias": "neutral",
  "summary": "金利・為替レジームの総括とJP株への含意",
  "regime": {
    "fed":    {"policy_rate_pct": 4.5, "stance": "neutral", "next_event": "2026-06-17", "note": "…"},
    "boj":    {"policy_rate_pct": 0.5, "stance": "hawkish", "next_event": "2026-06-13", "note": "…"},
    "usdjpy": {"level": 152.3, "trend": "円安", "as_of": "2026-06-05", "note": "…"}
  },
  "themes": [
    {
      "id": "boj-hike-path",
      "title": "日銀の追加利上げ観測",
      "category": "monetary",
      "stance": "tailwind",
      "confidence": "medium",
      "latest_event": {"date": "2026-06-03", "description": "主な意見で利上げに前向きな発言"},
      "affected_sectors": ["銀行", "保険"],
      "affected_codes": ["8306.JP", "8766.JP"],
      "rationale": "利ざや改善期待。2週間以降のガイダンスで意識されやすい",
      "sources": [{"title": "日銀 主な意見", "url": "https://www.boj.or.jp/...", "date": "2026-06-03", "type": "primary"}]
    }
  ],
  "universe_considered": ["8306.JP", "7203.JP"],
  "notes": "金利・為替を主軸に採点",
  "limitations": "マクロ予測は不確実。将来の値動きを保証しない。データは急変しうる。"
}
```

`category` ∈ {`monetary`, `fx`, `other`}。`themes[].stance`（`tailwind`/`neutral`/`headwind`, JP株向け）・`affected_sectors`（`curation_pool.yml` の正規セクター名）・`affected_codes`（`NNNN.JP`）が銘柄紐付けの結合キーです。`regime.*.stance` は中銀の政策スタンス（hawkish/neutral/dovish）で別物。**merge は本ファイルを読みません。**

## 9. `decision_latest.json`

```json
{
  "schema_version": 1,
  "date": "2026-06-06",
  "as_of": "2026-06-06",
  "applied": true,
  "tickers_written": true,
  "inputs": {
    "technical": "docs/curation/technical_latest.json",
    "fundamental": "fundamental_latest.json"
  },
  "weights": {"tech": 0.5, "fund": 0.5},
  "fundamental_age_days": 0,
  "conservative_mode": false,
  "ranking": [
    {
      "code": "6920.JP",
      "name": "レーザーテック",
      "sector": "半導体製造装置",
      "tech_score": 80,
      "fund_score": 84,
      "combined": 82.0,
      "warmup_ok": true,
      "in_universe_before": false,
      "action": "promote"
    }
  ],
  "changes": {
    "promoted": ["6920.JP"],
    "promoted_add": ["6920.JP"],
    "promoted_swap": [],
    "demoted": [],
    "watchlist": ["6146.JP"]
  },
  "guardrails": {
    "max_universe": 10,
    "max_daily_swaps": 2,
    "max_daily_adds": 2,
    "applied_swaps": 0,
    "applied_adds": 1,
    "conservative_mode": false,
    "fundamental_age_days": 0,
    "has_technical": true,
    "sector_cap_pct": 40
  },
  "data_moves": [
    {"from": "data/watchlist/6920.JP.parquet", "to": "data/6920.JP.parquet"}
  ],
  "universe_before": ["7011.JP"],
  "universe_after": ["7011.JP", "6920.JP"],
  "generated_at": "2026-06-06T04:45:00+09:00"
}
```

## 10. 週次レポート

`reports/weekly_YYYY-MM-DD.md` と `reports/weekly_latest.md` は同じ内容です。

必須:

- YAML front matter: `date`, `as_of`, `persona`, `disclaimer`
- 今週の注目銘柄
- ユニバースの動き
- 地合い
- まとめ
- 投資助言ではない旨の免責

詳細は `05_weekly_report.md` を正とします。

## 11. 統合制約

| 制約 | 対応 |
|---|---|
| `sync_data_files()` はトップレベル `data/*.parquet` のみ退避 | warmup は `data/watchlist/` に保存 |
| `main.py` は MA60 で最低 60 行、KPI gate は既定 200 行を要求 | `min_warmup_rows=200` を昇格条件にする |
| `daily-publish-dashboard.yml` は `docs/` を `rsync --delete` | `docs/curation/` を exclude 済み、レポートは `reports/` |
| `load_tickers()` は `watchlist` を日次予測対象にしない | watchlist は curation 専用 |
| `curation_pool.yml` の書き手を 1 つに限定 | 隔週の決定論 `curation_pool_merge.py` のみ |

## 12. `pool_candidates_latest.json`（プール agent 出力）

Claude pool agent が生成します（隔週）。`curation_pool_merge.py` への提案のみで、`curation_pool.yml` は書きません。

```json
{
  "schema_version": 1,
  "agent": "pool",
  "model": "claude-sonnet-4-6",
  "generated_at": "2026-06-16T07:40:00+09:00",
  "as_of": "2026-06-16",
  "candidates": [
    {
      "code": "NNNN.JP",
      "name": "...",
      "sector": "...",
      "action_hint": "add",
      "fund_score": 0,
      "liquidity_jpy": 0,
      "rationale": "日付・数値を含む一次情報由来の根拠",
      "sources": [{"title": "...", "url": "https://...", "date": "YYYY-MM-DD", "type": "primary"}]
    }
  ],
  "universe_reviewed": ["NNNN.JP"],
  "notes": "...",
  "limitations": "候補プールのスクリーンであり投資助言ではない。"
}
```

`action_hint` ∈ {`add`, `drop`, `keep`}。`liquidity_jpy` は参考値で、guardrail はローカル parquet 実測の中央値売買代金を権威にします。

## 13. `pool_decision_latest.json`（決定論監査）

`decision_latest.json` に対応するプール版です。`curation_pool_merge.py` が `pool_decision_latest.json` と `pool_decision_YYYY-MM-DD.json` に書きます。

```json
{
  "schema_version": 1,
  "date": "2026-06-16",
  "as_of": "2026-06-16",
  "applied": true,
  "proposal_valid": true,
  "pool_written": true,
  "mode": "grow",
  "inputs": {"proposal": "docs/curation/pool_candidates_latest.json"},
  "ranking": [
    {
      "code": "NNNN.JP",
      "name": "...",
      "sector": "...",
      "fund_score": 82,
      "liquidity_jpy": 3500000000,
      "proposal_liquidity_jpy": 3400000000,
      "action_hint": "add",
      "in_pool_before": false,
      "enabled": false,
      "action": "add",
      "reason": "accepted"
    }
  ],
  "changes": {"added": ["NNNN.JP"], "dropped": []},
  "guardrails": {
    "pool_target_size": 60, "pool_max_size": 80,
    "max_adds_per_run": 3, "max_drops_per_run": 0,
    "min_fund_score_to_add": 70, "liquidity_floor_jpy": 1000000000,
    "pool_sector_cap_pct": 40, "pool_cooldown_days": 30, "cadence_days": 14
  },
  "pool_before": ["..."],
  "pool_after": ["...", "NNNN.JP"],
  "fetched_missing_parquets": [{"code": "NNNN.JP", "status": "ok", "rows": 5000}],
  "cleanup": {
    "warmup_files_removed": [], "warmup_files_would_remove": [],
    "warmup_bytes_removed": 0, "errors": []
  },
  "generated_at": "2026-06-16T07:45:00+09:00"
}
```

`mode` ∈ {`grow`, `replace`, `noop`}。`ranking[].action` ∈ {`add`, `drop`, `keep`, `reject`}、`reason` は発火したガード（`accepted` / `liquidity_below_floor` / `missing_local_liquidity` / `fund_score_below_floor` / `sector_cap` / `pool_cooldown` / `add_limit_reached` / `replace_mode_drops_disabled` / `enabled_protected` 等）。提案欠落・不正時は `proposal_valid:false`・`mode:"noop"` で母集団は不変。
