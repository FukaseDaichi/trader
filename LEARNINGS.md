# Project Learnings

<!--
記法ルール:
- 1項目1洞察。複数の学びを1行に詰めない
- 各項目の先頭に日付を必ず入れる（例: - 2026-08-24: ...）
- 上4セクションは「生の観察」の置き場。Consolidated Principles には
  統合パスで抽出した原則だけを置く。両者を混ぜない
-->

## Patterns That Work
（効いたやり方・型）

- 2026-09-05: push 前の pre-flight は `ci-tests.yml` と同じループをそのままローカルで回すのが確実（`for f in tests/test_*.py; do uv run python "$f"; done`、46件で数分）。CI と同一の実行形なので結果が食い違わない。web 側は `npm run lint --prefix web` と `npm run build:prod --prefix web` の2本。
- 2026-09-05: エージェント指示の「クラフト」監査は、まず `git blame` で圧縮パスのコミットを特定するのが最短。このリポジトリは `b310adb4d`（2026-08-27 Refactor for clarity）が AGENTS.md / 07 / 08 にしか当たっておらず、旧世代向けの足場（step-by-step・文字数上限・過剰な大文字強調）は grep 0件だった一方、そのパスが触っていない 2026-06 生まれのスキル群にクラフトが集中していた。監査は「どこが掃除済みか」を先に確定させると対象が一気に絞れる。
- 2026-09-05: 指示ファイルへの一括変更は、1発見=1 patch ファイルに分けて `diff -u --label a/<path> --label b/<path>` で生成し、`git apply --check` を全件通してから適用すると取捨選択できる形で渡せる。同一ファイルを触る patch が複数あるときは適用順（後ろの行を触る方を後）を明示する。
- 2026-09-05: 「AI レイヤが実際に効いているか」は offline リプレイで決着する。`scripts/curation_merge.py` の `compute_decision` は純関数なので、各 curation コミットから baseline（`technical_<DATE>.json`）と agent 出力（`technical_latest.json`）を、親コミットから `tickers.yml` と `fundamental_latest.json` を取り出し、両方の入力で `compute_decision` を回して decision を突き合わせればよい。リポジトリには一切書き込まずに済む。

## Mistakes to Avoid
（失敗と再発防止策）

- 2026-09-05: この環境の `grep` は GNU/BSD grep ではなく **ugrep 7.8.4 をラップしたシェル関数**（`~/.claude/shell-snapshots/` 由来）。変数展開したファイル名リストを未クォートで渡すと警告を出したうえで連結名として扱い `No such file or directory` になる。複数ファイルを対象にするときはファイルごとにループするか、パスを明示的に列挙する。
- 2026-09-05: この Mac は BSD userland のため GNU 専用フラグが無い。今回踏んだのは `xargs -a <file>`（`invalid option`）と `cat -A`（`illegal option`）。GNU 前提のフラグは使う前に単発で確認する。
- 2026-09-05: モデルIDの新旧を記憶で判断して「引退済みだから CI が壊れる」と報告しかけた。実際は `claude-opus-4-8` / `claude-sonnet-4-6` は現役提供中で、真の論点は価格と世代（`claude-sonnet-4-6` $3/$15 → `claude-sonnet-5` $2/$10 で新しい方が安い）。モデルID・価格・非推奨状況は必ず一次情報で確認してから報告する。
- 2026-09-05: `docs/curation/technical_<DATE>.json` が `deterministic-baseline` のままであることを「エージェントが書いていない証拠」と読み違えた。dated ファイルは `scripts/technical_screen.py` が baseline と同時（=エージェント実行前）に書くスナップショットなので、定義上いつも baseline。エージェントが書いたかどうかは、curation コミット時点の `technical_latest.json` を見ないと判定できない。
- 2026-08-27: PowerShell で python の stdout を `| Select-Object -First N` に通すと broken pipe で exit 255 になる（スクリプト自体は正常終了）。終了コードの検証はパイプなしで実行して `$LASTEXITCODE` を見る。

## Domain Knowledge
（業務・仕様に関する事実）

- 2026-09-05: curation 系 JSON の `model` フィールドは監査証跡専用で、読み手のコードが1行も無い（`src/` `scripts/` `tests/` `web/src/` を grep して consumer ゼロ）。`scripts/technical_screen.py` が baseline 値 `deterministic-baseline` を書くだけ。したがってこの値を変えても merge 出力・ダッシュボードは一切変わらない。
- 2026-09-05: curation の AI ステップは5本すべて `continue-on-error: true`、かつ `scripts/curation_merge.py` が technical 欠落／fundamental 陳腐化で `conservative` に落ちるため、モデル起因の失敗は日次シグナルを止めない。代償として失敗が success として記録され、静かに縮退する。
- 2026-09-05: `.agents/skills/` 正本から補助ファイルを指すパスは必ずリポジトリルート相対でなければならない（07 の規約）。スタブ経由起動時のベースディレクトリは `.claude/skills/<name>` になるため、`../../` 形式は解決に失敗する。`jp-stock-ticker-curation/SKILL.md` に1件取り残しがあり修正した。
- 2026-09-05: `scripts/curation_merge.py` が technical JSON から読むのは `score` / `rows_available` / `name` / `sector` の4つだけ。`signals` `rationale` `notes` `model` `horizon_days` は未使用で、`warmup_ok` も `rows_available >= min_warmup_rows` で再計算される。つまり technical agent がユニバースに効く経路は `score` 一本。
- 2026-09-05: 週次 Weekly Fundamental & Report の `Refresh technical features` ステップ（`technical_screen.py` 再実行）が、週報ライターより前に `technical_latest.json` を baseline で上書きする。そのため週報が読む technical は常に baseline であり、日次エージェントが書いた `rationale` / `notes` の読み手はゼロ。
- 2026-09-05: 日次 technical agent の編集幅は score ±1〜2点（60銘柄中24〜59件を書き換え、最大 |Δ|=10）。5営業日を offline リプレイした結果、merge の decision は 4/5 日で完全一致、残り1日も watchlist の `warming` マーカー1件（6723.JP）が違うだけでユニバースは不変。実際に swap が出た 2026-08-31 も baseline と agent で同一（4307.JP in / 9983.JP out）。1回あたり約 $1.72・14分・16ターン。
- 2026-08-27: TOPIX 劣後（IR -0.09）の主因は銘柄選択ではなく構造要因。①target_vol 0.12 による vol targeting で平均グロス 0.496・β0.54（上昇相場の値上がりを半分しか取れない）、②5日ごとのほぼ全入れ替え（turnover 年約50倍）でコスト累計 -5.1%/9ヶ月。銘柄選択自体は alpha +9.7%/年・Sharpe 2.03 と正。
- 2026-08-27: `docs/portfolio_backtest.json` のベンチマークは毎期 turnover 2.0 の合成コスト（累計 -10.2%/9ヶ月）を課される same-basis 設計。実際のバイ&ホールド TOPIX（コストほぼゼロ）と比べると真のギャップは約 -10pt であり、公式 IR -0.09 が示すより大きい。

## Open Questions
（未解決・要調査）

- 2026-09-05: 日次 technical agent は毎営業日ちゃんと書いている（40/40 コミットで `model: claude-sonnet-4-6`）が、merge 結果をほぼ動かさない。残る判断は「日次で回し続けるか、週次1本に集約するか、merge の `min_gap` / `keep_floor` をエージェントの編集幅（±1〜2点）に合わせて効くようにするか」。ユーザー判断待ち。

## Consolidated Principles
（統合パス専用。通常の更新処理から直接追記しない）
