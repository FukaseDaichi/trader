# TOPIX始値（同一basis benchmark）設計

作成日: 2026-07-26 JST
ステータス: 実装済み（2026-07-26、commits ec9ae656..731c2745）。ロールアウト未完了:
2026-07-27朝の日次処理でのパネル反映、2026-08-01週次処理での公式
`docs/portfolio_backtest.json`への反映を待つ。
関連: `specification_document/06_issues_and_backlog.md`「継続中のP0制約」

## 1. 背景

Phase 2のportfolio KPI gateは`ir_unavailable_same_basis`で不合格が続いている。
原因はマクロパネルにTOPIXの始値が無いことである。

戦略側のexecution contract v2は「翌営業日寄付き→H営業日目終値」で
リターンを測る。同じ基準でベンチマークを測るには始値が要る。現在の
マクロパネルは終値しか持たないため、`benchmark_ret`/`excess_ret`は
NULLのままで、IR・alpha・beta・tracking errorが構造的に計算できない。

消費側は既に実装済みで、データの到着を待っている。

- `src/portfolio_backtest.py:166` `_prepare_topix()` — `{date, topix_open, topix}`
  を要求し、欠けていれば`None`を返す
- `src/portfolio_backtest.py:773` `_benchmark_return()` — `exit_close / entry_open - 1`
  を日付完全一致で計算（前後日での補完はしない）
- `scripts/weekly_cross_section_retrain.py:127-151` — 同じ契約でcoverageを判定

欠けているのは生産側だけである。`src/macro.py`の取得経路が全系列について
終値のみを切り出しており、始値は取得直後に捨てられている。

## 2. 目的とスコープ

### 目的

マクロパネルに`topix_open`列を追加し、Phase 2バックテストが
同一basisのbenchmarkリターンを計算できる状態にする。

### スコープ内

- `src/macro.py`の取得・検証・パネル構築に始値を通す
- `tests/test_macro_features.py`および`tests/test_portfolio_backtest.py`へのテスト追加

### スコープ外（明示的にやらないこと）

- **Phase 1特徴量の変更**。`topix_open`はモデルが読まない生データ列とする。
  特徴量セマンティクスが変わらないため、Phase 1 artifact schemaのバージョン上げと
  50銘柄の再学習は発生しない。
- **DBスキーマの変更**。`macro_snapshots`テーブルと`src/macro.py`
  `latest_snapshot_row()`の出力キーは一切変更しない。マイグレーション不要。
- **既存`topix`終値列の扱いの変更**。前日埋めを含め現状のまま維持する。
  変更すると`macro_topix_ret_20`等のマクロ特徴量の意味が変わる。
- **`turnover>0.40`および`cs_ic_vs_phase1`の改善**。本設計はこれらに触れない。
- **`TRADER_PORTFOLIO_MODE`の変更**。`shadow`を維持する。

## 3. 決定事項

| 論点 | 決定 | 理由 |
| --- | --- | --- |
| ベンチマークの原資産 | TOPIX連動ETF `1305.T`（現在の`topix`列と同一銘柄） | 始値と終値が同一銘柄・同一調整係数・同一応答から得られ、基準が原理的にズレない。1305は寄付きで実際に買えるため、コスト控除後の比較対象として妥当。TOPIX指数そのもの（`^TPX`）はYahooで空であることを2026-07-26に再確認済み |
| 用途 | ベンチマーク比較専用 | Phase 1のschema上げと再学習を回避する。現状Phase 1はKPI gate 0/50であり、特徴量追加で改善する根拠がない |
| 実装方式 | 系列設定でのopt-in（TOPIXのみ有効） | 変更の到達範囲をTOPIX 1系列に限定する。他系列の挙動は不変 |
| 検証タイミング | 実装直後にローカルで再計算 | `oos_predictions.parquet`がリポジトリにあるためDB不要で完結する。2026-08-01の判断日までにIR実数値を持てる |

### 採用しなかった案

- **TOPIX指数のOHLCを別ソースから取得**: `topix`終値（=1305）と別系列が
  混在し、TOPIXの定義が2つになる。マクロ特徴量との整合も別途必要になる。
