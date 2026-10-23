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
- 2026-09-06: LLM プロンプト改訂の過去データ検証は1日で回る。①`technical_features.json` を git 履歴から復元（40営業日ぶん取れる）②**日付を全部削除**してプロンプトに指標を直接埋め込む（未来データ参照の経路を断つ最も確実な方法。ファイル読み禁止の指示より硬い）③subagent を並列実行（1日あたり約$1.7・20分）④`data/*.parquet` から forward return を作って rank IC を突き合わせ。生の shadow 期間を待つ必要はない。
- 2026-09-06: エージェント指示の効き目は「常時コンテキストに入るファイル（`AGENTS.md`）からのホップ数」でほぼ決まる。あおいの口調が抜ける主因は、ペルソナ正本が `AGENTS.md` → `07` → `09` の2ホップ先にあり、かつ `AGENTS.md` 内に「あおい」の語が0回だったこと。1ホップ目を読み飛ばすと指示の存在ごと視界に入らない。守らせたい規則は正本を分けたままでも、核だけ常時読み込みファイルへ直書きして0ホップ化する。
- 2026-09-06: 「毎日同じ issue が積み上がる」型の調査は、まず全 issue の failure 文字列だけを `gh issue view N --json body` で抜いて横並びにするのが最短。今回は13件すべて `ticker_payloads_too_large` 単独で、しかも数値が単調増加していたため、「パイプラインの故障ではなく閾値超過の一本道」と即断できた。
- 2026-09-06: 成果物サイズの犯人特定は、git 履歴から日次の合計バイトを再構成（`git ls-tree -r --long <commit> -- <dir>` を全コミットに回す）してから、同じファイルを新旧2時点で JSON パースして構成要素ごとにバイト数を割る、の2段で決まる。今回はこれで「行数は不変、1シグナルが 1,484B→5,208B に肥大」まで即座に絞れた。
- 2026-09-26: 指示ファイルの節を別ファイルへ移したら、移した節の見出し名（例「ユーザーへの報告」）と移動元のファイル名でリポジトリ全体を grep し、参照切れを潰す。`d30b0dff3` では 09 と AGENTS.md の索引に参照切れが残り、読み手の文脈を書いた1行と規律1つが移設時に落ちていた。
- 2026-10-19: Phase 2 の週次推移は、`git log --grep='Weekly retrain refresh'` の各コミットから `git show <c>:docs/portfolio_backtest.json` と `docs/portfolio_shadow_report.json` を読めば数秒で並べられる（IR・turnover・`cs_ic_vs_phase1`・gate failures）。06 の「現在地」は手で更新されないと止まるので、状況判断の前に必ずこれで実測を取る。
- 2026-10-23: Python 依存の脆弱性監査は `uv export --frozen --no-hashes --no-emit-project --format requirements-txt > req.txt` → `uvx pip-audit -r req.txt --no-deps --disable-pip` で回る（`--no-emit-project` を付けないと先頭の `-e .` 行で pip-audit がパースエラー）。修正は `uv lock --upgrade-package <pkg>` を脆弱パッケージ分だけ並べると影響が最小。web は `npm audit` → `npm install --save-exact next@X eslint-config-next@X` → `npm audit fix`。

## Mistakes to Avoid
（失敗と再発防止策）

