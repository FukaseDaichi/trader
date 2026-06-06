# GitHub Actions / CI設計

作成日: 2026-06-06 JST ／ 改訂: rev.2

ワークフローは**2本**。日次（テクニカル駆動の銘柄入替）と週次（ファンダ更新＋総合レポート＋LINE通知）。

## 1. 認証セットアップ（サブスクOAuthトークン）

1. ローカルで `claude setup-token`（Claude Pro/Max契約が必要）→ OAuthトークン発行。
2. リポジトリ **Settings → Secrets and variables → Actions** に `CLAUDE_CODE_OAUTH_TOKEN` を登録。
3. ワークフローでは `with: claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}`（APIキー課金でなくサブスク枠で稼働）。
4. LINE は既存 `LINE_CHANNEL_ACCESS_TOKEN` / `LINE_USER_ID` を再利用。
5. レポートURL生成用に（任意）`TRADER_REPO_SLUG`（既定 `fukasedaichi/trader`）を `vars` か env で持つ。未設定時は `git remote` から導出。

> トークン失効時はエージェント失敗 → フェイルセーフで現状維持（`02`§6）。再発行して secret 更新。

## 2. スケジュールと既存パイプラインとの順序

| ワークフロー | JST | cron(UTC) | 主処理 |
|---|---|---|---|
| `daily-ticker-curation.yml` | 平日 04:30 | `30 19 * * 0-4` | テクニカル→マージ→`tickers.yml`入替→push |
| （既存）`daily-preopen-core.yml` | 平日 06:00 | `0 21 * * 0-4` | 最新ユニバースで `main.py` |
| `weekly-fundamental-report.yml` | 土 07:00 | `0 22 * * 5` | ファンダ更新→総合レポート→push→LINE |

- 日次は 04:30 → 06:00 の90分バッファで core 前に `tickers.yml` を反映。retry(06:20/06:40)が安全網。
- 週次ファンダは土曜に更新 → 翌週月曜以降の日次マージがその score をキャッシュ利用。レポートも同ジョブで生成。
- データ鮮度: 翌朝04:30/週末は前営業日の確定足が取得可能（既存coreと同じ仮定）。

## 3. 日次ワークフロー `daily-ticker-curation.yml`

テクニカル・エージェントのみ起動。ファンダは直近週の `fundamental_latest.json` をマージがキャッシュ利用する。

