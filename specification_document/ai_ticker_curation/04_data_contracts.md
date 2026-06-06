# データ契約とファイルスキーマ

作成日: 2026-06-06 JST

すべての日付は JST。新規ファイルは UTF-8 / `ensure_ascii=False`（既存スクリプトに整合）。各成果物は `*_latest.json`（最新）と `*_YYYY-MM-DD.json`（日付版・監査）を併せて出力する。

## 1. ディレクトリ構成（新規）

```
docs/curation/
  fundamental_latest.json      # ①ファンダ出力（週次更新＝日次マージのキャッシュ）
  fundamental_YYYY-MM-DD.json
  technical_features.json      # technical_screen.py が生成（テクニカルの数値入力）
  technical_latest.json        # ②テクニカル出力（日次更新）
  technical_YYYY-MM-DD.json
  decision_latest.json         # ③マージ決定（監査・日次）
  decision_YYYY-MM-DD.json
reports/                       # ★docs/外。publishのrsync --delete 対象外。GitHub blob URLで通知
  weekly_latest.md             # ⑥週次総合レポート（女の子ナビ解説）
  weekly_YYYY-MM-DD.md
data/watchlist/                # gitignore（非コミット）。毎回 update_data がフル履歴を再取得
  {code}.parquet               # 候補のwarmupデータ（sync_data_files の退避対象外）
curation_pool.yml              # テクニカルが日次スクリーニングする候補プール
```

> **配置の使い分け**: `tickers.yml` 更新の作業物は `docs/curation/`、人が読む週次レポートは **`docs/` 外の `reports/`**。後者は publish の `rsync --delete web/out/ docs/` の影響を受けず、`https://github.com/<owner>/<repo>/blob/main/reports/weekly_*.md` で安定的に参照できる。

## 2. `tickers.yml` 拡張（後方互換）

`load_tickers()` は `tickers` と `settings.max_tickers` のみ参照するため、`watchlist` と `settings.curation` を**追加しても既存処理は無影響**。

```yaml
tickers:
  - code: "7011.JP"
    name: "三菱重工業"
    enabled: true
    # 以下はキュレーション用の任意メタ（既存処理は無視）
    source: "curation"        # manual | curation
    added_on: "2026-06-06"
    combined: 81.5

watchlist:                    # 新規・任意。マージが管理。enabled昇格の控え
  - code: "6920.JP"
    name: "レーザーテック"
    fund_score: 72
    tech_score: 80
    combined: 76.0
    status: "warming"         # warming | ready
    rows_available: 140
    added_on: "2026-06-04"

settings:
  max_tickers: null           # 既存。enabled処理数の上限（null=無制限）。キュレーションは別途 max_universe を使用
  curation:                   # 新規・任意
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
    max_fundamental_age_days: 14   # ファンダ・キャッシュ許容鮮度（超過で新規昇格停止）
    report:
      persona_name: "あおい"        # 週次レポートの女の子ナビ名（任意・変更可）
      tone: "casual_kawaii"         # 「〜だね！」系のカジュアル文体（05参照）
```

> `max_tickers` と `max_universe` の関係: `max_tickers` を非nullにすると `load_tickers()` が enabled を先頭から切り詰めるため、キュレーションのユニバース管理と二重制御になり混乱しうる。**`max_tickers: null` を維持**し、上限は `curation.max_universe` 側で一元管理することを推奨。

## 3. `curation_pool.yml`（テクニカル候補プール）

流動性のある日本株（例: Nikkei225/TOPIX Coreのサブセット、既定30〜60銘柄）。テクニカル・エージェントの日次スクリーニング対象。週次（既存の universe-refresh 枠）で見直す。

```yaml
pool:
  - code: "7011.JP"
    name: "三菱重工業"
    sector: "機械"
  - code: "6920.JP"
    name: "レーザーテック"
    sector: "半導体製造装置"
  # ... 30〜60銘柄
```