- 2026-09-05: この環境の `grep` は GNU/BSD grep ではなく **ugrep 7.8.4 をラップしたシェル関数**（`~/.claude/shell-snapshots/` 由来）。変数展開したファイル名リストを未クォートで渡すと警告を出したうえで連結名として扱い `No such file or directory` になる。複数ファイルを対象にするときはファイルごとにループするか、パスを明示的に列挙する。
- 2026-09-05: この Mac は BSD userland のため GNU 専用フラグが無い。今回踏んだのは `xargs -a <file>`（`invalid option`）と `cat -A`（`illegal option`）。GNU 前提のフラグは使う前に単発で確認する。
- 2026-09-05: モデルIDの新旧を記憶で判断して「引退済みだから CI が壊れる」と報告しかけた。実際は `claude-opus-4-8` / `claude-sonnet-4-6` は現役提供中で、真の論点は価格と世代（`claude-sonnet-4-6` $3/$15 → `claude-sonnet-5` $2/$10 で新しい方が安い）。モデルID・価格・非推奨状況は必ず一次情報で確認してから報告する。
- 2026-09-05: `docs/curation/technical_<DATE>.json` が `deterministic-baseline` のままであることを「エージェントが書いていない証拠」と読み違えた。dated ファイルは `scripts/technical_screen.py` が baseline と同時（=エージェント実行前）に書くスナップショットなので、定義上いつも baseline。エージェントが書いたかどうかは、curation コミット時点の `technical_latest.json` を見ないと判定できない。
- 2026-09-06: パイロット2日で IC 改善 +0.030（現行の3倍）が出て「合格」と判断しかけた。17日に増やしたら +0.002 まで消え、本命の判定軸（大幅修正銘柄に絞った IC）は負に反転した。IC 系の検証は最低15日、かつ重複窓を考慮したブロックブートストラップで信頼区間がゼロを外れるまで結論を出さない。少数日の結果は方向すら信用できない。
- 2026-08-27: PowerShell で python の stdout を `| Select-Object -First N` に通すと broken pipe で exit 255 になる（スクリプト自体は正常終了）。終了コードの検証はパイプなしで実行して `$LASTEXITCODE` を見る。
- 2026-09-26: ハーネスは `git pull --rebase` より前に `CLAUDE.md`→`AGENTS.md` を読み込むため、古いチェックアウトで始まったセッションは pull 後もしばらく旧版の指示で動く（今回は「1〜3行で要約して提示」の旧 LEARNINGS ルールを踏んだ）。pull で `AGENTS.md` や 07〜09 が更新されたら、ディスク上の現行版を読み直してから作業する。
- 2026-10-19: Core 日次ジョブは stooq への接続タイムアウト（connect timeout=20 × 銘柄数・リトライ）が重なると `timeout-minutes: 30` に達して強制キャンセルされる（2026-10-19 実例）。job の強制キャンセルは main.py のフォールバックでは救えず、当時は Retry ワークフローだけが救済経路だった。同日 PR #25 で Stooq サーキットブレーカー（連続3回の接続失敗で遮断、300秒ごとに1回だけ再試行）を入れ、全断時の待ちを1プロセスあたり約2分に抑えた。外部 API を銘柄ループで叩く処理は「1件あたりの最悪待ち時間 × 件数」と job timeout を必ず突き合わせる。
- 2026-10-22: drift Issue の本文 `DRIFT_BREACH ... breached for [...]` は当時 PSI 警告銘柄（warned）を並べており、47銘柄が breach に見えたが実 breach は `6098.JP` の1件だけだった。drift Issue を読むときは本文ではなく `docs/drift_report.json` の `summary.breached_tickers` と各銘柄の `breach_reasons` を正とする（2026-10-22 に出力を breach 銘柄だけに修正済み）。
- 2026-10-23: `uv lock --upgrade-package curl-cffi` だけでは curl-cffi が上がらない。yfinance 1.2.0 が curl-cffi <0.14 を縛っているため、yfinance も同時に upgrade する必要がある（1.7.0 で解除）。また cloud セッションの proxy は Yahoo への CONNECT を 403 で拒否するため yfinance の実取得はローカル検証できない（`download_yfinance_data` は None を返して縮退するだけ）。

## Domain Knowledge
（業務・仕様に関する事実）

