# GitHub Actions / CI仕様

更新日: 2026-06-16 JST

AI 銘柄キュレーション用 workflow は 2 本です。日次はテクニカル駆動の銘柄入替、週次はファンダ更新・レポート生成・LINE 通知を担います。

## 1. 認証

- Claude: `secrets.CLAUDE_CODE_OAUTH_TOKEN`
- LINE: `secrets.LINE_CHANNEL_ACCESS_TOKEN`, `secrets.LINE_USER_ID`
- レポート URL slug: `vars.TRADER_REPO_SLUG`。未設定時は `git remote.origin.url` から導出し、失敗時は `FukaseDaichi/trader`
- **workflow permissions（必須）**: `contents: write` と **`id-token: write`**。`claude-code-action@v1` は OIDC トークンを取得するため `id-token: write` が無いと `Could not fetch an OIDC token` で失敗する。

## 2. スケジュール

| Workflow | JST | cron(UTC) | 主処理 |
|---|---:|---|---|
| `daily-ticker-curation.yml` | 平日 04:30 | `30 19 * * 0-4` | warmup → technical baseline → technical agent → merge |
| `daily-preopen-core.yml` | 平日 06:00 | `0 21 * * 0-4` | 最新ユニバースで `main.py` |
| `weekly-fundamental-report.yml` | 土曜 07:00 | `0 22 * * 5` | macro → fundamental → [隔週]pool refresh → report writer → LINE |

## 3. 日次 workflow

現行ステップ:

1. checkout
2. `astral-sh/setup-uv@v5`
3. `uv python install`
4. `uv sync`
5. `jpx_calendar.py is-open`
6. `curation_guard.py needs-run`
7. `curation_warmup.py --pool curation_pool.yml --out-dir data/watchlist`
8. `technical_screen.py --pool curation_pool.yml --date <today_jst>`
9. Claude `/jp-stock-technical-screen`（`continue-on-error: true`）
10. `curation_merge.py --technical docs/curation/technical_latest.json --date <today_jst> --apply|--dry-run`
11. `uv run python -c "from src.config import load_tickers; ..."` で検証
12. `.github/scripts/commit-and-push.sh "AI ticker curation (<date>)" tickers.yml data docs/curation`

`workflow_dispatch.inputs.apply` が `"false"` の場合は `--dry-run` になります。通常既定は `"true"` です。

## 4. 週次 workflow

現行ステップ:

1. checkout
2. `astral-sh/setup-uv@v5`
3. `uv python install`
4. `uv sync`
5. `today_jst` を解決
6. `technical_screen.py --pool curation_pool.yml --date <today_jst> || true`
7. Claude `/global-macro-screen`（`continue-on-error: true`）
8. Claude `/jp-stock-fundamental-screen`（`continue-on-error: true`）
9. [隔週] `curation_pool_merge.py --check-due --date <today_jst> --github-output [--force]`（cadence ガード、`continue-on-error: true`、`due` を `$GITHUB_OUTPUT` へ）
10. `due == 'true'` のときだけ Claude `/jp-stock-pool-screen`（Sonnet, `--max-turns 20`, `continue-on-error: true`）
11. `due == 'true'` のときだけ `curation_pool_merge.py --proposal docs/curation/pool_candidates_latest.json --date <today_jst> --apply|--dry-run`（`continue-on-error: true`）
12. Claude `/weekly-stock-report`（`continue-on-error: true`）
13. `.github/scripts/commit-and-push.sh "Weekly fundamental & report (<date>)" reports docs/curation curation_pool.yml`
14. `reports/weekly_<date>.md` が存在する場合だけ `curation_notify.py` で LINE 通知（`|| true`）
15. `due == 'true'` のときだけ `curation_pool_notify.py --decision docs/curation/pool_decision_latest.json` で LINE 通知（`continue-on-error: true`）
16. `weekly_performance_notify.py` で週間実績サマリを LINE 通知（`|| true`）

`workflow_dispatch.inputs`: `pool_refresh_force`（既定 `false`、`true` で `--force`）、`pool_refresh_apply`（既定 `true`→`--apply`、`false`→`--dry-run`）。隔週 cadence は `pool_decision_latest.json` の `date` が `cadence_days`(14) 以上前なら due。前回判断なし／前回 `proposal_valid:false` も due（無効・欠落の提案は cadence を消費しない）。

## 5. commit/push

両 workflow は `.github/scripts/commit-and-push.sh` を使います。

この helper は以下を行います。

- `git config` で GitHub Action user を設定
- 指定パスを `git add -A`
- 差分がなければ exit 0
- commit
- `git pull --rebase --autostash origin <branch>`
- `git push origin HEAD:<branch>`
- 最大 3 回 retry

## 6. 障害時の挙動

| 事象 | 挙動 |
|---|---|
| JPX休場 / 当日実施済み | 日次はスキップ |
| warmup で一部失敗 | レポートに記録し継続 |
| technical agent 失敗 | baseline `technical_latest.json` を使って merge 継続 |
| fundamental agent 失敗 | `fundamental_latest.json` が更新されず、日次側では既存キャッシュまたは conservative mode |
| report writer 失敗 | `reports/weekly_<date>.md` が無ければ LINE 通知なし |
| merge で変更なし | commit helper が差分なしで正常終了 |
| pool cadence ガード / pool merge が異常終了 | `continue-on-error`。週次レポート・commit・各通知は継続 |
| pool agent 失敗 / 提案欠落・不正 | pool merge は no-op で `proposal_valid:false` を記録。cadence を消費せず翌週リトライ |
| pool 提案で母集団に変化なし | `curation_pool.yml` は不変、`pool_decision_*.json` のみ更新、pool 通知はスキップ |
| push 競合 | rebase + retry |

## 7. publish連携

`daily-publish-dashboard.yml` は `web/out/` を `docs/` へ `rsync --delete` します。現行 workflow では `--exclude 'curation'` が指定済みなので `docs/curation/` は削除されません。

週次レポートは `reports/` にあり、publish の対象外です。
