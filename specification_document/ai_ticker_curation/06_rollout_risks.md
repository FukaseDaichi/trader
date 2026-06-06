# 段階導入・リスク・残課題

作成日: 2026-06-06 JST ／ 改訂: rev.2

無人で `main` を自動更新するため、**観測 → 影響限定 → 全自動**の順で安全に立ち上げる。頻度は「テクニカル日次／ファンダ週次／レポート週次」。

## 1. 段階的ロールアウト

| フェーズ | 目的 | 設定 | 終了条件 |
|---|---|---|---|
| P0 雛形整備 | 部品作成 | skill 3種(`.claude/skills/`)、`curation_pool.yml`、`scripts/{technical_screen,curation_warmup,curation_merge,curation_guard,curation_notify}.py` を実装 | ローカルで各スクリプト単体実行が通る |
| P1 ドライラン | 日次マージの質を観測 | `daily-ticker-curation` を `apply=false`。`tickers.yml` 不変で `decision_*.json` のみ生成 | 1〜2週間、決定ログが妥当 |
| P2 週次レポート先行 | レポート/通知を先に運用 | `weekly-fundamental-report` を稼働（ファンダ更新＋レポート＋LINE）。`tickers.yml` 入替はまだdry-run | レポート文体・LINE URL通知・ファンダscore品質を確認 |
| P3 add のみ | 影響限定 | 日次 `apply=true`、`max_daily_swaps:0 / max_daily_adds:1`（降格なし） | 新規昇格が warmup→KPIゲート通過を確認 |
| P4 少数入替 | 本運用 | `max_daily_swaps:2`。churn/セクター/cooldown/`max_fundamental_age_days` 有効 | 監査ログ・LINEで安定動作を確認 |

各フェーズは `settings.curation` / workflow入力の調整で移行（コード変更不要）。

## 2. サブスククォータ予算（重要）

| エージェント | 頻度 | モデル | 年間起動 | 備考 |
|---|---|---|---|---|
| テクニカル | 平日 | Sonnet 4.6 | ~250 | 軽量。日次入替を駆動 |
| ファンダ | 週次(土) | **Opus 4.8** | ~52 | Opus消費はここだけに集約 |
| レポート | 週次(土) | Sonnet 4.6 | ~52 | 入力JSONを読んで文章化のみ |

- `--max-turns` 上限: テクニカル15-20 / ファンダ40 / レポート10-15。
- レート制限時: エージェント失敗 → **現状維持**（日次は入替なし、週次は前回キャッシュ継続）。翌サイクルで自然回復。
- `main.py`（予測本体）はサブスク非消費。消費は上記3エージェントのみ。

## 3. リスクと緩和

| リスク | 影響 | 緩和策 |
|---|---|---|
| LLMの誤った銘柄選定 | 不適切な銘柄が enabled 化 | dry-run期間・churn上限・`min_combined`/`min_gap`・セクター上限・出典必須・全決定の監査ログ。誤判定は `git revert` で即時復旧 |
| コールドスタート | 新規が履歴不足でKPIゲート不通過→強制HOLD | warmup(`data/watchlist/`)＋`min_warmup_rows` 昇格条件 |
| **ファンダ・キャッシュの陳腐化** | 週次更新の遅延/失敗で古いファンダで入替し続ける | `max_fundamental_age_days`(既定14)超過で**新規昇格停止**＝既存維持（`02`§6） |
| whipsaw（日次往復入替） | 無駄な入替で履歴が不安定 | `cooldown_days`・`min_gap`・`max_daily_swaps` |
| **レポートの事実誤り** | カジュアル文体で数値/事実が崩れる | skillで「数値・日付・出典は入力JSONから正確に」を厳守。任意の自動検証（`05`§6：幻の銘柄/免責チェック） |
| トークン失効 | エージェント失敗 | フェイルセーフで現状維持。再発行運用を手順化 |
| push競合 | non-fast-forward失敗 | `git pull --rebase`＋`concurrency` |
| **publish の rsync 削除** | `daily-publish-dashboard.yml` の `rsync --delete` が `docs/curation/` を消す恐れ | rsyncに `--exclude 'curation'` 追加（実装時**必須**）。**週次レポートは `docs/` 外の `reports/`** で影響なし |
| 高値掴み | 上昇銘柄の後追い | 昇格はファンダ(業績)＋テクニカルの**両軸**必須。テーマ単独推奨を禁止 |
| 市場急変 | 有効性低下 | 既存KPIゲート/ボラガードが最終防波堤（キュレーションは入力更新のみ） |

## 4. 観測性・運用

- **監査ログ**: `docs/curation/decision_*.json` に日次の全判断を記録（再現可能）。
- **週次通知**: `curation_notify.py` で LINE にレポートGitHub URL（カジュアル文体、`05`§4）。
- **ダッシュボード(任意)**: `decision_latest.json`／最新レポートへのリンクをフロントに表示（`04`§9）。
- **健全性**: 既存 `daily-watchdog.yml` / `monthly_audit.py` がユニバース更新後も成果物を検証。