- 2026-09-05: curation 系 JSON の `model` フィールドは監査証跡専用で、読み手のコードが1行も無い（`src/` `scripts/` `tests/` `web/src/` を grep して consumer ゼロ）。`scripts/technical_screen.py` が baseline 値 `deterministic-baseline` を書くだけ。したがってこの値を変えても merge 出力・ダッシュボードは一切変わらない。
- 2026-09-05: curation の AI ステップは5本すべて `continue-on-error: true`、かつ `scripts/curation_merge.py` が technical 欠落／fundamental 陳腐化で `conservative` に落ちるため、モデル起因の失敗は日次シグナルを止めない。代償として失敗が success として記録され、静かに縮退する。
- 2026-09-05: `.agents/skills/` 正本から補助ファイルを指すパスは必ずリポジトリルート相対でなければならない（07 の規約）。スタブ経由起動時のベースディレクトリは `.claude/skills/<name>` になるため、`../../` 形式は解決に失敗する。`jp-stock-ticker-curation/SKILL.md` に1件取り残しがあり修正した。
- 2026-09-05: `scripts/curation_merge.py` が technical JSON から読むのは `score` / `rows_available` / `name` / `sector` の4つだけ。`signals` `rationale` `notes` `model` `horizon_days` は未使用で、`warmup_ok` も `rows_available >= min_warmup_rows` で再計算される。つまり technical agent がユニバースに効く経路は `score` 一本。
- 2026-09-06: ダッシュボードの signal オブジェクトは `docs/state.json` / `dashboard_index.json` / `tickers/*.json` で共通で、唯一の絞り込み地点が `src/dashboard.py` の `_normalize_signals`。ここは `update_state` と `export_dashboard_data` の両方から呼ばれるため、ここで落とすと既存30日分も次回実行で遡って縮む。
- 2026-09-06: signal の `threshold_optimization` を消しても監査証跡は失われない。当日分は `docs/backtest_report.json`（publish から --exclude 済み、git に無期限で残る）、履歴は `data/models/<version>/<ticker>/ticker_metadata.json` の `gate_evidence` に不変で入っており、signal の `gate_evidence_sha256` から辿れる。例外は ephemeral fallback gate で、この場合だけ git 履歴が唯一の記録になる。
- 2026-09-06: DB 書き込みが signal から読むフィールドは `src/db_records.py` の `signal_to_prediction_row` / `signal_to_signal_row` の固定キー写像で全部。ここに無いキーは live write-through も `scripts/backfill_state_signals.py` 経由の backfill も一切参照しない。
- 2026-09-05: 週次 Weekly Fundamental & Report の `Refresh technical features` ステップ（`technical_screen.py` 再実行）が、週報ライターより前に `technical_latest.json` を baseline で上書きする。そのため週報が読む technical は常に baseline であり、日次エージェントが書いた `rationale` / `notes` の読み手はゼロ。
- 2026-09-05: 日次 technical agent の編集幅は score ±1〜2点（60銘柄中24〜59件を書き換え、最大 |Δ|=10）。5営業日を offline リプレイした結果、merge の decision は 4/5 日で完全一致、残り1日も watchlist の `warming` マーカー1件（6723.JP）が違うだけでユニバースは不変。実際に swap が出た 2026-08-31 も baseline と agent で同一（4307.JP in / 9983.JP out）。1回あたり約 $1.72・14分・16ターン。
- 2026-09-06: `technical_screen.py` の決定論スコアは、27年・1,318週（重複なし）の検証で **rank IC が有意に負**。5日後 −0.026 (t=−4.25)、1日後 −0.019 (t=−3.04)、10日後 −0.032 (t=−3.75)、20日後 −0.036 (t=−3.07)。60日以降はゼロに減衰し、**どのホライズンでもプラスにならない**。27年中21年が負、構成10指標すべて負（順張り系ほど強く負、方向性のない `vol_ratio` が最もゼロに近い）。式は momentum を測っているが、この銘柄群の短期は reversion が優勢で符号が逆。
  ※直近3ヶ月だけで測ると |t|<1 でゼロと区別できない。サンプルを27年に伸ばして初めて有意になった。短期窓での「証拠なし」を「効果なし」と読まないこと。
  ※**最大の留保は生存バイアス**。検証に使った50銘柄は現在 enabled の銘柄の全履歴で、上場廃止・外された銘柄が欠落している。式の変更を議論する前にここを潰す必要がある。
