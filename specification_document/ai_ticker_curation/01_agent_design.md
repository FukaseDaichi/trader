# エージェント設計（テクニカル / ファンダメンタル / レポートライター）

更新日: 2026-06-06 JST

エージェントは 4 体です。すべて `claude-code-action@v1` で起動します。共通の鉄則は「`tickers.yml` を編集しない」「git 操作をしない」「所定パスの成果物だけを書く」です。ユニバース変更は `scripts/curation_merge.py` が担います。

## 1. 役割・頻度

| 項目 | テクニカル | マクロ | ファンダメンタル | レポートライター |
|---|---|---|---|---|
| 頻度 | 平日 04:30 JST | 土曜 07:00 JST、ファンダ前 | 土曜 07:00 JST | 土曜 07:00 JST、ファンダ後 |
| 目的 | 価格/出来高トレンドで候補採点 | 金利・金融政策・為替レジームを調査 | 業績/ガイダンス/財務/株主還元で候補採点 | 週次解説 Markdown 生成 |
| 主入力 | `technical_screen.py` の数値 | Web の一次/公式情報、`curation_pool.yml`, `tickers.yml` | Web の一次情報、`macro_latest.json`、`tickers.yml`, `curation_pool.yml` | `fundamental_latest.json`, `technical_latest.json`, `macro_latest.json`, `decision_*.json` |
| モデル | `claude-sonnet-4-6` | `claude-opus-4-8` | `claude-opus-4-8` | `claude-sonnet-4-6` |
| skill | `.claude/skills/jp-stock-technical-screen/` | `.claude/skills/global-macro-screen/` | `.claude/skills/jp-stock-fundamental-screen/` | `.claude/skills/weekly-stock-report/` |
| 出力 | `docs/curation/technical_latest.json` | `docs/curation/macro_latest.json`, `macro_<DATE>.json` | `docs/curation/fundamental_latest.json`, `fundamental_<DATE>.json` | `reports/weekly_<DATE>.md`, `weekly_latest.md` |

## 2. テクニカル・エージェント

### 実行前 baseline

workflow は agent の前に必ず以下を実行します。

```bash
uv run python scripts/technical_screen.py --pool curation_pool.yml --date <YYYY-MM-DD>
```

このスクリプトは `src.model.add_features(dropna=False)` を使い、以下を生成します。

- `docs/curation/technical_features.json`
- `docs/curation/technical_latest.json`
- `docs/curation/technical_<DATE>.json`

agent は baseline を精査して `technical_latest.json` を必要に応じて更新します。agent が失敗しても baseline が残るため、merge は継続できます。

### skill の要点

- 出力は `docs/curation/technical_latest.json` のみ
- `code`, `name`, `rows_available`, `warmup_ok` は入力値を維持
- 採点材料は MA5/20/60/200、5/20/60日リターン、RSI、MACD、ATR%、出来高比率、20日高値位置など
- Web 検索は禁止
- `tickers.yml`、`data/`、`src/`、`web/`、`.github/` は編集禁止

## 3. マクロ・エージェント

### skill の要点

- 出力は `docs/curation/macro_latest.json` と `docs/curation/macro_<as_of>.json` のみ
- 金利・金融政策・為替（Fed/FOMC、日銀、ドル円）を主軸に世界情勢を一次/公式情報で調査
- 各テーマに JP株向けの `stance`（tailwind/neutral/headwind）、`affected_sectors`（`curation_pool.yml` の正規セクター名）、`affected_codes`（`NNNN.JP`）を付与
- すべてのテーマに絶対日付付きの一次/公式ソースを要求
- `tickers.yml` は編集禁止

### 位置づけ

- 週次 workflow でファンダ agent の**前**に実行する。
- ファンダ agent はこれを読み、前向き（2週間以降）の `catalyst`/`risk_penalty`/`thesis` を tilt する（スコア体系・`schema_version` は不変）。
- レポートライターはこれを読み「🌍 世界の動き（金利・為替）」節を書く。
- **`curation_merge.py` は `macro_latest.json` を参照しない。** ユニバース昇格判定はテク+ファンダの合成スコアのみで決まる。

## 4. ファンダメンタル・エージェント

### skill の要点

- 出力は `docs/curation/fundamental_latest.json` と `docs/curation/fundamental_<as_of>.json`
- すべての selected candidate に、直近約 90 日以内の一次情報を少なくとも 1 つ要求
- `references/selection-framework.md` の 100 点評価を使用
- `as_of` は merge の鮮度判定で使うため必須
- `tickers.yml` は編集禁止

### 採点軸

- 業績モメンタムと質: 30
- ガイダンスと上方修正トレンド: 20
- バリュエーション再評価余地: 15
- バランスシートとキャッシュ創出: 15
- 株主還元とガバナンス: 10
- カタリスト: 5
- リスク/流動性ペナルティ: -5

`score >= 70` を候補、`score >= 80` を高確信の目安にします。ただし最終昇格は `curation_merge.py` の guardrail が決めます。

## 5. レポートライター・エージェント

### skill の要点

- 出力は `reports/weekly_<as_of>.md` と `reports/weekly_latest.md`
- `docs/` ではなくリポジトリ直下の `reports/` に書く
- 入力 JSON に存在しない銘柄、数値、出典は作らない
- カジュアルな女の子ナビ文体だが、数値・日付・固有名詞は正確に保つ
- 末尾に投資助言ではない旨の免責を必ず入れる

レポートの構成と文体は `05_weekly_report.md` を正とします。

## 6. workflow上の安全規約

workflow の `claude_args` で `git commit`、`git push`、`rm` などを抑止しています。さらに構造上、agent 後の不可逆変更は `curation_merge.py` と `.github/scripts/commit-and-push.sh` に限定されています。

agent step は `continue-on-error: true` です。テクニカル agent が失敗しても baseline が残り、ファンダ/レポート agent が失敗しても commit helper は差分なしなら正常終了します。