```yaml
name: Daily Ticker Curation

on:
  schedule:
    - cron: "30 19 * * 0-4"   # 04:30 JST 平日（06:00 core より前）
  workflow_dispatch:
    inputs:
      apply:
        description: "apply to tickers.yml (true) or dry-run (false)"
        default: "true"

permissions:
  contents: write

concurrency:
  group: daily-curation-main
  cancel-in-progress: false

jobs:
  curate:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv python install
      - run: uv sync

      - name: Check JPX open day
        id: market
        run: |
          TODAY_JST=$(TZ=Asia/Tokyo date +%F)
          echo "today_jst=$TODAY_JST" >> "$GITHUB_OUTPUT"
          uv run python scripts/jpx_calendar.py is-open \
            --date "$TODAY_JST" --cache-path data/jpx_holidays.json --github-output

      - name: Idempotency guard
        id: guard
        if: ${{ steps.market.outputs.is_open == 'true' }}
        run: uv run python scripts/curation_guard.py needs-run --date "${{ steps.market.outputs.today_jst }}" --github-output

      - name: Warm up candidate data
        if: ${{ steps.market.outputs.is_open == 'true' && steps.guard.outputs.needs_run == 'true' }}
        run: uv run python scripts/curation_warmup.py --pool curation_pool.yml --out-dir data/watchlist

      # ② Technical agent (Sonnet / local indicators) -> technical_latest.json
      - name: Technical analysis agent
        if: ${{ steps.market.outputs.is_open == 'true' && steps.guard.outputs.needs_run == 'true' }}
        uses: anthropics/claude-code-action@v1
        with:
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          prompt: "/jp-stock-technical-screen as_of=${{ steps.market.outputs.today_jst }}"
          claude_args: |
            --model claude-sonnet-4-6
            --max-turns 20
            --allowedTools "Read,Write,Edit,Glob,Grep,Bash"
            --disallowedTools "Bash(git push:*),Bash(git commit:*),Bash(rm:*),WebSearch,WebFetch"

      # ③ Deterministic merge: technical(today) + fundamental(cached weekly) -> tickers.yml
      - name: Merge & apply (guarded)
        if: ${{ steps.market.outputs.is_open == 'true' && steps.guard.outputs.needs_run == 'true' }}
        run: |
          APPLY_FLAG="--apply"
          if [ "${{ github.event.inputs.apply }}" = "false" ]; then APPLY_FLAG="--dry-run"; fi
          uv run python scripts/curation_merge.py \
            --technical docs/curation/technical_latest.json \
            --fundamental docs/curation/fundamental_latest.json \
            --date "${{ steps.market.outputs.today_jst }}" \
            $APPLY_FLAG

      - name: Validate tickers.yml
        if: ${{ steps.market.outputs.is_open == 'true' && steps.guard.outputs.needs_run == 'true' }}
        run: uv run python -c "from src.config import load_tickers; print('enabled:', len(load_tickers()))"

      # Shared rebase-safe helper (git pull --rebase --autostash + retry 3x).
      # data/watchlist is gitignored; promoted candidates are moved into data/.
      - name: Commit & push (shared helper)
        if: ${{ steps.market.outputs.is_open == 'true' && steps.guard.outputs.needs_run == 'true' }}
        run: |
          TODAY_JST="${{ steps.market.outputs.today_jst }}"
          mkdir -p docs/curation
          bash .github/scripts/commit-and-push.sh "AI ticker curation ($TODAY_JST)" tickers.yml data docs/curation
```

> マージは `--fundamental` が無い/古い場合でも動作する（ファンダ軸はキャッシュ。欠落時は安全側挙動、`02`§6）。

## 4. 週次ワークフロー `weekly-fundamental-report.yml`

ファンダ更新 → 総合レポート生成 → push → LINEでGitHub URL通知。

```yaml
name: Weekly Fundamental & Report

on:
  schedule:
    - cron: "0 22 * * 5"      # 07:00 JST 土曜
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: weekly-fundamental-main
  cancel-in-progress: false

jobs:
  fundamental-report:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv python install
      - run: uv sync

      - name: Resolve dates
        id: dt
        run: echo "today_jst=$(TZ=Asia/Tokyo date +%F)" >> "$GITHUB_OUTPUT"

      # Ensure technical_latest exists for the report (refresh from latest data)
      - name: Refresh technical features
        run: uv run python scripts/technical_screen.py --pool curation_pool.yml --out docs/curation/technical_features.json || true

      # ① Fundamental agent (Opus / web research) -> fundamental_latest.json (weekly cache)
      - name: Fundamental analysis agent
        uses: anthropics/claude-code-action@v1
        with:
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          prompt: "/jp-stock-fundamental-screen as_of=${{ steps.dt.outputs.today_jst }}"
          claude_args: |
            --model claude-opus-4-8
            --max-turns 40
            --allowedTools "Read,Write,Edit,Glob,Grep,WebSearch,WebFetch,Bash"
            --disallowedTools "Bash(git push:*),Bash(git commit:*),Bash(rm:*)"

      # ⑥ Report writer agent (Sonnet / girl persona) -> reports/weekly_YYYY-MM-DD.md
      - name: Weekly report writer agent
        uses: anthropics/claude-code-action@v1
        with:
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          prompt: "/weekly-stock-report as_of=${{ steps.dt.outputs.today_jst }}"
          claude_args: |
            --model claude-sonnet-4-6
            --max-turns 15
            --allowedTools "Read,Write,Glob,Grep"
            --disallowedTools "Bash,WebSearch,WebFetch"

      # Shared rebase-safe helper (git pull --rebase --autostash + retry 3x).
      - name: Commit & push (shared helper)
        run: |
          TODAY_JST="${{ steps.dt.outputs.today_jst }}"
          mkdir -p reports docs/curation
          bash .github/scripts/commit-and-push.sh "Weekly fundamental & report ($TODAY_JST)" reports docs/curation

      # ⑧ LINE notify the GitHub URL of the report (casual tone)
      - name: Notify report URL via LINE
        env:
          LINE_CHANNEL_ACCESS_TOKEN: ${{ secrets.LINE_CHANNEL_ACCESS_TOKEN }}
          LINE_USER_ID: ${{ secrets.LINE_USER_ID }}
          TRADER_REPO_SLUG: ${{ vars.TRADER_REPO_SLUG }}
        run: |
          uv run python scripts/curation_notify.py \
            --report "reports/weekly_${{ steps.dt.outputs.today_jst }}.md" \
            --date "${{ steps.dt.outputs.today_jst }}"
```

