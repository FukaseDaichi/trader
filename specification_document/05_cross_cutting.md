# 横断的課題

## 1. [MEDIUM] ログ基盤の欠如

**影響**: 全 Python ファイル

**現状**: 全モジュールが `print()` を使用。

**問題点**:
- ログレベル (INFO/WARNING/ERROR) の区別なし
- 構造化ログ非対応。自動監視ツールでのフィルタリング困難
- タイムスタンプなし (GitHub Actions が付与するが、ローカル実行時は未付与)
- エラーと情報メッセージが同一ストリームに混在

**修正方針**:
```python
import logging
logger = logging.getLogger(__name__)

# main.py で初期設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
```

---

## 2. [MEDIUM] 型ヒントの欠如

**影響**: `src/model.py`, `src/data_loader.py`, `src/predictor.py`, `src/backtest.py`

**現状**: `dashboard.py` の一部関数のみ型ヒント付き。他モジュールは未実装。

**問題点**:
- IDE / linter による型エラー検知が機能しない
- 関数シグネチャからの仕様理解が困難
- リファクタリング時の安全性が低い

**修正方針**: 段階的に型ヒントを追加。`mypy --strict` での検証を目標。

---

## 3. [MEDIUM] テストの完全欠如

**影響**: プロジェクト全体

**現状**: Python 側に `tests/` ディレクトリなし。`pytest` が依存関係に未追加。Frontend にもテストフレームワーク未導入。

**問題点**:
- コード変更が既存機能を壊さないことを保証する手段がない
- リファクタリングの安全性が極めて低い
- バグの再発防止が不可能

**修正方針**:
1. `pyproject.toml` に `pytest` を追加
2. 最優先のユニットテスト対象:
   - `src/predictor.py` — `action_from_probability()` のエッジケース
   - `src/model.py` — `add_features()` の出力カラム検証
   - `src/config.py` — `load_tickers()` のバリデーション
   - `src/backtest.py` — `_compute_metrics()` の境界値
3. Frontend: `vitest` + `@testing-library/react` の導入

---

## 4. [MEDIUM] `numpy` の直接依存関係未宣言

**影響**: `pyproject.toml`

**現状**: `numpy` を直接 import するファイルが 5 つ以上あるが、`pyproject.toml` に未記載。pandas / lightgbm からの間接依存に依存。

**修正方針**: `pyproject.toml` の `dependencies` に `"numpy>=1.26.0"` を追加。

---

## 5. [MEDIUM] タイムゾーン処理の不統一

**影響**: 全スクリプト、バックエンドモジュール

**パターン一覧**:

| ファイル | 方式 |
|---------|------|
| `src/dashboard.py` | `datetime.now(ZoneInfo("Asia/Tokyo"))` |
| `src/backtest.py` | `datetime.now()` (タイムゾーンなし) |
| `scripts/jpx_calendar.py` | `datetime.now(UTC) + timedelta(hours=9)` |
| `scripts/*.py` (5ファイル) | `datetime.now()` (タイムゾーンなし) |
| `.github/workflows/*.yml` | `TZ=Asia/Tokyo date +%F` |

**問題点**: GitHub Actions (UTC) では `datetime.now()` が UTC を返す。レポートの `generated_at` が UTC タイムスタンプとなり、JST 表記の他データと不整合。

**修正方針**: `dashboard.py` のパターンに全箇所を統一:
```python
from zoneinfo import ZoneInfo
JST = ZoneInfo("Asia/Tokyo")
now = datetime.now(JST)
```

---

## 6. [LOW] `.gitignore` の不備

**現状の問題**:
- `data/features/` (feature_precompute 生成物) が未除外
- `web/node_modules/`, `web/.next/` のトップレベル保護なし

**修正方針**:
```gitignore
# Feature precompute output (not committed)
data/features/

# Web build artifacts (handled by web/.gitignore or here)
web/node_modules/
web/.next/
web/out/
```

---

## 7. [LOW] 機密情報のログ漏洩リスク

**影響**: `src/notifier.py`, `src/config.py`

**問題**: LINE トークン等の機密情報がエラーメッセージに含まれた場合、`print()` で GitHub Actions ログに出力される可能性。

**修正方針**:
- `logging` 導入後、機密値を含む変数のログ出力を回避
- GitHub Actions の `::add-mask::` コマンドで動的マスキングを追加

---

## 8. [LOW] `config.py` のモジュールレベル副作用

**影響**: テスタビリティ、エラーメッセージの品質

**問題**: `TICKERS`, `LINE_CONFIG`, `BACKTEST_GATE_CONFIG` がインポート時に即座に評価される。`tickers.yml` が不正な場合、import 文のトレースバックが出力される。

**修正方針**: 遅延初期化パターンの導入:
```python
_tickers = None

def get_tickers():
    global _tickers
    if _tickers is None:
        _tickers = load_tickers()
    return _tickers
```
