# CI/CD ワークフロー 問題一覧

## 1. 全ワークフロー共通

### 1.1 [CRITICAL] git push 競合 — pull/rebase なしで main にプッシュ

**影響ワークフロー**: 9つ全て (core, retry, publish, retrain, universe, calendar, audit, rotating, feature)

**問題**: 全ワークフローが `git push` 前に `git pull --rebase` を実行しない。2つのワークフローが同時実行され、両方が push すると、後発が `non-fast-forward` エラーで失敗する。

**同時実行リスクの高い組み合わせ**:
- `nightly-rotating-refresh` (19:30 JST) vs `nightly-feature-precompute` (20:00 JST) — 30分差でコンカレンシーグループも未設定
- `daily-preopen-core` + `daily-publish-dashboard` — publish は core 完了後にトリガーされるが、別ワークフローの push と衝突可能
- `weekly-model-retrain` (土曜) + `workflow_dispatch` の手動実行

**修正方針**:
```yaml
- name: Push changes
  run: |
    git pull --rebase origin main
    git push
```
全ワークフローに `git pull --rebase` を追加。加えて、push 操作用のグローバルコンカレンシーグループを検討:
```yaml
concurrency:
  group: git-push-main
  cancel-in-progress: false
```

---

### 1.2 [MEDIUM] ワークフロー失敗時の通知機構なし

**問題**: いずれのワークフローにも失敗時のアラート (LINE, email, Slack) がない。watchdog は 12:30 JST に日次データの鮮度のみチェックし、exit code 1 を返すだけ。週次・月次・夜間ワークフローの失敗は完全に検出されない。

**修正方針**:
- 全ワークフローに失敗時の通知ステップを追加:
```yaml
- name: Notify failure
  if: failure()
  run: |
    curl -X POST "$LINE_NOTIFY_URL" ...
```
- watchdog にも LINE 通知を組み込む

---

## 2. `daily-preopen-core.yml`

### 2.1 [MEDIUM] `git add -A data/` が意図しないファイルをステージ

**箇所**: L61

**問題**: `data/` ディレクトリ内の全変更をステージ。`data/features/` (feature_precompute が生成) や一時ファイルが含まれる可能性。

**修正方針**: 明示的にファイルを指定:
```yaml
git add data/*.parquet data/jpx_holidays.json docs/
```

---

### 2.2 [LOW] 即座の失敗通知なし

**問題**: core が 06:00 JST に失敗しても、次のアクションは 06:20 JST のリトライか 12:30 JST の watchdog まで発生しない。

---

## 3. `daily-preopen-retry.yml`

### 3.1 [HIGH] stale checkout で run_guard が誤判定

**箇所**: L24 (checkout) → L49-52 (run_guard)

**問題**: チェックアウト後に core ワークフローが push した場合、`docs/state.json` が古い状態のまま。run_guard が `needs_run=true` と誤判定し、不要な重複実行が発生する。

**修正方針**: run_guard チェックの直前に `git pull` を追加:
```yaml
- name: Fetch latest state
  run: git pull origin main
- name: Check guard
  run: uv run python scripts/run_guard.py ...
```

---

## 4. `daily-publish-dashboard.yml`

### 4.1 [HIGH] `rsync --delete` の exclude リストが不完全

**箇所**: L90-101

**問題**: `--delete` により `web/out/` に存在しないファイルが `docs/` から削除される。`history_data.json` は事前に `web/public/` にコピーされるため通常は残るが、Next.js ビルドの挙動変更で消失するリスクがある。

**修正方針**: `history_data.json` を exclude リストに明示追加:
```yaml
rsync -av --delete \
  --exclude 'history_data.json' \
  --exclude 'state.json' \
  ...
```

---

## 5. `daily-watchdog.yml`

### 5.1 [MEDIUM] watchdog がアラートを送信しない

**箇所**: `scripts/workflow_watchdog.py`

**問題**: 失敗検知時に exit code 1 を返すのみ。開発者が Actions タブを見ない限り、失敗に気づかない。

---

### 5.2 [MEDIUM] `history_data.json` の日付チェック未実装

**箇所**: `workflow_watchdog.py` L62-69

**問題**: ファイルの存在とフィールドの有無のみチェック。`last_update` が今日の日付であることを検証しない。昨日のデータでも通過する。

---

## 6. `weekly-model-retrain.yml`

### 6.1 [LOW] JPX カレンダーチェック未実装

**問題**: 土曜日に実行されるが、JPX ガードがない。祝日等の影響は受けないが、不要な Stooq API リクエストが発生する。

---

### 6.2 [LOW] 空の LINE トークンによるログノイズ

**箇所**: L36-37

**問題**: `LINE_CHANNEL_ACCESS_TOKEN: ""` で通知を抑制するが、各ティッカーに対して "LINE configuration missing" のログが出力される。

**修正方針**: 環境変数 `TRADER_SKIP_NOTIFY=true` 等で明示的にスキップ。

---

## 7. `nightly-rotating-refresh.yml`

### 7.1 [MEDIUM] コンカレンシーグループ未設定

**問題**: 19:30 JST 実行。30分後の nightly-feature-precompute (20:00 JST) と重複する可能性。両方が `data/` に書き込み・push するため、git 競合が発生する。

**修正方針**: コンカレンシーグループを追加:
```yaml
concurrency:
  group: nightly-data-main
  cancel-in-progress: false
```

---

### 7.2 [LOW] JPX カレンダーチェック未実装

**問題**: 平日のみ実行だが、JPX 祝日 (平日) にも実行される。データ更新なしで無駄な処理。

---

## 8. `nightly-feature-precompute.yml`

### 8.1 [HIGH] プリコンピュート結果がコミットされない

**箇所**: L37-39

```yaml
git add -A docs/feature_precompute_report.json
```

**問題**: `data/features/*.parquet` がワークフローでコミットされない。プリコンピュートの成果物が永続化されず、毎回再計算される。本スクリプトの結果は他のどのスクリプトからも参照されておらず、実質的にデッドコードとなっている。

**修正方針**:
- (A案) `data/features/` をコミット対象に追加し、daily pipeline から参照する
- (B案) ワークフロー自体を無効化/削除

---

### 8.2 [MEDIUM] コンカレンシーグループ未設定

rotating-refresh と同じ問題。

---

## 9. `monthly-calendar-sync.yml`

### 9.1 [MEDIUM] コンカレンシーグループ未設定

**問題**: 手動ディスパッチとスケジュール実行が重複する可能性。

---

### 9.2 [MEDIUM] `cmd_sync` のエラーハンドリング未実装

**箇所**: `scripts/jpx_calendar.py` L145

**問題**: `_fetch_public_holidays()` が例外を投げた場合、`cmd_sync` はハンドルせずにクラッシュ。`cmd_is_open` にはフォールバックがあるが、`cmd_sync` にはない。

**修正方針**: try/except を追加し、フェッチ失敗時はキャッシュファイルを維持。

---

## 10. `quarterly-stress-test.yml`

### 10.1 [LOW] コスト/スリッページがハードコード

**箇所**: L32-34

```yaml
--cost-bps 20.0
--slippage-bps 10.0
```

**問題**: daily pipeline の環境変数設定 (10.0 / 5.0) との関係が不明示。

**修正方針**: リポジトリ変数またはコメントで関係性を文書化。
