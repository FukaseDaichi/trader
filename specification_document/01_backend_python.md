# Python バックエンド 問題一覧

## 1. `src/data_loader.py`

### 1.1 [HIGH] HTTP リクエストにタイムアウト未設定

**箇所**: `download_stooq_data()` L15

```python
response = requests.get(url, headers=headers)
```

**問題**: `timeout` パラメータが未指定。Stooq が応答しない場合、無制限にハングする。GitHub Actions ではジョブ全体がタイムアウトまでスタックし、有用なログが残らない。

**修正方針**: `requests.get(url, headers=headers, timeout=30)` を設定。リトライロジック (exponential backoff) の導入も推奨。

---

### 1.2 [MEDIUM] Stooq エラー検知が脆弱

**箇所**: L19

```python
if "Preceded by" in response.text:
```

**問題**: 英語の特定フレーズに依存。Stooq がエラー形式を変更すると検知できない。

**修正方針**: Content-Type の検証 (`text/csv`)、レスポンスコード検証、CSV パースの成功/失敗で判断する。

---

### 1.3 [MEDIUM] ダウンロードデータの整合性チェック未実装

**箇所**: L23-36

**問題**: 以下を検証していない:
- 価格が正の値であること
- OHLC 関係 (high >= low, high >= open 等)
- 出来高が非負であること
- 異常データ (全ゼロ、同一値の繰り返し)

**修正方針**: `_validate_ohlcv(df)` バリデーション関数を追加。

---

### 1.4 [MEDIUM] Parquet マージで履歴データが上書きされるリスク

**箇所**: L59-63

```python
combined_df = pd.concat([old_df, new_df]).drop_duplicates(subset=['date'], keep='last')
```

**問題**: `keep='last'` により、新データが常に旧データを上書き。Stooq が不正確な過去日データを返した場合、正しい履歴が無警告で破壊される。

**修正方針**: 新旧データの差分を検知し、一定以上の乖離 (例: 終値 5% 以上) がある場合は警告を出す。

---

### 1.5 [LOW] `sync_data_files` がバックアップなしでデータを削除

**箇所**: L92

**問題**: `tickers.yml` からティッカーを除外した瞬間に parquet ファイルが永久削除される。

**修正方針**: 削除前にバックアップディレクトリへ移動、または `--dry-run` モードを追加。

---

## 2. `src/model.py`

### 2.1 [MEDIUM] `vol_ratio` でゼロ除算の可能性

**箇所**: L106

```python
df['vol_ratio'] = df['vol_ma_5'] / df['vol_ma_20']
```

**問題**: 20日間の出来高が全てゼロの場合、`vol_ma_20` がゼロとなり `inf` が発生する。

**修正方針**: `df['vol_ma_20'].replace(0, np.nan)` でガードする。

---

### 2.2 [MEDIUM] train/backtest 間のパラメータ不整合

**箇所**: L275-277 (model.py) vs backtest.py の環境変数

```python
# model.py — ハードコード
val_size = 60
purge_gap = 5
n_folds = 3
```

**問題**: `backtest.py` は `TRADER_BT_VAL_SIZE` 等の環境変数で設定可能だが、`model.py` はハードコード。ユーザーが環境変数でバックテスト設定を変更しても、訓練側に反映されない。

**修正方針**: `model.py` も `BACKTEST_GATE_CONFIG` から読み取るか、共通定数を `config.py` に定義する。

---

### 2.3 [LOW] ストリーク計算が O(n) の iloc ループ

**箇所**: L122-128

**問題**: `.iloc[i]` による代入ループは大規模データで低速。

**修正方針**: vectorized な実装 (`groupby + cumcount` パターン) に置換。

---

### 2.4 [LOW] fold_predictions が空になる可能性

**箇所**: L279-295

**問題**: 全3フォールドが最小データ要件を満たさない場合、アンサンブルが最終モデル1つのみとなるが、警告なし。

**修正方針**: `len(fold_predictions) < n_folds` の場合に warning ログを出力。

---

## 3. `src/predictor.py`

### 3.1 [HIGH] volatility が NaN 時の表示崩れ

**箇所**: L113, L116

```python
signal["reason"] = f"強い上昇シグナル (上昇確率 {prob_up:.0%})・ボラティリティ低 ({volatility:.1%})"
```

**問題**: `volatility` が NaN の場合、`{volatility:.1%}` が `"nan%"` と表示される。LINE 通知で不正な文字列がユーザーに送信される。

**修正方針**: NaN チェックを追加し、`"N/A"` 等のフォールバック表示を使用。

---

### 3.2 [LOW] `int()` 切り捨てによる指値のずれ

**箇所**: L111-112

**問題**: `int()` は切り捨て。`round()` がより正確。

---

### 3.3 [LOW] `resolve_thresholds` の二重呼び出し