- **全系列で常に始値を取得**: 誰も読まない列と検証対象が増える。
- **ベンチマーク専用parquetを別途持つ**: 同一銘柄のデータが2箇所に分かれ、
  取得タイミング差で始値と終値の日付が食い違いうる。本設計が回避したい
  基準ズレを自ら作り込むことになる。
- **TOPIX終値同士のリターンで代用**: 戦略と比較条件が変わり、IRの意味が壊れる
  （`06_issues_and_backlog.md`で既に不採用と決定済み）。

## 4. 設計

### 4.1 データフロー

```
DEFAULT_MARKET_SERIES["topix"] に始値opt-inフラグ
        ↓
fetch_market_series()  1回の応答から date/close/open を同時に切り出す
        ↓
_validated_market_frame()  終値と始値をまとめて検証
        ↓
_aligned_levels()  topix（終値・前日埋めあり） / topix_open（始値・前日埋めなし）
        ↓
build_macro_panel()  出力列に topix_open を追加
        ↓
data/macro/macro_panel.parquet
        ↓
既存の消費側が自動で拾う
  ├─ portfolio_backtest.py       → IR / alpha / beta / tracking error
  └─ weekly_cross_section_retrain.py → benchmark coverage 判定
```

### 4.2 変更箇所

1. **`DEFAULT_MARKET_SERIES`**: `topix`エントリに始値opt-inフラグ（`"open": True`）
   を追加する。他の系列にはフラグを付けない。

2. **`fetch_market_series(spec)`**: specが始値を要求し、かつ提供元の応答に
   始値が含まれる場合のみ、返却フレームに`open`列を含める。要求が無い場合の
   返却は現状どおり`[date, close]`のまま。
   - yfinance経路: `auto_adjust=True`の応答は既に`open`を含む。同一応答から
     終値と同時に切り出す。
   - Stooq経路: `download_stooq_data()`は正規化済みOHLCVを返すため、
     既に`open`を保持している。新たな通信先は増えない。

3. **`_validated_market_close()` → `_validated_market_frame()`にリネーム**し、
   始値の検証を追加する（詳細は4.4）。この関数はprivateであり、テストは
   公開APIの`fetch_market_series`経由で検証しているため、リネームの影響は
   `src/macro.py`内に閉じる。

4. **`_aligned_levels()`**: 系列フレームが`open`列を持つ場合、`<key>`（終値由来）
   に加えて`<key>_open`（始値由来）を出力する。前日埋めは`<key>`にのみ適用し、
   `<key>_open`には**適用しない**（4.4 守り①）。

5. **`build_macro_panel()`**: 新定数`MACRO_AUX_LEVEL_COLS = ["topix_open"]`を
   追加する。列存在保証ループをこの定数にも適用し、出力列順を
   `["date"] + MACRO_LEVEL_COLS + MACRO_AUX_LEVEL_COLS + MACRO_FEATURE_COLS`とする。
   `MACRO_LEVEL_COLS`は変更しない。

### 4.3 不変条件

- `MACRO_LEVEL_COLS`と`MACRO_FEATURE_COLS`の内容は変更しない。
- `latest_snapshot_row()`の返却キーは変更しない。よって`macro_snapshots`への
  書き込み経路（`src/db.py:1027-1036`）は無変更。
- `add_macro_features()`は`["date"] + feature_cols`のみを選択するため、
  列追加の影響を受けない。
- `topix_open`がパネルに存在しない、または全欠損であっても、パイプラインは
  現在と同一の挙動（benchmark unavailable）に縮退する。

### 4.4 エラー処理と欠損時の契約

#### 守り① `topix_open`は前日埋めをしない

`_aligned_levels()`は全系列を日付で外部結合したのち前日埋めをしている。
USD/JPYは日本の祝日にも値を持つため、TOPIXが取引していない日付の行が
パネルに存在する。

始値も前日埋めすると、そのような行に前日の始値と前日の終値が並び、
実在しない取引期間のベンチマークリターンが生成される。日付は完全一致し、
値は正であるため、消費側のどの検査も通過してしまう。検出不能な誤りである。

したがって`topix_open`は前日埋めせず、非取引日はNaNのまま残す。
消費側の`dropna` + 日付完全一致により、該当日は自動的に除外される。