## 5. ロールバック手順

1. 問題コミットを特定（`AI ticker curation (YYYY-MM-DD)` / `Weekly fundamental & report (YYYY-MM-DD)`）。
2. `git revert <sha>` で push（または該当銘柄を手動修正）。
3. 暴走時は `settings.curation.enabled: false`（または workflow を `workflow_dispatch` 限定）で**即時停止**。
4. 原因は `decision_*.json`・各エージェント出力・レポートmdから追跡。

## 6. 実装チェックリスト（新規作成物）

- [ ] `.claude/skills/jp-stock-technical-screen/`（SKILL.md。`technical_screen.py`実行→JSON出力・`tickers.yml`非編集）
- [ ] `.claude/skills/jp-stock-fundamental-screen/`（SKILL.md＋framework。週次フル採点・JSON出力のみ）
- [ ] `.claude/skills/weekly-stock-report/`（SKILL.md。女の子ペルソナでmd生成。`05`準拠）
- [ ] `scripts/technical_screen.py`（`add_features()`流用、`technical_features.json`生成）
- [ ] `scripts/curation_warmup.py`（`data/watchlist/`へ蓄積。`update_data`ロジック流用）
- [ ] `scripts/curation_merge.py`（テク当日＋ファンダ週次キャッシュ合成・ガード・`tickers.yml`編集・監査・データ移管）＋ユニットテスト
- [ ] `scripts/curation_guard.py`（冪等判定。`run_guard.py`踏襲）
- [ ] `scripts/curation_notify.py`（レポートURLをLINE通知。`notifier.py`再利用、ペルソナ文体）
- [ ] `curation_pool.yml`（流動性ある30〜60銘柄）
- [ ] `.github/workflows/daily-ticker-curation.yml`（日次・テクニカル、`03`§3）
- [ ] `.github/workflows/weekly-fundamental-report.yml`（週次・ファンダ＋レポート＋LINE、`03`§4）
- [ ] `tickers.yml` に `watchlist`/`settings.curation`（`report` 含む）追記
- [ ] `daily-publish-dashboard.yml` の rsync に `--exclude 'curation'` 追加
- [ ] `.gitignore` に `data/watchlist/` 追加（warmupは非コミット・毎回再取得）
- [ ] secret `CLAUDE_CODE_OAUTH_TOKEN` 登録（`claude setup-token`）／（任意）`vars.TRADER_REPO_SLUG`
- [ ] （任意）`.claude/settings.json` の `permissions` でCIエージェントのBash細粒度制限を担保
- [ ] （任意）フロント：`decision_latest.json`／最新レポートのリンク表示

## 7. 残課題 / 実装時に決めること

- **相対力(対TOPIX)**: テクニカルに含めるなら指数データ取得が必要。範囲外なら個別モメンタムで代替（`01`§2.1）。
- **`Bash(...)` 細粒度制限の実挙動**: `--allowedTools`/`--disallowedTools` のコマンドパターンの効きを小規模テストで確認。最終担保は「エージェントはファイル出力のみ・pushは決定論ステップ」という構造（`03`§4注）。
- **週次の実行曜日/時刻**: 土曜07:00を既定としたが、日曜や金曜夜でも可。翌週の日次マージに間に合えばよい。
- **レポートのペルソナ名/トーン**: 既定「あおい」。`settings.curation.report` で調整。
- **セクター対応表**: ファンダ`sector`欠落時の補完辞書。
- **push安全化（rebase）の統一**: 完了。全ワークフロー（新規2本を含む）が共有ヘルパ `.github/scripts/commit-and-push.sh`（`git pull --rebase --autostash` ＋ 最大3回リトライ ＋ 失敗時 `rebase --abort`）を使用。

## 8. まとめ（設計方針の要点）

1. **頻度の分離**: テクニカル日次（入替駆動）／ファンダ週次（業績の裏付け・Opusを週1に集約）／レポート週次（読み物＋LINE）。
2. **責務分離**: LLM=分析・採点・文章化、決定論コード=不可逆変更（`tickers.yml`編集・push）。無人自動pushの安全性をここで担保。
3. **3エージェント**: テクニカル(Sonnet)／ファンダ(Opus/Web)／レポートライター(Sonnet/女の子ペルソナ)。`docs/curation/` JSON と `reports/` md の契約で疎結合。
4. **基本維持＋少数入替**: warmup・churn上限・cooldown・ファンダ鮮度ガードで既存ML資産を壊さず漸進。
5. **観測可能・可逆**: 全決定を監査ログ化、週次レポートで人にも説明、`git revert`／`enabled:false` で即時停止・復旧。