**箇所**: L106-107 → L58

**問題**: `generate_signal` 内で一度解決した閾値を `action_from_probability` 内で再度解決。無駄な処理。

---

## 4. `src/backtest.py`

### 4.1 [HIGH] 閾値最適化の過学習リスク

**箇所**: L121-148 (`_build_threshold_candidates`)

**問題**: 1500候補の閾値を OOS データで探索し、同じ OOS データで KPI 評価を行う。閾値最適化自体にクロスバリデーションがないため、OOS データに過学習するリスクがある。

**修正方針**:
- 閾値最適化用の内部 CV を追加
- 候補数を削減 (ベイズ最適化等を検討)
- 最低限、最適化と評価のデータを分離

---

### 4.2 [MEDIUM] `_simulate_strategy` のパフォーマンス

**箇所**: L159-166

**問題**: 各閾値候補 × 全 OOS 行で `resolve_thresholds` を繰り返し呼び出し。数万回の不要な辞書生成。

**修正方針**: 閾値を事前解決し、内部ループでは直接値を使用。

---

### 4.3 [LOW] `max_drawdown` の符号規約が不統一

**箇所**: L204 (負値) vs L237 (abs で比較)

**修正方針**: `max_drawdown_abs` 等のフィールド名で意図を明示。

---

### 4.4 [LOW] `write_backtest_report` のタイムゾーン未指定

**箇所**: L397

```python
"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
```

**修正方針**: `datetime.now(ZoneInfo("Asia/Tokyo"))` に統一。

---

## 5. `src/notifier.py`

### 5.1 [MEDIUM] LINE API 呼び出しにリトライなし

**箇所**: L61-72

**問題**: 一時的なネットワークエラーやレートリミットで通知が消失する。トレーディングシグナルの通知が失われるため影響大。

**修正方針**: `tenacity` ライブラリ等で 3 回リトライ (exponential backoff) を追加。

---

### 5.2 [LOW] `quote()` のピリオドエンコード懸念は誤検知

**箇所**: L52

**検証結果**: `urllib.parse.quote("6758.jp")` は `6758.jp` のままで、`.` はエンコードされない。

**対応方針**: 本項目は不具合修正対象から除外し、将来 `quote(..., safe=...)` を変更する場合に再評価する。

---

## 6. `src/dashboard.py`

### 6.1 [HIGH] 全価格履歴＋全特徴量を JSON 出力

**箇所**: L140-167

**問題**: 各ティッカーの全期間データ (35+ カラム含む) を `history_data.json` に出力。ティッカー数・期間の増加で数十MBに膨張し、GitHub Pages のページ読込が著しく劣化。

**修正方針**:
- フロントエンドに必要なカラム (date, open, high, low, close, volume) のみ出力
- 期間を直近 1-2 年に制限
- 特徴量カラムを除外

---

### 6.2 [MEDIUM] 非アトミックな JSON ファイル書き込み

**箇所**: L114-115, L173-174

**問題**: 書き込み中にプロセスが kill されると、JSON ファイルが破損。次回実行時にロードエラーとなる。

**修正方針**: 一時ファイルに書き込み後、`os.replace()` でアトミックにリネーム。

---

## 7. `main.py`

### 7.1 [MEDIUM] ティッカー毎の例外ハンドリング未実装

**箇所**: L21-93 (メインループ)

**問題**: 1つのティッカーで例外が発生すると、パイプライン全体が中断。それまでに生成されたシグナルは `update_dashboard()` に到達せず消失。

**修正方針**: ティッカー毎に `try/except` で囲み、個別失敗を記録して残りを続行。

---

### 7.2 [LOW] 全ティッカー失敗時にダッシュボードが空更新

**箇所**: L98-100

**問題**: `signals` が空リストでも `update_dashboard()` を実行。前日のシグナルが空データで上書きされる。

**修正方針**: 全ティッカー失敗時は明示的な警告ログを出力し、ダッシュボード更新をスキップするオプションを追加。

---

## 8. `src/config.py`

### 8.1 [MEDIUM] モジュールレベルの副作用

**箇所**: L124-126

```python
TICKERS = load_tickers()
LINE_CONFIG = get_line_config()
BACKTEST_GATE_CONFIG = get_backtest_gate_config()
```

**問題**: インポート時に実行される。`tickers.yml` が不正な場合、`import config` でクラッシュ。テスト時に孤立したインポートが不可能。

**修正方針**: 遅延初期化パターン (関数呼び出し or lazy loading) に変更。

---

### 8.2 [LOW] ティッカー構造のバリデーション未実装

**箇所**: L27-28

**問題**: `code` や `name` キーの存在を検証しない。不正な YAML でも後段で `KeyError` として表面化。

**修正方針**: `load_tickers()` 内で必須キーの存在チェックを追加。