現行の`_aligned_levels()`のffillループは`series_data`のキー（`topix`等）を
走査するため、`topix_open`は結果として埋められない。ただしこれは偶然の
副作用に依存させず、意図として明示し、テスト（6.1 #4）で固定する。

既存`topix`終値の前日埋めは変更しない。

#### 守り② 始値が不正なときは始値だけを捨てる

日次パイプラインを止めないこと（AGENTS.md「The daily signal run must never
break」）を最優先とする。始値の取得または検証に失敗した場合、終値は生かした
まま始値のみを落とす。系列全体を破棄してマクロ特徴量をNaNにすることはしない。

結果として振る舞いは現状（`benchmark_reason = "topix_open_unavailable_same_basis"`、
gate不合格）に戻る。誤った合格を出さず、正直に不合格を出す。

終値側の失敗は現行動作を変えない。終値が検証に失敗した場合は従来どおり
系列全体を`None`として破棄する（`fetch_market_series`が次の提供元へ
フォールバックする既存経路）。始値のみの破棄は、終値が検証を通過した
場合に限る。

#### 守り③ 始値と終値の調整基準ズレを検出する

提供元が終値のみを分割調整し始値を生値で返す場合、始値の系列単体では
不連続が生じないため、既存の前日比チェックを通過してしまう。

これを検出するため、同一日の`close / open - 1`（日中変動率）を検査する。
広範な指数連動ETFが1日で50%動くことは現実的にあり得ない。既存定数
`_MAX_MARKET_DAILY_MOVE`（0.50）をそのまま流用し、新しい閾値は導入しない。

10:1分割で終値のみが調整された場合、分割日以前の全日で日中変動率が
約−90%となり、即座に検出される。

#### 検証項目のまとめ

始値に対して以下を検査する。

1. **個別日付の欠損値化**（実装時に方針変更、2026-07-26）: 有限かつ正でない
   値は、その日付だけをNaNにする。非取引日の欠損と同じ扱いとし、始値列全体
   は破棄しない。実データで`1305.T`の全履歴（2008年〜、4555行）に2009年の
   薄商い時代の異常値が3件見つかり（出来高1,000〜663,500株、始値0円）、
   「1件でも不正なら列全体を破棄」という当初のルールでは17年前のこの3件
   のせいで直近の始値まで永久に使えなくなることが判明したための変更。
2. 前日比の絶対値が`_MAX_MARKET_DAILY_MOVE`を超える場合、始値列全体を破棄
   し、理由を標準出力にログする。
3. 同一日の`close / open - 1`の絶対値が`_MAX_MARKET_DAILY_MOVE`を超える
   場合、同上。

#### 状態別の振る舞い

| 状況 | 振る舞い |
| --- | --- |
| 始値が正常 | `topix_open`が入り、IR/alpha/beta/tracking errorが計算される |
| 非取引日 | NaN。該当期間はbenchmark比較から除外される |
| プロバイダーが始値自体を返さない | 始値列は取得されない。終値とマクロ特徴量は無傷。現状に縮退 |
| 一部日付が非有限・非正（2026-07-26実装。実データで`1305.T`全履歴中3件を確認） | その日付だけNaN化。始値列自体は維持され、他の日付は生きる |
| 始値系列の不連続（前日比異常） | 始値列全体を破棄。終値とマクロ特徴量は無傷。理由をログ |
| 始値と終値の基準ズレ | 同上 |
| 一部日付のみ欠損（非取引日、または上記のNaN化） | `_benchmark_coverage()`が`coverage_ratio < 1.0`と`incomplete_same_basis_coverage`を報告し、gateは不合格のまま |

## 5. データ根拠（2026-07-26 実測）

| 銘柄 | 結果 |
| --- | --- |
| `1305.T` | `auto_adjust=True`で`['Close','High','Low','Open','Volume']`を返す。直近2年487営業日で`Open`の欠損ゼロ。日中変動率は平均−0.00035 / 標準偏差0.00855 |
| `^TPX` | "possibly delisted; no price data found" — 空。`src/macro.py`のコメント記載どおり |
| `1306.T` | 始値は取得可能だが、Yahooが未調整の10:1不連続を残す既知問題があり採用しない |

バックテスト対象期間（2025-10-21〜2026-07-10、35期間）は`1305.T`の
取得可能履歴に完全に含まれる。