### 実装時に要確認
- **skill配置**: `/jp-stock-technical-screen` `/jp-stock-fundamental-screen` `/weekly-stock-report` は `.claude/skills/` 配下が必要（`01`）。
- **ツール細粒度制限**: `--allowedTools`/`--disallowedTools` に加え、確実性を上げるならリポジトリ `.claude/settings.json` の `permissions` でも担保。`Bash(...)` パターンの実挙動は小規模テストで確認。最終担保は「エージェントはファイル出力のみ・pushは決定論ステップ」という構造側。
- **push安全化（rebase）**: 全ワークフローが共有ヘルパ `.github/scripts/commit-and-push.sh` を使用（`git pull --rebase --autostash` ＋ 最大3回リトライ ＋ 失敗時 `rebase --abort`）。本2本も同ヘルパに統一済み。`reports/` / `docs/curation/` 不在時に `git add` が失敗しないよう、ヘルパ呼び出し前に `mkdir -p` する。helper は「変更なし→exit 0」を内蔵するので差分チェックは不要。

## 5. レポートのGitHub URLとLINE通知 `scripts/curation_notify.py`

- 既存 `src/notifier.py`（LINE Push API）を流用してテキスト送信。
- URL生成: `https://github.com/<TRADER_REPO_SLUG>/blob/main/reports/weekly_<DATE>.md`（`TRADER_REPO_SLUG` 未設定時は `git config --get remote.origin.url` から導出）。
- 文面は女の子ペルソナのカジュアル文体（`05`§4）。例:
  `今週のレポートできたよ〜！📈 注目はXXだね！続きはこちら→ <URL>（投資は自己責任だよ！）`

## 6. 冪等ガード `scripts/curation_guard.py`

既存 `scripts/run_guard.py` 踏襲。`needs-run --date <JST> --github-output` で当日未実施時のみ `needs_run=true`。日次の二重キュレーションを防止。

## 7. 障害時の挙動

| 事象 | 挙動 |
|---|---|
| JPX休場 / 当日実施済み | 日次はスキップ。 |
| テクニカル失敗 | `technical_latest.json` 欠落 → マージ現状維持（差分なし=push無し）。 |
| ファンダ失敗（週次） | `fundamental_latest.json` 更新されず → 翌週まで前回キャッシュを継続利用。レポートは前回ファンダで生成 or スキップ。 |
| レポート失敗 | push対象なし。LINE通知スキップ。翌週リカバリ。 |
| スキーマ検証失敗 | 非ゼロexitで `tickers.yml` 未push（壊れた構成を出さない）。 |
| push競合 | `git pull --rebase` 後再push。`concurrency` で直列化。 |

## 8. concurrency / 権限 / publish 連携

- 両ワークフロー `permissions: contents: write`。各 `concurrency` グループで自身を直列化。
- **publish連携の注意**: 既存 `daily-publish-dashboard.yml` は `rsync --delete web/out/ docs/`。週次レポートは `docs/` 外の `reports/` に置くので影響なし。`docs/curation/` を残すには publish の rsync 除外に `--exclude 'curation'` を追加（実装時必須）。