- 2026-09-06: 日次 technical agent は**狭い裁量（±1.7点）のままが最適**。現行は IC を +0.010〜+0.014 改善し、ブロックブートストラップ 95%CI がゼロを含まない。裁量を ±15 に広げた改訂プロンプトを19営業日で検証したところ +0.0027（CI がゼロを含む）に劣化し、大幅修正(|調整|≥5)銘柄に絞ると −0.0098。介入幅と IC 改善の順位相関は −0.132、大胆な日ほど成績が悪い。つまり `$1.72/日` は pay しており、廃止も拡大も誤り。
- 2026-09-06: `score_entry()` は features エントリの純関数なので、任意の過去日の baseline を再計算できる。保存済み `technical_<date>.json` と突き合わせれば検証ハーネスの正しさを保証できる（19日全一致）。ただし features と baseline は**同一 curation コミットから取る**こと。`data_through` で寄せると金曜日だけ土曜の週次ジョブが撮り直した別スナップショットを掴む。
- 2026-09-06: `tech_weight` / `fund_weight` の 0.5/0.5 は**実質 93/7**。銘柄間の標準偏差がテクニカル 22.14 に対しファンダ 6.03（ファンダ点は52〜83点に密集）で、combined の変動をテクニカルが支配する。真に半々にするには `fund_weight` ≈ 0.79 が必要。
- 2026-09-06: ファンダ寄せは現状のデータでは検証不能。①ファンダ点の履歴は週次13本しかない（LLM 生成物なので遡れない）②その13本での単独 IC は 5/10/20/60日で −0.006 / −0.019 / +0.015 / −0.010 と全てゼロ近傍で解釈不能 ③`fund_weight` を 0.9 以上にすると昇格ゲート通過数が 10 → 43 → 73 に爆発する（ファンダ点が閾値70の直上に密集しているため）。テクニカルの27年履歴と違い、ファンダは長期検証の材料が存在しない。
- 2026-08-27: TOPIX 劣後（IR -0.09）の主因は銘柄選択ではなく構造要因。①target_vol 0.12 による vol targeting で平均グロス 0.496・β0.54（上昇相場の値上がりを半分しか取れない）、②5日ごとのほぼ全入れ替え（turnover 年約50倍）でコスト累計 -5.1%/9ヶ月。銘柄選択自体は alpha +9.7%/年・Sharpe 2.03 と正。
- 2026-08-27: `docs/portfolio_backtest.json` のベンチマークは毎期 turnover 2.0 の合成コスト（累計 -10.2%/9ヶ月）を課される same-basis 設計。実際のバイ&ホールド TOPIX（コストほぼゼロ）と比べると真のギャップは約 -10pt であり、公式 IR -0.09 が示すより大きい。
- 2026-09-06: `UserPromptSubmit` フックで毎ターン指示を再注入するコストの実測。静的な `cat` 一発なら実行は約19ms/ターン（既存 `PostToolUse` の `ruff` / `uv run python tests/` / `npm run lint` は秒オーダー）。注入位置は会話末尾なのでプロンプトキャッシュの前半は壊れない。ただし本文ぶん（今回の案で約360文字）が毎ターン積み上がるため、長いセッションほど高くつく＝ドリフトが起きる場面とコストが乗る場面が一致する。
- 2026-09-06: Claude Code の output style（`.claude/output-styles/`）はシステムプロンプトを直接書き換えるので口調の強制力は最も高いが、非 default の style はコード生成向けの組み込み指示を落とす仕様。開発リポジトリでペルソナ目的に使うのは副作用が大きく不適。加えて `/output-style` はターミナルダイアログ系のためデスクトップアプリからは開けない。
- 2026-09-26: 週報の「総合」点は、モデルが `technical_latest.json` と `fundamental_latest.json` の平均を自分で計算したもので、マージの `combined` ではない。週次ジョブの `Refresh technical features` が technical をベースラインで上書きするため、2026-09-26 号では伊藤忠が 69.2 と載ったが、マージ上は 76.25 だった。正しい値は `decision_latest.json` の `ranking[]`（`combined` / `tech_score` / `fund_score`）にある。ただし週次ジョブはマージを回さないので、`fund_score` は前回の fundamental に基づく値になる。
- 2026-09-26: `pipeline-safety-reviewer.md` の不変条件は 08 の手書きの写しで、08 と同期する仕組みが無い。2026-09-26 時点で、fail-closed・スキーマ版の bump・parquet 削除禁止の3つが欠け、`universe_select.py --apply`（08 が許可している tickers.yml の3人目の書き手）も漏れていた。08 を変えたらレビュアーも直す。
- 2026-10-19: Phase 2 公式 backtest の IR は週ごとに ±0.8 振れる（2026-09-19 −0.84 → 09-26 +0.80 → 10-17 +0.002）。単週の IR で畳む/育てるを決めてはいけない。一方 turnover は 0.99→0.69 と単調に下がりつつも、全週で `turnover>0.40` が唯一の安定した gate 不合格要因。
- 2026-10-22: データ取得が stale な日の再実行は、前日と同じ `market_as_of_date` / `entry_date` のシグナルを別 `run_date` で再発行し、両方が同一 outcome に精算される。`docs/signal_outcomes_recent.json` で同じ `(ticker, entry_date)` が2行並び、直後の数営業日ぶんが欠けていればこのパターン。drift の `n_outcomes` はこれで水増しされていた（2026-10-22 に drift 側だけ重複排除）。

## Open Questions
（未解決・要調査）

- 2026-09-06: 日次 technical agent は現状維持で決着（裁量拡大は19日検証で不合格、`min_gap` 引き下げは25日で効果ゼロ、ファンダ寄せは検証材料が存在せず保留）。残る最大の論点は**テクニカル式の27年負 IC が生存バイアスの産物かどうか**。現在の検証は enabled 50銘柄の全履歴で、廃止・除外銘柄が欠落している。候補プール側でも再現するなら、重み調整ではなく**式の符号そのもの**の議論になる。$0・AI実行ゼロで測れる。
- 2026-10-22: Phase 1 の PSI 参照分布は `train_pool_end`（holdout・内部検証の手前）までの250行で作られるため、再学習直後でも直近窓と約3ヶ月ずれ、50銘柄中47銘柄が常時 `psi>0.25` 警告になる。`build_feature_reference` の docstring「再学習直後は PSI≈0」は成り立っていない。参照を直近へ寄せるかは artifact 内容を変えるので未着手。

## Consolidated Principles
（統合パス専用。通常の更新処理から直接追記しない）