## 6. テスト計画

### 6.1 `tests/test_macro_features.py`

1. 始値opt-inのある系列は`open`付きで返り、opt-inの無い系列は
   `open`列を持たない（他系列が無傷であることの証明）
2. 始値系列に前日比50%超の飛びがある場合、始値のみ破棄され終値は残る
3. 終値のみ調整・始値が生値という基準ズレを合成データで再現し、
   日中変動率チェックで検出され始値のみ破棄されることを確認する
4. **TOPIXが非取引でUSD/JPYのみ動いた日付の行で、`topix`は前日埋めされ
   `topix_open`はNaNのままであること**（守り①の実証）
5. 始値が取得できない場合でも`topix_open`列は存在し全欠損となる
   （列構成が状況で揺れないこと）
6. `latest_snapshot_row()`の出力キーが不変であること（DB契約の証明）

### 6.2 `tests/test_portfolio_backtest.py`

7. `build_macro_panel()`が生成したパネルを`_prepare_topix()`に渡すと
   `None`ではなく始値付きフレームが返ること（生産側と消費側が実際に
   噛み合うことの証明）

### 6.3 回帰

既存の`tests/test_macro_features.py`、`tests/test_portfolio_backtest.py`、
`tests/test_weekly_cross_section_retrain_contract.py`、
`tests/test_cross_section.py`が全て通ること。

## 7. 検証手順（実装直後）

1. 上記テストと関連既存テストを実行する
2. `uv run python scripts/update_macro_snapshots.py`をローカル実行し、
   全履歴入りのパネルを再生成する
3. パネルを点検する
   - バックテスト対象期間（2025-10-21〜2026-07-10）における`topix_open`の欠損数
   - 日中変動率の分布が5節の実測値と整合すること
4. `cs-v1-20260725`の`oos_predictions.parquet`に対しCSバックテストを再実行する
5. `benchmark_coverage.coverage_ratio`、`metrics.information_ratio`、
   `metrics.alpha`、`metrics.beta`、`gate.failures`を読み出して報告する

## 8. 反映の段取り

- コードとテストのみコミットする。ローカルで再生成したパネルは
  測定用にとどめ、コミットしない。
- 2026-07-27（月）朝の日次preopen処理が`update_macro_snapshots.py`を
  通常実行し、始値入りパネルを自然にコミットする。
- 2026-08-01（土）の週次処理が、始値入りパネルで初めて公式の
  `docs/portfolio_backtest.json`を生成する。ここでIRが載る。
- `docs/`配下に新規ファイルは追加されないため、
  `daily-publish-dashboard.yml`の`--exclude`追加は不要
  （`tests/test_publish_workflow.py`に影響なし）。

## 9. 期待される結果

| 項目 | 実装後 |
| --- | --- |
| `ir_unavailable_same_basis` | 解消される |
| `information_ratio` / `alpha` / `beta` / `tracking_error` | 有限値が算出される |
| `benchmark_coverage.coverage_ratio` | 1.0（非取引日の混入が無い限り） |
| `turnover>0.40` | **残る**（0.89、本設計の対象外） |
| `cs_ic_vs_phase1 = -0.240` | **残る**（本設計の対象外） |
| `active_readiness.active_ready` | **false のまま** |

本設計はactive化を実現するものではない。IRという評価軸を初めて
測定可能にし、Phase 2を今後育てるか shadow-only に畳むかの判断材料を
得ることが目的である。得られたIRが低い場合、Phase 2縮小の根拠となる。

## 10. リスク

| リスク | 対応 |
| --- | --- |
| ETF始値が寄付き板の薄さでノイズを含む | 実際に寄付きで約定可能な価格であり、コスト控除後比較としてはむしろ妥当。5節の実測で日中変動率の標準偏差は0.85%と正常範囲 |
| 提供元が将来`open`を返さなくなる | 守り②により始値のみ破棄され現状に縮退する。日次パイプラインは停止しない |
| 提供元の調整方針変更で基準がズレる | 守り③の日中変動率チェックで検出し、始値のみ破棄する |
| coverageが100%に届かずgateが通らない | `_benchmark_coverage()`が正直に`coverage_ratio`と理由を報告する。欠損の混入は誤った合格より望ましい |
