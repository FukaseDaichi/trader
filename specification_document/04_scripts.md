# スクリプト (`scripts/`) 問題一覧

## 1. `scripts/jpx_calendar.py`

### 1.1 [MEDIUM] `cmd_is_open` が毎回リモート API にアクセス

**箇所**: L107-113

**問題**: 日次 core、retry、watchdog の各ワークフローで呼び出されるため、1日3-5回 `holidays-jp.github.io` にリクエスト。API のレートリミットやダウンタイムで複数ワークフローが連鎖的に失敗するリスク。

**修正方針**: ローカルキャッシュ (`data/jpx_holidays.json`) を優先読み取りし、キャッシュの鮮度 (1ヶ月以内) を検証。リモートは月次 sync で更新。

---

### 1.2 [LOW] `_today_jst()` のタイムゾーン検出が脆弱

**箇所**: L38-43

```python
if getattr(now.tzinfo, "key", None) == TOKYO_TZ:
```

**問題**: `tzinfo.key` は実装依存属性。一部の Python 実装では存在しない可能性。

---

### 1.3 [LOW] リモートフェッチエラーの無言処理

**箇所**: L114 `except Exception:`

**問題**: フォールバックは適切だが、失敗時のログが出力されない。

**修正方針**: `print(f"Warning: remote fetch failed: {e}", file=sys.stderr)` を追加。

---

## 2. `scripts/run_guard.py`

### 2.1 [MEDIUM] state.json の stale 読み取り

**問題**: 前述の CI/CD セクション (3.1) と同一。checkout 時点の state.json を読むため、他ワークフローの push 後の状態を反映しない。

**修正方針**: ワークフロー側で `git pull` 後にガードを実行。

---

## 3. `scripts/workflow_watchdog.py`

### 3.1 [MEDIUM] アラート送信機能なし

**問題**: exit code 1 を返すのみ。LINE や email 等の通知手段がない。

**修正方針**: watchdog 内に LINE 通知ロジックを追加、またはワークフロー側に failure 通知ステップを追加。

---

## 4. `scripts/monthly_audit.py`

### 4.1 [MEDIUM] `evaluate_kpi_gate` 失敗時の例外ハンドリング未実装

**箇所**: L55-66

**問題**: 1ティッカーの KPI 評価で例外が発生するとスクリプト全体がクラッシュ。他ティッカーの監査結果が失われる。

**修正方針**: ティッカー毎に try/except で囲み、エラーをレポートに記録。

---

### 4.2 [LOW] `datetime.now()` にタイムゾーン未指定

**箇所**: L84

**修正方針**: `datetime.now(ZoneInfo("Asia/Tokyo"))` に変更。

---

### 4.3 [LOW] `numpy` が直接依存関係として未宣言

**箇所**: L14 `import numpy as np`

**問題**: `pyproject.toml` に `numpy` がない。pandas/lightgbm からの間接依存。

---

## 5. `scripts/universe_refresh.py`

### 5.1 [LOW] `load_data` 失敗時の例外ハンドリング未実装

**箇所**: L29

**問題**: parquet ファイル破損時にクラッシュ。

---

### 5.2 [LOW] スクリプトがプレースホルダ状態

**箇所**: L47-48

**問題**: "Phase-1 snapshot" として実装。実際のユニバースリフレッシュ機能は未実装。毎週 GitHub Actions 分数を消費。

---

### 5.3 [LOW] `datetime.now()` にタイムゾーン未指定

---

## 6. `scripts/rotating_refresh.py`

### 6.1 [MEDIUM] 部分失敗時にコミットがスキップされる

**箇所**: L60

```python
return 0 if not failed else 1
```

**問題**: 1ティッカーでも失敗すると exit code 1 が返り、GitHub Actions のデフォルト動作で後続の commit/push ステップがスキップされる。成功したティッカーのデータ更新も永続化されない。

**修正方針**: 部分失敗時も exit code 0 とし、失敗情報はレポートに記録。またはワークフロー側で `if: always()` を commit ステップに追加。

---

### 6.2 [LOW] `datetime.now()` にタイムゾーン未指定

---

## 7. `scripts/feature_precompute.py`

### 7.1 [HIGH] プリコンピュート結果が未活用

**箇所**: L39-40

**問題**: `data/features/{code}.parquet` を生成するが:
1. ワークフローがコミットしない
2. 他のスクリプトが読み取らない
3. daily pipeline は常に `add_features(df)` を再計算

実質的にデッドコード。毎晩 GitHub Actions の実行時間を消費するのみ。

**修正方針**:
- A案: daily pipeline を改修して `data/features/` を活用 → ワークフローでコミット
- B案: ワークフローを無効化/削除して Actions 分数を節約

---

### 7.2 [LOW] `datetime.now()` にタイムゾーン未指定

---

## 8. `scripts/stress_test.py`

### 8.1 [LOW] 失敗時も exit code 0

**箇所**: L73

**問題**: ティッカーの処理失敗をレポートに記録するが、常に成功として終了。ワークフローは成功と見なしコミットを実行する。失敗の可視性が低い。

---

### 8.2 [LOW] `datetime.now()` にタイムゾーン未指定

---

## 横断的課題: `datetime.now()` のタイムゾーン

| スクリプト | 行番号 |
|-----------|--------|
| `monthly_audit.py` | L84 |
| `universe_refresh.py` | L43 |
| `rotating_refresh.py` | L49 |
| `feature_precompute.py` | L53 |
| `stress_test.py` | L59 |
| `src/backtest.py` | L397 |

**共通修正方針**: 全箇所を `datetime.now(ZoneInfo("Asia/Tokyo"))` に統一。`dashboard.py` の JST 実装を参照パターンとする。
