# エージェント設計（テクニカル / ファンダメンタル / レポートライター）

作成日: 2026-06-06 JST ／ 改訂: rev.2

エージェントは3体。すべて `claude-code-action@v1` で起動する。共通の鉄則は **「`tickers.yml` を絶対に編集しない」「出力は所定パスのファイルのみ」**。ユニバース変更は決定論マージ(`02`)が担う。

## 1. 役割・頻度の一覧

| 項目 | ② テクニカル | ① ファンダメンタル | ⑥ レポートライター |
|---|---|---|---|
| 頻度 | **日次（平日）** | **週次（土曜）** | **週次（土曜・①の直後）** |
| 目的 | 価格/出来高トレンドで候補採点 | 業績/ガイダンス/財務で候補採点 | ①②を総合した解説レポート生成 |
| 主入力 | ローカル価格データ→指標 | Web（IR/TDnet/EDINET/JPX/開示） | `fundamental_latest.json`+`technical_latest.json`+直近`decision_*` |
| 推奨モデル | Sonnet 4.6 (`claude-sonnet-4-6`) | Opus 4.8 (`claude-opus-4-8`) | Sonnet 4.6 (`claude-sonnet-4-6`) |
| 主ツール | `Read,Write,Glob,Grep,Bash(指標計算)`（Web禁止） | `WebSearch,WebFetch,Read,Write,Glob,Grep,Bash` | `Read,Write`（Web/Bash不要） |
| skill | `.claude/skills/jp-stock-technical-screen/` | `.claude/skills/jp-stock-fundamental-screen/` | `.claude/skills/weekly-stock-report/` |
| 出力 | `docs/curation/technical_latest.json` | `docs/curation/fundamental_latest.json` | `reports/weekly_YYYY-MM-DD.md` |

> 日次マージは「当日のテクニカル」＋「直近週のファンダ（キャッシュ）」で合成する（`02`）。ファンダは週1更新でも、業績は日次で動かないため実用上十分。

## 2. ② テクニカル・エージェント（日次）

### 2.1 設計思想：決定論計算 + LLM判断のハイブリッド
指標計算は決定論であるべき。エージェントは **`Bash` で計算スクリプト実行 → 数値読取 → 順位付け・根拠生成 → JSON出力** の流れ。

新規 `scripts/technical_screen.py`（実装対象）:
- 入力: 候補プール `curation_pool.yml`（流動性ある日本株、既定30〜60銘柄）+ 既存enabled + watchlist。
- 各銘柄で `data/` / `data/watchlist/` の parquet を読み、既存 `src/model.add_features()`（35指標）を流用して指標算出。
- `docs/curation/technical_features.json`（中間生成物。エージェントの数値入力）を出力。

### 2.2 skill 手順（`.claude/skills/jp-stock-technical-screen/SKILL.md`）
1. `uv run python scripts/technical_screen.py --pool curation_pool.yml --out docs/curation/technical_features.json` を実行。
2. 生成JSONを `Read`。
3. 各銘柄を 0-100 採点（トレンド/MA配列/モメンタム/出来高/相対力/ブレイクの重み付け。重みはskillに明記）。
4. 上位の短い根拠を付け `docs/curation/technical_latest.json` を `Write`。
5. **`tickers.yml` 非編集**を厳守。

### 2.3 出力契約
`technical_latest.json`（スキーマは `04`）。各候補 `code/name/score/signals/horizon_days/rationale/rows_available/warmup_ok`。`warmup_ok=false`（行数<`min_warmup_rows`）はマージで昇格抑止。

## 3. ① ファンダメンタル・エージェント（週次）

### 3.1 skill 配置と既存資産の再利用
既存 `skills/jp-stock-ticker-curation/`（`SKILL.md`+`references/selection-framework.md`）を**資産として再利用**し、出力契約のみ変更:

- 新skill `.claude/skills/jp-stock-fundamental-screen/SKILL.md` を作成（`/skill-name` 起動は `.claude/skills/` 配下が対象）。
- スコアは既存 `selection-framework.md`（100点：業績30/ガイダンス20/バリュ15/財務15/株主還元10/カタリスト5/リスク減点5）を流用（`references/` に複製 or 相対参照）。
- **重要差分**: 既存skillは `tickers.yml` を直接編集するが、新skillは**「`tickers.yml` 非編集。出力は `docs/curation/fundamental_latest.json` のみ」**を明記。

### 3.2 週次処理（deep相当）
- 既存enabled + watchlist + 流動性候補を longlist 化し、フレームワークでフル採点。
- ハードフィルタ（直近90日以内の一次情報1件以上、財務危機/会計懸念の除外）を適用。
- 出典(一次情報URL+日付)・数値・日付を必須。テーマ単独推奨は禁止。

### 3.3 出力契約
`fundamental_latest.json`（スキーマは `04`）。各候補 `code/name/sector/score/subscores/thesis/sources/confidence`。`score>=70` 採用候補、`>=80` 高確信。この週次ファイルが翌週の日次マージのファンダ入力（キャッシュ）になる。

## 4. ⑥ レポートライター・エージェント（週次）

### 4.1 役割
構造化された①②の結果を、**人が読んで楽しいカジュアル解説**（女の子ナビ／「〜だね！」）の Markdown に変換する。分析と“読み物”を分離し、ペルソナを1箇所に集約。

### 4.2 入力 → 出力
- 入力: `docs/curation/fundamental_latest.json`, `docs/curation/technical_latest.json`, 直近1週間の `docs/curation/decision_*.json`（入替の経緯）。
- 出力: `reports/weekly_YYYY-MM-DD.md`（＋ `reports/weekly_latest.md`）。**`docs/` 外の `reports/`** に置く（publishのrsync衝突回避）。

### 4.3 skill 手順（`.claude/skills/weekly-stock-report/SKILL.md`）
1. 上記JSONを `Read`。
2. ペルソナ・文体規約（`05` に定義）に従い、構成（あいさつ→今週の注目銘柄→ユニバースの変化→地合い→まとめ→免責）でMarkdownを `Write`。
3. **数値・日付・出典は正確に**（カジュアルでも事実は崩さない）。投資助言ではない旨の免責を必ず入れる。
4. `tickers.yml` 等は非編集。

> 文体・構成・サンプルは `05_weekly_report.md` を参照。

## 5. クォータ配慮（サブスク範囲）

- 起動回数: テクニカル=平日(~250/年)、ファンダ=週次(~52/年)、レポート=週次(~52/年)。**Opus消費は週1のファンダのみ**に限定。
- `--max-turns` 上限: テクニカル15-20 / ファンダ(週次)40 / レポート10-15。
- レート制限時は**ユニバース変更なし＝現状維持**（`02`§6）。レポートは翌週分でリカバリ。
- 既存 `main.py`（予測本体）はサブスク非消費。サブスク消費は本キュレーションのエージェント起動に限定。

## 6. 共通の安全規約（全エージェント）

- 出力先は所定パスのみ（テク/ファンダ=`docs/curation/*.json`、レポート=`reports/*.md`）。`tickers.yml`,`data/`,`web/`,`src/`,`.github/` は変更禁止。
- `git commit`/`git push` 非実行（`--disallowedTools` で抑止。push は決定論ステップが担う）。
- 値は推測で埋めず、一次情報・実データに基づく。日付は絶対表記（例 `2026-06-05`）。
- リポジトリ標準（`CLAUDE.md`：赤=上昇/青=下落、`NNNN.JP` 形式、日本語UI）を尊重。