## 4. ① ファンダ出力 `fundamental_latest.json`（週次更新／日次マージのキャッシュ）

> 週次（土曜）に更新され、翌週の平日マージはこのファイルの `fund_score` をキャッシュとして参照する。マージ側は `as_of` で鮮度を確認し、`max_fundamental_age_days` 超過なら新規昇格を停止する（`02`§2/§6）。

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
        "earnings": 26, "guidance": 17, "valuation": 12,
        "balance_sheet": 13, "shareholder_return": 8,
        "catalyst": 5, "risk_penalty": -3
      },
      "thesis": "2026-05-12発表のFY受注がYoY+xx%、上方修正。EUV検査の…（日付と数値で）",
      "sources": [
        {"title": "決算short", "url": "https://...", "date": "2026-05-12", "type": "primary"}
      ],
      "confidence": "high"
    }
  ],
  "universe_reviewed": ["7011.JP", "6501.JP", "..."],
  "notes": "週次フル採点。当週の新規開示・決算を反映",
  "limitations": "将来の値動きを保証しない。データは急変しうる。"
}
```

必須: `schema_version`,`agent`,`generated_at`,`as_of`,`candidates[].code/name/score`。`score` は 0-100。出典なしの候補は不可。

## 5. ② テクニカル出力 `technical_latest.json`

```json
{
  "schema_version": 1,
  "agent": "technical",
  "model": "claude-sonnet-4-6",
  "generated_at": "2026-06-06T04:35:40+09:00",
  "as_of": "2026-06-06",
  "data_through": "2026-06-05",
  "candidates": [
    {
      "code": "6920.JP",
      "name": "レーザーテック",
      "score": 80,
      "signals": {
        "trend": "up", "ma_stack": "MA25>MA50>MA200",
        "rsi14": 61.2, "macd": "bull_cross",
        "rs_vs_topix_20d": 4.2, "atr_pct": 2.1,
        "vol_zscore": 1.4, "breakout_20d": true,
        "ret_20d": 7.8
      },
      "horizon_days": 5,
      "rationale": "MA好配列＋出来高増ブレイク。RSI過熱手前。",
      "rows_available": 740,
      "warmup_ok": true
    }
  ],
  "universe_evaluated": ["（curation_pool + enabled + watchlist）"],
  "notes": ""
}
```

必須: `candidates[].code/name/score/rows_available/warmup_ok`。`warmup_ok=false`(=`rows_available < min_warmup_rows`)はマージで昇格抑止。

## 6. テクニカル特徴量 `technical_features.json`（中間生成物）

`scripts/technical_screen.py` が `add_features()` を用いて算出。エージェントの数値入力。

```json
{
  "generated_at": "2026-06-06T04:34:00+09:00",
  "data_through": "2026-06-05",
  "min_warmup_rows": 200,
  "entries": [
    {
      "code": "6920.JP", "name": "レーザーテック", "rows": 740,
      "close": 21340.0, "ret_5d": 3.1, "ret_20d": 7.8,
      "ma25": 20100, "ma50": 19500, "ma200": 17800,
      "rsi14": 61.2, "macd_hist": 35.2, "atr_pct": 2.1,
      "vol_zscore": 1.4, "high_20d": 21500, "high_60d": 21800
    }
  ]
}
```

## 7. ③ 決定監査ログ `decision_latest.json`

```json
{
  "schema_version": 1,
  "date": "2026-06-06",
  "as_of": "2026-06-06",
  "applied": true,
  "inputs": {
    "fundamental": "docs/curation/fundamental_2026-06-06.json",
    "technical": "docs/curation/technical_2026-06-06.json"
  },
  "weights": {"fund": 0.5, "tech": 0.5},
  "ranking": [
    {
      "code": "6920.JP", "name": "レーザーテック",
      "fund_score": 84, "tech_score": 80, "combined": 82.0,
      "in_universe_before": false, "warmup_ok": true,
      "action": "promote",
      "reasons": ["combined>=70", "warmup_ok", "beats_min_enabled+gap"]
    },
    {
      "code": "1802.JP", "name": "大林組",
      "fund_score": 41, "tech_score": 47, "combined": 44.0,
      "in_universe_before": true, "action": "demote",
      "reasons": ["below_keep_floor", "paired_with_promote"]
    }
  ],
  "changes": {
    "promoted": ["6920.JP"], "demoted": ["1802.JP"],
    "added_to_watchlist": ["6146.JP"], "removed_from_watchlist": []
  },
  "guardrails": {
    "max_daily_swaps": 2, "applied_swaps": 1, "applied_adds": 0,
    "max_universe": 10, "sector_caps_ok": true,
    "schema_valid": true, "jpx_open": true, "fail_safe_triggered": false
  },
  "data_moves": [
    {"from": "data/watchlist/6920.JP.parquet", "to": "data/6920.JP.parquet"}
  ],
  "universe_before": ["7011.JP", "...", "1802.JP"],
  "universe_after":  ["7011.JP", "...", "6920.JP"]
}
```

## 7-B. 週次レポート `reports/weekly_YYYY-MM-DD.md`（契約の要点）

- 生成: レポートライター・エージェント（週次）。入力は `fundamental_latest.json` + `technical_latest.json` + 直近 `decision_*.json`。
- 配置: **`docs/` 外の `reports/`**（publish rsync 対象外）。`reports/weekly_latest.md` も併記。
- 形式: Markdown。先頭にフロントマター（`date`,`as_of`,`persona`,`disclaimer`）。本文は女の子ナビのカジュアル解説（「〜だね！」）。
- 必須要素: 今週の注目銘柄（ファンダ＋テクニカルの根拠・数値・日付）、ユニバースの入替、地合い、まとめ、**免責**。
- 構成・文体規約・サンプル・LINE文面は **`05_weekly_report.md`** を正とする。

## 8. 既存コードとの統合制約（再掲・遵守必須）

| 制約 | 影響 | 対応 |
|---|---|---|
| `sync_data_files()` はトップレベル `data/*.parquet` のみ走査しenabled外を `data/archive/` へ退避 | warmupをトップレベルに置くと `main.py` 実行で退避される | warmupは `data/watchlist/`（サブディレクトリ）に置く。昇格時のみトップレベルへ移動 |
| `update_data()` の出力先はトップレベル固定 | warmupに直接使えない | 出力先指定可能にするか `warmup_candidate()` を新設（ロジックは流用） |
| `main.py` は MA60 で最低60行、KPIゲートは `TRADER_BT_MIN_TRAIN_ROWS`(既定200) を要求 | 履歴不足銘柄は強制HOLD | `min_warmup_rows`(既定200) を昇格条件にしてコールドスタート回避 |
| `load_tickers()` は `tickers`/`settings.max_tickers` のみ参照 | `watchlist`/`settings.curation` 追加は安全 | スキーマ拡張は後方互換 |
| 既存 `daily-publish-dashboard.yml` は `web/out/` を `docs/` へ `rsync --delete`（一部JSONを除外） | `docs/curation/` が publish の rsync で削除される懸念 | publish の rsync に `--exclude 'curation'` を追加（実装時必須・`06`の残課題）。**週次レポートは `docs/` 外の `reports/` に置くため影響なし** |
| 週次レポートのGitHub URL | LINE通知に安定URLが必要 | `reports/`(docs外)＋ `https://github.com/<owner>/<repo>/blob/main/reports/weekly_<DATE>.md`。slugは `TRADER_REPO_SLUG` or `git remote` 由来 |

## 9. フロントエンド連携（任意）

`docs/curation/decision_latest.json` を読めば、ダッシュボードに「本日の入替（追加/除外と根拠）」「watchlist」を表示可能。スコープ外だが、`web/src/types/index.ts` に型追加＋カード1枚で実現できる軽微拡張（`06`に任意項目として記載）。
