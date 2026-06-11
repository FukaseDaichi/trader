# UI全面リデザイン 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ダッシュボードを「3秒で今日の売買候補がわかり、検索でき、用語はその場で学べる」UIに全面刷新する(設計書: `specification_document/plans/2026-06-11-ui-redesign.md`)。

**Architecture:** 表示トークン(`signal.ts`)・用語辞書(`glossary.ts`)・検索正規化(`search.ts`)を単一ソース化し、その上にヒーロー/検索リスト/文章合成などの新コンポーネントを積む。データ契約は `dashboard_index.json` への optional フィールド追加のみ(新規 JSON ファイルなし)。

**Tech Stack:** Next.js 16 (static export) + React 19 + TailwindCSS 4 + Recharts 3 + lucide-react + date-fns。新規 npm 依存ゼロ。Python 3.13 (uv)。

---

## 実行時の約束事(全タスク共通)

- 作業ディレクトリ: リポジトリルート = `/Users/fukasedaichi/git/trader`。web コマンドは `cd web` してから。
- **webのテスト方針**: このリポジトリの web/ にはユニットテストランナーが無い(リポジトリ慣習。テストは `tests/` の素の Python スクリプトのみ)。新たに test framework は導入しない。各タスクの品質ゲートは:
  `cd web && npm run lint && npx tsc --noEmit`
  両方ともエラー0で次へ進む。Python 変更だけは素スクリプトで TDD する。
- 既存のページが**途中のタスクでも常にコンパイル可能**であるように、`signal.ts` の既存関数シグネチャは維持したまま拡張する(後で消す関数は最終クリーンアップで消す)。
- `git add` は**タスクで触ったファイルだけ**を明示指定する(`specification_document/06_issues_and_backlog.md` にユーザーの未コミット変更があるため `git add -A` 禁止)。
- コミットメッセージ末尾に `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` を付ける。
- `<Term>` コンポーネントは **`<Link>` の内側で使わない**(タップが遷移と衝突する)。リスト行・ヒーローチップ内に用語は置かず、列ヘッダや説明文に置く。
- 禁止事項(AGENTS.md): `tickers.yml` を編集しない。`docs/` 配下に新規ファイルを作らない。

---

### Task 1: Python — dashboard_index に前日比フィールドを追加 (TDD)

**Files:**
- Modify: `src/dashboard.py`(`_calc_avg_volume` の直後 ≒159行目にヘルパー追加、`export_dashboard_data` 内 ≒286行・299-307行に配線)
- Test: `tests/test_dashboard_index_change.py`(新規)

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_dashboard_index_change.py` を新規作成:

```python
"""dashboard_index の前日比ヘルパー `_latest_change` の単体テスト。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.dashboard import _latest_change


def main() -> None:
    # データ不足
    assert _latest_change([]) == (None, None)
    assert _latest_change([{"close": 100}]) == (None, None)

    # 正常系: 末尾2件から (prev_close, change_pct)
    prev, chg = _latest_change([{"close": 100}, {"close": 103}])
    assert prev == 100.0
    assert chg is not None and abs(chg - 0.03) < 1e-9

    # 3件以上でも末尾2件を使う
    prev, chg = _latest_change([{"close": 200}, {"close": 100}, {"close": 103}])
    assert prev == 100.0

    # 欠損・ゼロ割り・bool 混入は (None, None)
    assert _latest_change([{"close": None}, {"close": 103}]) == (None, None)
    assert _latest_change([{"close": 100}, {"close": None}]) == (None, None)
    assert _latest_change([{"close": 0}, {"close": 103}]) == (None, None)
    assert _latest_change([{"close": True}, {"close": 103}]) == (None, None)
    assert _latest_change([{}, {"close": 103}]) == (None, None)

    print("OK: test_dashboard_index_change")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run python tests/test_dashboard_index_change.py`
Expected: `ImportError: cannot import name '_latest_change'`

- [ ] **Step 3: 最小実装**

`src/dashboard.py` の `_calc_avg_volume`(148〜158行)の直後に追加:

```python
def _latest_change(records: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    """Return (prev_close, change_pct) from the last two records, or (None, None)."""
    if len(records) < 2:
        return None, None
    prev = records[-2].get("close")
    last = records[-1].get("close")
    for value in (prev, last):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None, None
    prev_f = float(prev)
    last_f = float(last)
    if prev_f == 0:
        return None, None
    return prev_f, (last_f - prev_f) / prev_f
```

`export_dashboard_data` 内、`avg_volume_20 = _calc_avg_volume(records, window=20)`(287行)の直後に1行:

```python
        prev_close, change_pct = _latest_change(records)
```

同関数の index payload(299-307行)を次に置換(`prev_close`/`change_pct` の2キー追加。**`latest_data` の dict 自体は変更しない**— `records[-1]` は `tickers/{code}.json` の `data` 配列と同一オブジェクトのため、汚染すると詳細 JSON に漏れる):

```python
        dashboard_index["tickers"][code] = {
            "ticker": code,
            "name": name,
            "latest_data": latest_data,
            "avg_volume_20": avg_volume_20,
            "prev_close": prev_close,
            "change_pct": change_pct,
            "latest_signal": latest_signal,
            "data_file": f"tickers/{code}.json",
            "rows": len(records),
        }
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run python tests/test_dashboard_index_change.py`
Expected: `OK: test_dashboard_index_change`

Run: `uv run python tests/test_dashboard_portfolio.py`
Expected: 既存テストもパス(出力末尾に OK 系メッセージ)

- [ ] **Step 5: Commit**

```bash
git add src/dashboard.py tests/test_dashboard_index_change.py
git commit -m "dashboard_indexに前日比(prev_close/change_pct)を追加"
```

---

### Task 2: TypeScript 型に optional フィールドを追加

**Files:**
- Modify: `web/src/types/index.ts:49-57`(`TickerSummary`)

- [ ] **Step 1: `TickerSummary` に2フィールド追加**

```ts
export interface TickerSummary {
  ticker: string;
  name: string;
  latest_data: TickerData | null;
  avg_volume_20: number | null;
  prev_close?: number | null;
  change_pct?: number | null;
  latest_signal: Signal | null;
  data_file: string;
  rows: number;
}
```

- [ ] **Step 2: 型チェック**

Run: `cd web && npx tsc --noEmit`
Expected: エラー0

- [ ] **Step 3: Commit**

```bash
git add web/src/types/index.ts
git commit -m "TickerSummaryにprev_close/change_pct型を追加"
```

---

### Task 3: signal.ts を表示トークンの単一ソースに刷新

売り=青系へ統一、`HOLD`→「様子見」、ヒーロー掲載条件 `isActionable` などを追加。**既存関数(`actionLabel` 等)のシグネチャは維持**するので他ファイルはそのままコンパイルできる。

**Files:**
- Modify: `web/src/lib/signal.ts`(全置換)

- [ ] **Step 1: ファイル全体を以下で置換**

```ts
import type { Signal, SignalAction, SignalThresholds } from "../types";

export interface ActionStyle {
  label: string;
  badgeClass: string;
  textClass: string;
  cardClass: string;
  rowTintClass: string;
  barColor: string;
}

/**
 * 表示トークンの単一ソース。
 * 配色ルール: 買い=赤系 / やや買い=橙 / 様子見=グレー / やや売り=シアン / 売り=青系。
 * 緑(emerald)は「成績テスト合格」など健全性の意味専用(売りには使わない)。
 */
export const ACTION_STYLE: Record<SignalAction, ActionStyle> = {
  BUY: {
    label: "買い",
    badgeClass: "bg-red-500/20 text-red-300",
    textClass: "text-red-300",
    cardClass: "bg-red-900/20 border-red-500/50",
    rowTintClass: "bg-red-500/5",
    barColor: "#ef4444",
  },
  MILD_BUY: {
    label: "やや買い",
    badgeClass: "bg-orange-500/20 text-orange-300",
    textClass: "text-orange-300",
    cardClass: "bg-orange-900/20 border-orange-500/50",
    rowTintClass: "bg-orange-500/5",
    barColor: "#f97316",
  },
  HOLD: {
    label: "様子見",
    badgeClass: "bg-slate-700/80 text-slate-300",
    textClass: "text-slate-400",
    cardClass: "bg-slate-800 border-slate-700",
    rowTintClass: "",
    barColor: "#475569",
  },
  MILD_SELL: {
    label: "やや売り",
    badgeClass: "bg-cyan-500/20 text-cyan-300",
    textClass: "text-cyan-300",
    cardClass: "bg-cyan-900/20 border-cyan-500/50",
    rowTintClass: "bg-cyan-500/5",
    barColor: "#06b6d4",
  },
  SELL: {
    label: "売り",
    badgeClass: "bg-blue-500/20 text-blue-300",
    textClass: "text-blue-300",
    cardClass: "bg-blue-900/20 border-blue-500/50",
    rowTintClass: "bg-blue-500/5",
    barColor: "#3b82f6",
  },
};

export function actionLabel(action: SignalAction): string {
  return ACTION_STYLE[action].label;
}

export function actionBadgeClass(action: SignalAction): string {
  return ACTION_STYLE[action].badgeClass;
}

export function actionTextClass(action: SignalAction): string {
  return ACTION_STYLE[action].textClass;
}

export function actionCardClass(action: SignalAction): string {
  return ACTION_STYLE[action].cardClass;
}

export function isBuySide(action: SignalAction): boolean {
  return action === "BUY" || action === "MILD_BUY";
}

export function isSellSide(action: SignalAction): boolean {
  return action === "SELL" || action === "MILD_SELL";
}

/** 「今日のAI判断」ヒーロー掲載条件(LINE digest と同じ): 成績テスト合格 かつ 様子見以外 */
export function isActionable(signal: Signal | null | undefined): boolean {
  return !!signal && signal.gate_passed === true && signal.action !== "HOLD";
}

/** 確率の文字色。買い寄り=赤系 / 売り寄り=青系。 */
export function probTextClass(prob: number | null | undefined): string {
  if (prob == null) return "text-slate-400";
  if (prob >= 0.8) return "text-red-300";
  if (prob >= 0.65) return "text-orange-300";
  if (prob <= 0.1) return "text-blue-300";
  if (prob <= 0.25) return "text-cyan-300";
  return "text-slate-300";
}

export function confidenceLabel(signal: Signal): string {
  if (signal.confidence_label) return signal.confidence_label;
  if (signal.gate_passed === true) return "自信あり";
  if (signal.gate_passed === false) return "自信なし";
  if ((signal.reason || "").includes("KPI")) return "自信なし";
  return "判定なし";
}

/** 成績テスト(KPIゲート)の結果をやさしい言葉のピル用トークンで返す */
export function gateLabel(signal: Signal): { text: string; className: string } {
  const label = confidenceLabel(signal);
  if (label === "自信あり") {
    return {
      text: "成績テスト合格",
      className: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
    };
  }
  if (label === "自信なし") {
    return {
      text: "成績テスト不合格",
      className: "bg-amber-500/15 text-amber-300 border-amber-500/40",
    };
  }
  return {
    text: "成績テスト対象外",
    className: "bg-slate-700 text-slate-300 border-slate-600",
  };
}

export function formatProbability(prob: number | null | undefined): string {
  return prob == null ? "---" : `${(prob * 100).toFixed(1)}%`;
}

export function formatPrice(v: number | null | undefined): string {
  return v == null ? "---" : `¥${v.toLocaleString()}`;
}

export function formatChangePct(v: number | null | undefined): string {
  if (v == null) return "---";
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
}

/** 前日比の文字色。日本式: 上昇=赤 / 下落=青。 */
export function changeTextClass(v: number | null | undefined): string {
  if (v == null) return "text-slate-400";
  if (v > 0) return "text-red-300";
  if (v < 0) return "text-blue-300";
  return "text-slate-300";
}

// ---- 旧API(最終クリーンアップで削除予定。Task 3時点では既存ページが参照) ----

export function confidenceBadgeClass(signal: Signal): string {
  const label = confidenceLabel(signal);
  if (label === "自信あり") return "bg-emerald-500/20 text-emerald-300 border-emerald-500/40";
  if (label === "自信なし") return "bg-amber-500/20 text-amber-300 border-amber-500/40";
  return "bg-slate-700 text-slate-300 border-slate-600";
}

export function formatThresholds(thresholds: SignalThresholds | null | undefined): string {
  if (!thresholds) return "---";
  return [
    `B ${(thresholds.buy * 100).toFixed(0)}%`,
    `MB ${(thresholds.mild_buy * 100).toFixed(0)}%`,
    `MS ${(thresholds.mild_sell * 100).toFixed(0)}%`,
    `S ${(thresholds.sell * 100).toFixed(0)}%`,
    `Vol ${(thresholds.volatility_limit * 100).toFixed(1)}%`,
  ].join(" / ");
}
```

- [ ] **Step 2: 品質ゲート**

Run: `cd web && npm run lint && npx tsc --noEmit`
Expected: エラー0(既存ページは旧APIのまま動く。HOLD表示が「様子見」、SELLバッジが青になるのは意図どおり)

- [ ] **Step 3: Commit**

```bash
git add web/src/lib/signal.ts
git commit -m "signal.tsを表示トークン単一ソース化(売り=青統一/HOLD=様子見)"
```

---

### Task 4: 検索正規化とやさしい指標ラベルの lib

**Files:**
- Create: `web/src/lib/search.ts`
- Create: `web/src/lib/indicators.ts`

- [ ] **Step 1: `web/src/lib/search.ts` を作成**

```ts
/** 日本語向けのゆるい正規化: NFKC(全角/半角) + 小文字化 + カタカナ→ひらがな */
export function normalizeJa(s: string): string {
  return s
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[ァ-ヶ]/g, (ch) =>
      String.fromCharCode(ch.charCodeAt(0) - 0x60),
    );
}

/** 銘柄名 or コードに部分一致するか(「6701」「6701.jp」「とよた」「トヨタ」いずれもヒット) */
export function matchesTicker(query: string, code: string, name: string): boolean {
  const q = normalizeJa(query.trim());
  if (!q) return true;
  return normalizeJa(name).includes(q) || normalizeJa(code).includes(q);
}
```

- [ ] **Step 2: `web/src/lib/indicators.ts` を作成**(ホームの `describeRsi`/`describeVolume` の後継。旧関数は Task 7 のホーム刷新で消える)

```ts
/** RSI を「過熱感」のやさしいラベルへ */
export function heatLabel(rsi: number | null | undefined): {
  text: string;
  className: string;
} {
  if (rsi == null) return { text: "---", className: "text-slate-500" };
  if (rsi >= 70) return { text: "過熱気味", className: "text-red-300" };
  if (rsi <= 30) return { text: "売られすぎ", className: "text-cyan-300" };
  if (rsi >= 55) return { text: "やや強い", className: "text-slate-300" };
  if (rsi <= 45) return { text: "やや弱い", className: "text-slate-300" };
  return { text: "ふつう", className: "text-slate-400" };
}

/** 出来高/20日平均 を「商いの活発さ」ラベルへ */
export function activityLabel(
  volume: number | null | undefined,
  avg20: number | null | undefined,
): { text: string; ratioText: string; className: string } {
  if (volume == null || avg20 == null || avg20 <= 0) {
    return { text: "---", ratioText: "---", className: "text-slate-500" };
  }
  const ratio = volume / avg20;
  const ratioText = `${ratio.toFixed(1)}x`;
  if (ratio >= 1.5) return { text: "かなり活発", ratioText, className: "text-amber-300" };
  if (ratio >= 1.1) return { text: "やや活発", ratioText, className: "text-slate-300" };
  if (ratio >= 0.8) return { text: "ふつう", ratioText, className: "text-slate-400" };
  return { text: "閑散", ratioText, className: "text-slate-500" };
}
```

- [ ] **Step 3: 品質ゲート**

Run: `cd web && npm run lint && npx tsc --noEmit`
Expected: エラー0

- [ ] **Step 4: Commit**

```bash
git add web/src/lib/search.ts web/src/lib/indicators.ts
git commit -m "検索正規化とやさしい指標ラベルのlibを追加"
```

---

### Task 5: 用語辞書 glossary.ts(コピー確定版)

**Files:**
- Create: `web/src/lib/glossary.ts`

- [ ] **Step 1: ファイル作成**(22語。コピーはこのまま使う)

```ts
export interface GlossaryEntry {
  term: string;
  formal?: string;
  short: string;
  analogy?: string;
}

/** 用語辞書の単一ソース。Term ツールチップと用語集アコーディオンの両方がここを参照する。 */
export const GLOSSARY: Record<string, GlossaryEntry> = {
  prob_up: {
    term: "上がる確率",
    formal: "prob_up(予測上昇確率)",
    short:
      "AIが予想する「翌営業日にこの株が上がりそうな見込み」。50%が五分五分です。",
    analogy:
      "降水確率と同じ読み方。70%なら、過去の似た場面で10回中7回くらい上がったイメージ。",
  },
  gate: {
    term: "成績テスト",
    formal: "KPIゲート(ウォークフォワード検証)",
    short:
      "その銘柄でAIの予測どおり売買していたら成績が基準を超えていたか、を過去データで試験したもの。不合格の銘柄は自動的に「様子見」になります。",
    analogy: "模試で合格点を取れた科目だけ本番を受ける、みたいな仕組み。",
  },
  rsi: {
    term: "過熱感",
    formal: "RSI(14日)",
    short:
      "最近の値上がりの勢いを0〜100で表したもの。70以上は「買われすぎ」、30以下は「売られすぎ」の目安。",
    analogy: "連戦続きの選手の疲労メーター。上がりっぱなしはどこかで息切れしやすい。",
  },
  volume_ratio: {
    term: "商いの活発さ",
    formal: "出来高の20日平均比",
    short:
      "その日の売買量がふだん(直近20日平均)の何倍だったか。1.5倍以上なら注目が集まっているサイン。",
  },
  threshold: {
    term: "判断ライン",
    formal: "シグナル閾値",
    short:
      "上がる確率が何%を超えたら「買い」と言うかの基準。銘柄ごとに過去データで自動調整されています。",
  },
  volatility_guard: {
    term: "値動きの荒さ制限",
    formal: "ボラティリティガード",
    short:
      "値動きが荒すぎる銘柄には、確率が高くても強い「買い」を出さない安全装置。",
  },
  limit_price: {
    term: "買ってよい上限",
    formal: "指値目安",
    short: "これより高い値段では追いかけて買わないほうがよい、という目安の価格。",
  },
  stop_loss: {
    term: "撤退ライン",
    formal: "損切りライン",
    short:
      "買った後にここまで下がったら、いったん売って損を確定させる目安の価格。",
    analogy:
      "「ここまで来たら傘をさす」と先に決めておくことで、ずぶ濡れ(大損)を防ぐ。",
  },
  drawdown: {
    term: "一時的な落ち込み",
    formal: "ドローダウン",
    short:
      "資産がいちばん良かった時点から、どれだけ下がったか。山頂からの下り幅です。",
  },
  hit_rate: {
    term: "的中率",
    formal: "ヒット率",
    short: "「買い」と言ったあと、実際に株価が上がった割合。",
  },
  excess_return: {
    term: "市場平均との差",
    formal: "超過リターン(対TOPIX)",
    short:
      "ただ市場全体を買った場合と比べて、どれだけ上回れたか。プラスならAIの判断に意味があった、ということ。",
  },
  topix: {
    term: "市場平均(TOPIX)",
    formal: "東証株価指数",
    short:
      "東証に上場するほぼ全銘柄の平均的な値動き。このAIの成績を測る「ものさし」に使っています。",
  },
  calibration: {
    term: "確率の正直さ",
    formal: "キャリブレーション(較正)",
    short:
      "「70%」と言った予測が、本当に70%くらいの頻度で当たっているか。正直なほど確率を信用できます。",
    analogy: "降水確率70%と言って毎回晴れる天気予報は使えない、のと同じ。",
  },
  brier: {
    term: "予測のズレ点数",
    formal: "Brierスコア",
    short:
      "確率予測のズレを点数化したもの。0に近いほど正確。0.25は「全部50%と言う」のと同じレベル。",
  },
  ic: {
    term: "順位の当たり具合",
    formal: "IC(情報係数)",
    short:
      "「上がりそう」と予想した順番が、実際に上がった順番とどれくらい合っていたか。0より上なら順位付けに意味あり。",
  },
  sharpe: {
    term: "安定度スコア",
    formal: "シャープレシオ",
    short:
      "リターンを値動きの荒さで割ったもの。高いほど「荒れずに稼げている」。1を超えればかなり良好。",
  },
  equity_curve: {
    term: "資産の伸び",
    formal: "エクイティカーブ",
    short:
      "AIのシグナルどおり売買していたら資産がどう増減したかのシミュレーション(コスト込み)。",
  },
  ma: {
    term: "平均線",
    formal: "移動平均線(MA5/20/60)",
    short:
      "直近5日・20日・60日の終値の平均をつないだ線。株価がこの線より上なら上昇基調の目安。",
  },
  regime: {
    term: "市場ムード",
    formal: "マクロレジーム",
    short:
      "金利や為替など市場全体の環境が、株にとって追い風(前向き)か向かい風(慎重)かの判定。",
  },
  shadow_mode: {
    term: "提案モード",
    formal: "シャドーモード",
    short:
      "AIのおすすめ配分はまだ「提案」だけで、売買シグナルには影響していない状態。成績を確認してから本番に切り替えます。",
  },
  cs_rank: {
    term: "AI順位",
    formal: "クロスセクション順位",
    short: "監視中の全銘柄を「上がりそう」順に並べたときの順位。1位がいちばん有望。",
  },
  mae_mfe: {
    term: "最大逆行/最大順行",
    formal: "MAE/MFE",
    short:
      "買ってから売るまでの間に、最悪でどこまで下がったか(逆行)・最高でどこまで上がったか(順行)。",
  },
};
```

- [ ] **Step 2: 品質ゲート**

Run: `cd web && npm run lint && npx tsc --noEmit`
Expected: エラー0

- [ ] **Step 3: Commit**

```bash
git add web/src/lib/glossary.ts
git commit -m "初心者向け用語辞書glossary.tsを追加(22語)"
```

---

### Task 6: Term ツールチップコンポーネント

**Files:**
- Create: `web/src/components/Term.tsx`

- [ ] **Step 1: ファイル作成**

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { GLOSSARY } from "../lib/glossary";

interface TermProps {
  k: string;
  children?: React.ReactNode;
  className?: string;
}

/**
 * 用語ツールチップ。点線下線の言葉をタップ/ホバーすると噛み砕いた解説を出す。
 * 注意: <Link> の内側では使わない(タップが遷移と衝突するため)。
 */
export default function Term({ k, children, className = "" }: TermProps) {
  const entry = GLOSSARY[k];
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const [shiftX, setShiftX] = useState(0);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: Event) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  // 画面端で見切れないよう水平方向をクランプ
  useEffect(() => {
    if (!open) {
      setShiftX(0);
      return;
    }
    const el = popRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const pad = 12;
    if (rect.left < pad) {
      setShiftX(pad - rect.left);
    } else if (rect.right > window.innerWidth - pad) {
      setShiftX(window.innerWidth - pad - rect.right);
    }
  }, [open]);

  if (!entry) return <span className={className}>{children}</span>;

  return (
    <span ref={wrapRef} className={`relative inline-block ${className}`}>
      <button
        type="button"
        className="cursor-help border-b border-dotted border-slate-500 text-left text-inherit hover:border-slate-300"
        aria-expanded={open}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
      >
        {children ?? entry.term}
      </button>
      {open && (
        <div
          ref={popRef}
          role="tooltip"
          style={{ marginLeft: shiftX }}
          className="absolute left-1/2 top-full z-50 mt-2 w-72 -translate-x-1/2 rounded-xl border border-slate-600 bg-slate-800 p-3 text-left font-normal normal-case shadow-xl"
        >
          <div className="text-sm font-bold text-slate-100">{entry.term}</div>
          <p className="mt-1 text-xs leading-relaxed text-slate-300">{entry.short}</p>
          {entry.analogy && (
            <p className="mt-2 text-xs leading-relaxed text-slate-400">💡 {entry.analogy}</p>
          )}
          {entry.formal && (
            <p className="mt-2 border-t border-slate-700 pt-2 text-xs text-slate-500">
              正式名: {entry.formal}
            </p>
          )}
        </div>
      )}
    </span>
  );
}
```

- [ ] **Step 2: 品質ゲート**

Run: `cd web && npm run lint && npx tsc --noEmit`
Expected: エラー0

- [ ] **Step 3: Commit**

```bash
git add web/src/components/Term.tsx
git commit -m "用語ツールチップTermコンポーネントを追加"
```

---

### Task 7: GlossaryAccordion(用語集)

**Files:**
- Create: `web/src/components/GlossaryAccordion.tsx`

- [ ] **Step 1: ファイル作成**

```tsx
"use client";

import { BookOpen } from "lucide-react";
import { GLOSSARY } from "../lib/glossary";

/** ホーム下部の用語集。glossary.ts から自動生成され、Term と説明が二重管理にならない。 */
export default function GlossaryAccordion() {
  const entries = Object.entries(GLOSSARY);
  return (
    <section className="mb-8 rounded-xl border border-slate-800 bg-slate-900/80 p-5">
      <h2 className="mb-1 flex items-center gap-2 text-lg font-bold text-white">
        <BookOpen size={18} className="text-slate-400" />
        用語集
      </h2>
      <p className="mb-4 text-xs text-slate-400">
        画面の点線つきの言葉は、タップするとその場でも説明が出ます。
      </p>
      <div className="grid grid-cols-1 gap-x-6 md:grid-cols-2">
        {entries.map(([key, e]) => (
          <details key={key} className="group border-b border-slate-800/80 py-2">
            <summary className="flex cursor-pointer list-none items-center justify-between text-sm text-slate-200 hover:text-white">
              <span>
                {e.term}
                {e.formal && <span className="ml-2 text-xs text-slate-500">{e.formal}</span>}
              </span>
              <span className="text-slate-500 transition-transform group-open:rotate-90">›</span>
            </summary>
            <p className="mt-2 text-xs leading-relaxed text-slate-400">{e.short}</p>
            {e.analogy && (
              <p className="mt-1 text-xs leading-relaxed text-slate-500">💡 {e.analogy}</p>
            )}
          </details>
        ))}
      </div>
    </section>
  );
}
```

- [ ] **Step 2: 品質ゲート → Commit**

Run: `cd web && npm run lint && npx tsc --noEmit` → エラー0

```bash
git add web/src/components/GlossaryAccordion.tsx
git commit -m "用語集アコーディオンを追加"
```

---

### Task 8: SiteHeader / SiteFooter(共通ナビ+市場ムードピル)

RegimeBanner の役割(market_bias 表示+リンク)をヘッダーピルとフッターへ移す。RegimeBanner 本体の削除はホーム刷新(Task 11)で行う。

**Files:**
- Create: `web/src/components/SiteHeader.tsx`
- Create: `web/src/components/SiteFooter.tsx`

- [ ] **Step 1: `web/src/components/SiteHeader.tsx` を作成**

```tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { MacroLatest } from "../types";
import { fetchJson } from "../lib/fetchJson";

function moodToken(bias: string): { text: string; className: string } {
  switch (bias) {
    case "risk_on":
      return { text: "市場ムード: 前向き", className: "bg-red-500/15 text-red-300 border-red-500/40" };
    case "risk_off":
      return { text: "市場ムード: 慎重", className: "bg-blue-500/15 text-blue-300 border-blue-500/40" };
    default:
      return { text: "市場ムード: 中立", className: "bg-slate-800 text-slate-300 border-slate-600" };
  }
}

function MoodPill() {
  const [macro, setMacro] = useState<MacroLatest | null>(null);
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
    fetchJson<MacroLatest>(
      `${basePath}/curation/macro_latest.json`,
      (v): v is MacroLatest => typeof v === "object" && v !== null,
    ).then(setMacro);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: Event) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  if (!macro?.market_bias) return null;
  const mood = moodToken(macro.market_bias);

  return (
    <span ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`rounded-full border px-3 py-1 text-xs font-semibold ${mood.className}`}
      >
        {mood.text}
      </button>
      {open && (
        <div className="absolute right-0 top-full z-50 mt-2 w-72 rounded-xl border border-slate-600 bg-slate-800 p-3 shadow-xl">
          <p className="text-xs leading-relaxed text-slate-300">
            {macro.summary ||
              "金利や為替など市場全体の環境から、相場が追い風か向かい風かを週次で判定しています。"}
          </p>
          {macro.as_of && <p className="mt-1 text-xs text-slate-500">基準日: {macro.as_of}</p>}
        </div>
      )}
    </span>
  );
}

export default function SiteHeader({ updated }: { updated?: string }) {
  const pathname = usePathname();
  const navClass = (active: boolean) =>
    `rounded-full px-3 py-1 text-sm transition-colors ${
      active ? "bg-slate-800 text-white" : "text-slate-400 hover:text-white"
    }`;
  return (
    <header className="mx-auto mb-8 flex max-w-7xl flex-wrap items-center justify-between gap-3">
      <div className="flex flex-wrap items-center gap-2 md:gap-4">
        <Link href="/" className="text-xl font-bold tracking-tight text-white md:text-2xl">
          AI株式トレーダー
        </Link>
        <nav className="flex items-center gap-1">
          <Link href="/" className={navClass(pathname === "/")}>
            ホーム
          </Link>
          <Link href="/performance" className={navClass(pathname.startsWith("/performance"))}>
            成績
          </Link>
        </nav>
      </div>
      <div className="flex items-center gap-3">
        <MoodPill />
        {updated && <span className="text-xs text-slate-500">更新: {updated}</span>}
      </div>
    </header>
  );
}
```

- [ ] **Step 2: `web/src/components/SiteFooter.tsx` を作成**

```tsx
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

export default function SiteFooter() {
  return (
    <footer className="mx-auto mt-12 flex max-w-7xl flex-col items-start justify-between gap-3 border-t border-slate-800/80 pb-10 pt-6 text-xs text-slate-500 sm:flex-row sm:items-center">
      <p>本サイトはAIによる予測の実験プロジェクトです。投資の最終判断はご自身の責任でお願いします。</p>
      <div className="flex shrink-0 items-center gap-4">
        <a
          href="https://github.com/FukaseDaichi/trader/tree/main/reports"
          target="_blank"
          rel="noopener noreferrer"
          className="underline decoration-dotted hover:text-slate-300"
        >
          週次レポート
        </a>
        <a href={`${basePath}/curation/decision_latest.json`} className="underline decoration-dotted hover:text-slate-300">
          銘柄入替ログ
        </a>
      </div>
    </footer>
  );
}
```

- [ ] **Step 3: 品質ゲート → Commit**

Run: `cd web && npm run lint && npx tsc --noEmit` → エラー0

```bash
git add web/src/components/SiteHeader.tsx web/src/components/SiteFooter.tsx
git commit -m "共通SiteHeader(ナビ+市場ムードピル)とSiteFooterを追加"
```

---

### Task 9: TodayHero(今日のAI判断ヒーロー)

**Files:**
- Create: `web/src/components/TodayHero.tsx`

- [ ] **Step 1: ファイル作成**

```tsx
"use client";

import Link from "next/link";
import { Coffee, TrendingDown, TrendingUp } from "lucide-react";
import { format, parseISO } from "date-fns";
import { ja } from "date-fns/locale";
import { DashboardIndexData, Signal } from "../types";
import {
  actionLabel,
  formatProbability,
  isActionable,
  isBuySide,
  isSellSide,
} from "../lib/signal";
import Term from "./Term";

interface HeroItem {
  code: string;
  signal: Signal;
}

function formatDateJa(iso: string): string {
  if (!iso) return "";
  try {
    return format(parseISO(iso), "M/d(E)", { locale: ja });
  } catch {
    return iso;
  }
}

function HeroChip({ code, signal, side }: { code: string; signal: Signal; side: "buy" | "sell" }) {
  const probClass = side === "buy" ? "text-red-300" : "text-blue-300";
  const mild = signal.action === "MILD_BUY" || signal.action === "MILD_SELL";
  return (
    <Link
      href={`/stocks/${code}`}
      className="flex items-center justify-between gap-3 rounded-lg bg-slate-950/60 px-3 py-2.5 transition-colors hover:bg-slate-900"
    >
      <span className="min-w-0">
        <span className="block truncate text-sm font-semibold text-slate-100">
          {signal.name || code}
          {mild && <span className="ml-2 text-xs font-normal text-slate-400">({actionLabel(signal.action)})</span>}
        </span>
        <span className="font-mono text-xs text-slate-500">{code}</span>
      </span>
      <span className={`shrink-0 text-sm font-bold ${probClass}`}>
        上がる確率 {formatProbability(signal.prob_up)}
      </span>
    </Link>
  );
}

export default function TodayHero({ data }: { data: DashboardIndexData }) {
  const all: HeroItem[] = Object.entries(data.tickers)
    .map(([code, t]) => ({ code, signal: t.latest_signal }))
    .filter((x): x is HeroItem => x.signal != null);

  const buys = all
    .filter((x) => isActionable(x.signal) && isBuySide(x.signal.action))
    .sort((a, b) => (b.signal.prob_up ?? 0) - (a.signal.prob_up ?? 0));
  const sells = all
    .filter((x) => isActionable(x.signal) && isSellSide(x.signal.action))
    .sort((a, b) => (a.signal.prob_up ?? 1) - (b.signal.prob_up ?? 1));
  const watchCount = all.length - buys.length - sells.length;

  const latestDate = all.reduce<string>(
    (acc, x) => (x.signal.date > acc ? x.signal.date : acc),
    "",
  );

  return (
    <section className="mb-10">
      <div className="mb-3 flex flex-wrap items-baseline gap-2">
        <h2 className="text-xl font-bold text-white">今日のAI判断</h2>
        {latestDate && <span className="text-sm text-slate-400">{formatDateJa(latestDate)}時点</span>}
      </div>

      {buys.length === 0 && sells.length === 0 ? (
        <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-8 text-center">
          <Coffee size={28} className="mx-auto mb-3 text-slate-500" />
          <p className="text-lg font-bold text-slate-200">今日は「様子見」の日</p>
          <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-slate-400">
            全{all.length}銘柄、<Term k="gate">成績テスト</Term>
            を合格した売買サインはありませんでした。無理に動かないのも大事な判断です。
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {buys.length > 0 && (
            <div
              className={`rounded-xl border border-red-500/40 bg-red-500/10 p-4 ${
                sells.length === 0 ? "md:col-span-2" : ""
              }`}
            >
              <div className="mb-3 flex items-center justify-between">
                <span className="flex items-center gap-2 text-sm font-bold text-red-300">
                  <TrendingUp size={16} /> 買い候補
                </span>
                <span className="text-xs font-semibold text-red-300">{buys.length}社</span>
              </div>
              <div className="space-y-2">
                {buys.map((x) => (
                  <HeroChip key={x.code} code={x.code} signal={x.signal} side="buy" />
                ))}
              </div>
            </div>
          )}
          {sells.length > 0 && (
            <div
              className={`rounded-xl border border-blue-500/40 bg-blue-500/10 p-4 ${
                buys.length === 0 ? "md:col-span-2" : ""
              }`}
            >
              <div className="mb-3 flex items-center justify-between">
                <span className="flex items-center gap-2 text-sm font-bold text-blue-300">
                  <TrendingDown size={16} /> 売り候補
                </span>
                <span className="text-xs font-semibold text-blue-300">{sells.length}社</span>
              </div>
              <div className="space-y-2">
                {sells.map((x) => (
                  <HeroChip key={x.code} code={x.code} signal={x.signal} side="sell" />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {(buys.length > 0 || sells.length > 0) && (
        <p className="mt-3 text-xs leading-relaxed text-slate-500">
          ほか{watchCount}銘柄は「様子見」。ここに出るのは
          <Term k="gate">成績テスト</Term>に合格した売買サインだけです。
        </p>
      )}
    </section>
  );
}
```

- [ ] **Step 2: 品質ゲート → Commit**

Run: `cd web && npm run lint && npx tsc --noEmit` → エラー0

```bash
git add web/src/components/TodayHero.tsx
git commit -m "今日のAI判断ヒーロー(TodayHero)を追加"
```

---

### Task 10: StockExplorer(検索+絞り込み+全銘柄リスト)

**Files:**
- Create: `web/src/components/StockExplorer.tsx`

- [ ] **Step 1: ファイル作成**

```tsx
"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Search } from "lucide-react";
import { DashboardIndexData, Signal, SignalAction, TickerSummary } from "../types";
import {
  ACTION_STYLE,
  actionBadgeClass,
  actionLabel,
  changeTextClass,
  formatChangePct,
  formatPrice,
  formatProbability,
  isActionable,
  probTextClass,
} from "../lib/signal";
import { matchesTicker } from "../lib/search";
import { heatLabel } from "../lib/indicators";
import Term from "./Term";

type FilterKey = "all" | "buy" | "sell" | "hold";
type SortKey = "recommended" | "prob_desc" | "prob_asc" | "change_desc" | "code";

interface Row {
  code: string;
  t: TickerSummary;
}

function inFilter(filter: FilterKey, action: SignalAction | undefined): boolean {
  if (filter === "all") return true;
  if (!action) return filter === "hold";
  if (filter === "buy") return action === "BUY" || action === "MILD_BUY";
  if (filter === "sell") return action === "SELL" || action === "MILD_SELL";
  return action === "HOLD";
}

/** おすすめ順: 成績テスト合格の売買候補が先頭、続いて確率の極端さ順 */
function recommendedScore(t: TickerSummary): number {
  const s = t.latest_signal;
  if (!s || s.prob_up == null) return -1;
  return (isActionable(s) ? 1 : 0) + Math.abs(s.prob_up - 0.5);
}

function ProbBar({ signal }: { signal: Signal | null }) {
  const p = signal?.prob_up;
  if (p == null || !signal) return <span className="text-xs text-slate-500">---</span>;
  return (
    <span className="flex items-center gap-2">
      <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-800">
        <span
          className="block h-full rounded-full"
          style={{
            width: `${Math.round(p * 100)}%`,
            backgroundColor: ACTION_STYLE[signal.action].barColor,
          }}
        />
      </span>
      <span className={`w-14 shrink-0 text-right text-xs font-semibold ${probTextClass(p)}`}>
        {formatProbability(p)}
      </span>
    </span>
  );
}

export default function StockExplorer({ data }: { data: DashboardIndexData }) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterKey>("all");
  const [sort, setSort] = useState<SortKey>("recommended");

  const rows = useMemo<Row[]>(
    () => Object.entries(data.tickers).map(([code, t]) => ({ code, t })),
    [data],
  );

  const counts = useMemo(() => {
    const c = { all: rows.length, buy: 0, sell: 0, hold: 0 };
    for (const { t } of rows) {
      const a = t.latest_signal?.action;
      if (a === "BUY" || a === "MILD_BUY") c.buy += 1;
      else if (a === "SELL" || a === "MILD_SELL") c.sell += 1;
      else c.hold += 1;
    }
    return c;
  }, [rows]);

  const visible = useMemo(() => {
    const filtered = rows.filter(
      ({ code, t }) =>
        matchesTicker(query, code, t.name) && inFilter(filter, t.latest_signal?.action),
    );
    const arr = [...filtered];
    switch (sort) {
      case "prob_desc":
        arr.sort((a, b) => (b.t.latest_signal?.prob_up ?? -1) - (a.t.latest_signal?.prob_up ?? -1));
        break;
      case "prob_asc":
        arr.sort((a, b) => (a.t.latest_signal?.prob_up ?? 2) - (b.t.latest_signal?.prob_up ?? 2));
        break;
      case "change_desc":
        arr.sort((a, b) => (b.t.change_pct ?? -Infinity) - (a.t.change_pct ?? -Infinity));
        break;
      case "code":
        arr.sort((a, b) => a.code.localeCompare(b.code));
        break;
      default:
        arr.sort((a, b) => recommendedScore(b.t) - recommendedScore(a.t));
    }
    return arr;
  }, [rows, query, filter, sort]);

  const hasChange = rows.some(({ t }) => t.change_pct != null);

  const chips: { key: FilterKey; label: string; activeClass: string }[] = [
    { key: "all", label: `すべて ${counts.all}`, activeClass: "border-slate-500 bg-slate-800 text-white" },
    { key: "buy", label: `買い ${counts.buy}`, activeClass: "border-red-500/60 bg-red-500/15 text-red-300" },
    { key: "sell", label: `売り ${counts.sell}`, activeClass: "border-blue-500/60 bg-blue-500/15 text-blue-300" },
    { key: "hold", label: `様子見 ${counts.hold}`, activeClass: "border-slate-500 bg-slate-800 text-slate-300" },
  ];

  return (
    <section className="mb-10">
      <div className="mb-3 flex flex-col gap-2 sm:flex-row">
        <label className="flex flex-1 items-center gap-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 focus-within:border-slate-500">
          <Search size={16} className="shrink-0 text-slate-500" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="銘柄名・コードで検索(例: トヨタ / 7203)"
            className="w-full bg-transparent text-sm text-slate-100 placeholder:text-slate-500 focus:outline-none"
          />
        </label>
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as SortKey)}
          className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-300 focus:border-slate-500 focus:outline-none"
          aria-label="並び替え"
        >
          <option value="recommended">おすすめ順</option>
          <option value="prob_desc">上がる確率が高い順</option>
          <option value="prob_asc">上がる確率が低い順</option>
          {hasChange && <option value="change_desc">前日比が大きい順</option>}
          <option value="code">コード順</option>
        </select>
      </div>

      <div className="mb-4 flex flex-wrap gap-2">
        {chips.map((chip) => (
          <button
            key={chip.key}
            type="button"
            onClick={() => setFilter(chip.key)}
            className={`rounded-full border px-3 py-1 text-xs font-semibold transition-colors ${
              filter === chip.key
                ? chip.activeClass
                : "border-slate-700 text-slate-400 hover:border-slate-500 hover:text-slate-200"
            }`}
          >
            {chip.label}
          </button>
        ))}
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-800 bg-slate-900/60">
        <div className="hidden grid-cols-[1.6fr_1fr_0.8fr_1.2fr_0.8fr] gap-3 px-3 py-2 text-xs text-slate-500 md:grid">
          <span>銘柄</span>
          <span>株価{hasChange && " / 前日比"}</span>
          <span>判断</span>
          <span>
            <Term k="prob_up">上がる確率</Term>
          </span>
          <span>
            <Term k="rsi">過熱感</Term>
          </span>
        </div>

        {visible.length === 0 && (
          <p className="px-3 py-6 text-center text-sm text-slate-500">
            「{query}」に当てはまる銘柄がありません
          </p>
        )}

        {visible.map(({ code, t }) => {
          const s = t.latest_signal;
          const tint = s ? ACTION_STYLE[s.action].rowTintClass : "";
          const heat = heatLabel(t.latest_data?.rsi);
          return (
            <div key={code} className={tint}>
              <Link
                href={`/stocks/${code}`}
                className="hidden grid-cols-[1.6fr_1fr_0.8fr_1.2fr_0.8fr] items-center gap-3 border-t border-slate-800/70 px-3 py-2.5 transition-colors hover:bg-slate-800/40 md:grid"
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm font-semibold text-slate-100">{t.name}</span>
                  <span className="font-mono text-xs text-slate-500">{code}</span>
                </span>
                <span className="text-sm text-slate-200">
                  {formatPrice(t.latest_data?.close)}
                  {t.change_pct != null && (
                    <span className={`ml-1.5 text-xs ${changeTextClass(t.change_pct)}`}>
                      {formatChangePct(t.change_pct)}
                    </span>
                  )}
                </span>
                <span>
                  {s ? (
                    <span className={`rounded-full px-2.5 py-0.5 text-xs font-bold ${actionBadgeClass(s.action)}`}>
                      {actionLabel(s.action)}
                    </span>
                  ) : (
                    <span className="text-xs text-slate-500">---</span>
                  )}
                </span>
                <ProbBar signal={s} />
                <span className={`text-xs ${heat.className}`}>{heat.text}</span>
              </Link>

              <Link
                href={`/stocks/${code}`}
                className="flex items-center justify-between gap-3 border-t border-slate-800/70 px-3 py-3 transition-colors hover:bg-slate-800/40 md:hidden"
              >
                <span className="min-w-0">
                  <span className="flex items-center gap-2">
                    <span className="truncate text-sm font-semibold text-slate-100">{t.name}</span>
                    {s && (
                      <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-bold ${actionBadgeClass(s.action)}`}>
                        {actionLabel(s.action)}
                      </span>
                    )}
                  </span>
                  <span className="mt-0.5 block text-xs text-slate-500">
                    <span className="font-mono">{code}</span>
                    <span className="ml-2 text-slate-400">{formatPrice(t.latest_data?.close)}</span>
                    {t.change_pct != null && (
                      <span className={`ml-1.5 ${changeTextClass(t.change_pct)}`}>
                        {formatChangePct(t.change_pct)}
                      </span>
                    )}
                  </span>
                </span>
                <span className={`shrink-0 text-sm font-bold ${probTextClass(s?.prob_up)}`}>
                  {formatProbability(s?.prob_up)}
                </span>
              </Link>
            </div>
          );
        })}
      </div>
    </section>
  );
}
```

- [ ] **Step 2: 品質ゲート → Commit**

Run: `cd web && npm run lint && npx tsc --noEmit` → エラー0

```bash
git add web/src/components/StockExplorer.tsx
git commit -m "検索・絞り込み・並び替え付き全銘柄リスト(StockExplorer)を追加"
```

---

### Task 11: ホームページ刷新(案A組み立て)+ スリム成績カード + 配分カード文言

**Files:**
- Modify: `web/src/app/page.tsx`(全置換)
- Modify: `web/src/components/PerformanceCard.tsx`(全置換)
- Modify: `web/src/components/PortfolioCard.tsx`(文言+Term)
- Delete: `web/src/components/RegimeBanner.tsx`

- [ ] **Step 1: `web/src/app/page.tsx` を全置換**(静的解説ブロック3つ・旧カードグリッド・describeRsi/describeVolume を廃止)

```tsx
"use client";

import { useEffect, useState } from "react";
import { DashboardIndexData } from "../types";
import SiteHeader from "../components/SiteHeader";
import SiteFooter from "../components/SiteFooter";
import TodayHero from "../components/TodayHero";
import StockExplorer from "../components/StockExplorer";
import PerformanceCard from "../components/PerformanceCard";
import PortfolioCard from "../components/PortfolioCard";
import GlossaryAccordion from "../components/GlossaryAccordion";

export default function Home() {
  const [data, setData] = useState<DashboardIndexData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
    fetch(`${basePath}/dashboard_index.json`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((json: DashboardIndexData) => {
        setData(json);
        setLoading(false);
      })
      .catch((err) => {
        console.error("Failed to load dashboard index", err);
        setLoading(false);
      });
  }, []);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-400">
        読み込み中...
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-red-400">
        データの読み込みに失敗しました。
      </div>
    );
  }

  return (
    <main className="min-h-screen bg-slate-950 p-4 text-slate-200 md:p-8">
      <SiteHeader updated={data.last_update} />
      <div className="mx-auto max-w-7xl">
        <TodayHero data={data} />

        <h2 className="mb-4 text-xl font-bold text-white">全銘柄をさがす</h2>
        <StockExplorer data={data} />

        <PerformanceCard />
        <PortfolioCard />
        <GlossaryAccordion />
      </div>
      <SiteFooter />
    </main>
  );
}
```

- [ ] **Step 2: RegimeBanner を削除**

```bash
git rm web/src/components/RegimeBanner.tsx
```

- [ ] **Step 3: `web/src/components/PerformanceCard.tsx` を全置換**(スリム化+Term)

```tsx
"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { PerformanceSummary } from "../types";
import { fetchJson, isAvailablePayload } from "../lib/fetchJson";
import Term from "./Term";

export default function PerformanceCard() {
  const [perf, setPerf] = useState<PerformanceSummary | null>(null);

  useEffect(() => {
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
    fetchJson<PerformanceSummary>(
      `${basePath}/performance_summary.json`,
      (v): v is PerformanceSummary => isAvailablePayload(v),
    ).then(setPerf);
  }, []);

  if (!perf || !perf.available || !perf.horizons) return null;

  const h5 = perf.horizons["5"];
  const curve = perf.equity_curve || [];
  const cumReturn = curve.length ? curve[curve.length - 1].equity - 1 : null;
  const pct = (v: number | null | undefined) =>
    v == null ? "---" : `${(v * 100).toFixed(1)}%`;

  return (
    <section className="mb-8 rounded-xl border border-slate-800 bg-slate-900/80 p-5">
      <div className="mb-1 flex items-center justify-between">
        <h2 className="text-lg font-bold text-white">AIの成績</h2>
        <Link href="/performance" className="text-sm text-blue-400 hover:underline">
          くわしく見る →
        </Link>
      </div>
      <p className="mb-4 text-xs text-slate-400">
        実際に出した買いサインのあとに株価がどうなったかの記録です。サンプルが貯まるほど信頼度が上がります。
      </p>
      <div className="grid grid-cols-3 gap-4">
        <div>
          <div className="mb-1 text-xs text-slate-500">
            <Term k="hit_rate">的中率</Term>
            <span className="ml-1">(5日後)</span>
          </div>
          <div className="text-2xl font-bold text-slate-100">{pct(h5?.hit_rate)}</div>
        </div>
        <div>
          <div className="mb-1 text-xs text-slate-500">
            <Term k="equity_curve">通算リターン</Term>
          </div>
          <div className="text-2xl font-bold text-slate-100">{pct(cumReturn)}</div>
        </div>
        <div>
          <div className="mb-1 text-xs text-slate-500">サイン回数</div>
          <div className="text-2xl font-bold text-slate-200">{perf.n_long_signals ?? 0}</div>
        </div>
      </div>
    </section>
  );
}
```

- [ ] **Step 4: `web/src/components/PortfolioCard.tsx` の文言を差し替え**(構造は維持)

4-a. タイトル行(46行)を置換:

```tsx
        <h3 className="text-lg font-bold text-white">AIのおすすめ配分</h3>
```

4-b. モードピル(48-56行)を置換:

```tsx
          {pf.mode === "active" ? (
            <span className="rounded-full border border-emerald-500/40 bg-emerald-500/20 px-2 py-1 text-xs font-bold text-emerald-300">
              自動反映中
            </span>
          ) : (
            <span className="rounded-full border border-amber-500/40 bg-amber-500/20 px-2 py-1 text-xs font-bold text-amber-300">
              <Term k="shadow_mode">提案モード</Term>
            </span>
          )}
```

4-c. ファイル先頭の import に追加:

```tsx
import Term from "./Term";
```

4-d. サマリー指標の見出し(69/73/77行)をやさしい言葉に置換。`text-emerald-300`/`text-blue-300` の数値色はやめて `text-slate-100` に統一(緑=健全性専用ルール):

```tsx
          <div className="mb-1 text-xs text-slate-500">投資にあてる割合</div>
          <div className="text-2xl font-bold text-slate-100">{fmtPct(pf.gross_exposure)}</div>
```

```tsx
          <div className="mb-1 text-xs text-slate-500">想定の値動き幅</div>
          <div className="text-2xl font-bold text-slate-100">{fmtPct(pf.expected_vol)}</div>
```

```tsx
          <div className="mb-1 text-xs text-slate-500">期待リターン</div>
          <div className="text-2xl font-bold text-slate-100">{fmtPct(pf.expected_ret)}</div>
```

4-e. テーブルヘッダ(100-105行)を置換(`uppercase` は不要になるので外す):

```tsx
            <tr className="border-b border-slate-800 text-xs text-slate-500">
              <th className="pb-2 pr-4 text-left">銘柄</th>
              <th className="pb-2 pr-4 text-right">配分</th>
              <th className="pb-2 pr-4 text-center">前回比</th>
              <th className="pb-2 pr-4 text-right"><Term k="cs_rank">AI順位</Term></th>
              <th className="pb-2 pr-4 text-right">期待リターン</th>
              <th className="pb-2 text-right"><Term k="prob_up">上がる確率</Term></th>
            </tr>
```

- [ ] **Step 5: 品質ゲート + ビルド**

Run: `cd web && npm run lint && npx tsc --noEmit && npm run build:prod`
Expected: すべてエラー0(ビルドは `docs/dashboard_index.json` を読んで静的ページ生成に成功)

- [ ] **Step 6: Commit**

```bash
git add web/src/app/page.tsx web/src/components/PerformanceCard.tsx web/src/components/PortfolioCard.tsx
git commit -m "ホームを案A(今日の結論ファースト)に全面刷新"
```

(RegimeBanner の削除は Step 2 の `git rm` でステージ済みなのでこのコミットに含まれる。`git status` で deleted になっていることを確認。)

---

### Task 12: SignalNarrative(AIのひとこと)+ ThresholdGauge(判断ラインゲージ)

**Files:**
- Create: `web/src/components/SignalNarrative.tsx`
- Create: `web/src/components/ThresholdGauge.tsx`

- [ ] **Step 1: `web/src/components/SignalNarrative.tsx` を作成**

```tsx
"use client";

import { Sparkles } from "lucide-react";
import clsx from "clsx";
import { Signal } from "../types";
import {
  actionCardClass,
  actionLabel,
  actionTextClass,
  formatPrice,
  formatProbability,
  isBuySide,
  isSellSide,
} from "../lib/signal";
import Term from "./Term";

function pctText(v: number): string {
  return `${(v * 100).toFixed(0)}%`;
}

/** シグナルのフィールドから「AIのひとこと」を日本語文として自動合成する */
export default function SignalNarrative({ signal }: { signal: Signal }) {
  const p = signal.prob_up;
  const th = signal.thresholds;
  const action = signal.action;
  const raw = signal.raw_action;

  const actionSpan = (
    <span className={clsx("font-bold", actionTextClass(action))}>{actionLabel(action)}</span>
  );

  let body: React.ReactNode;

  if (signal.gate_passed === false && raw && raw !== "HOLD" && action === "HOLD") {
    // 素の予測は売買寄りだが成績テスト不合格で様子見に落とされたケース
    body = (
      <>
        AIの素の予測は「{actionLabel(raw)}」寄り(<Term k="prob_up">上がる確率</Term>{" "}
        {formatProbability(p)})ですが、この銘柄は<Term k="gate">成績テスト</Term>
        に不合格のため「様子見」にしています。
        {signal.confidence_reason && (
          <span className="text-slate-400">({signal.confidence_reason})</span>
        )}
      </>
    );
  } else if (p != null && th && isBuySide(action)) {
    const line = action === "BUY" ? th.buy : th.mild_buy;
    body = (
      <>
        判断は{actionSpan}。<Term k="prob_up">上がる確率</Term>は{formatProbability(p)}で、
        この銘柄の<Term k="threshold">{action === "BUY" ? "買いライン" : "やや買いライン"}</Term>(
        {pctText(line)})を超えています。
        {signal.gate_passed === true && (
          <>
            過去データでの<Term k="gate">成績テスト</Term>にも合格している、本命のシグナルです。
          </>
        )}
      </>
    );
  } else if (p != null && th && isSellSide(action)) {
    const line = action === "SELL" ? th.sell : th.mild_sell;
    body = (
      <>
        判断は{actionSpan}。<Term k="prob_up">上がる確率</Term>は{formatProbability(p)}と低く、
        <Term k="threshold">{action === "SELL" ? "売りライン" : "やや売りライン"}</Term>(
        {pctText(line)})を下回っています。
        {signal.gate_passed === true && <>持っている場合は手放すことを検討するサインです。</>}
      </>
    );
  } else if (p != null) {
    body = (
      <>
        判断は{actionSpan}。<Term k="prob_up">上がる確率</Term>は{formatProbability(p)}で、
        買いとも売りとも言い切れない<Term k="threshold">判断ライン</Term>の間にあります。
        今日は急いで動く必要はありません。
      </>
    );
  } else {
    body = <>{signal.reason || "本日の予測データがありません。"}</>;
  }

  const showPlan = isBuySide(action) && (signal.limit_price != null || signal.stop_loss != null);

  return (
    <div className={clsx("rounded-xl border p-5", actionCardClass(action))}>
      <div className="mb-2 flex items-center gap-2 text-xs text-slate-400">
        <Sparkles size={14} />
        AIのひとこと
        <span className="text-slate-500">({signal.date})</span>
      </div>
      <p className="text-sm leading-relaxed text-slate-200">{body}</p>
      {showPlan && (
        <div className="mt-4 grid grid-cols-2 gap-3">
          {signal.limit_price != null && (
            <div className="rounded-lg bg-slate-900/60 px-3 py-2.5">
              <Term k="limit_price" className="text-xs text-slate-400">
                買ってよい上限
              </Term>
              <div className="mt-1 text-base font-bold text-slate-100">
                {formatPrice(signal.limit_price)}
              </div>
            </div>
          )}
          {signal.stop_loss != null && (
            <div className="rounded-lg bg-slate-900/60 px-3 py-2.5">
              <Term k="stop_loss" className="text-xs text-slate-400">
                撤退ライン
              </Term>
              <div className="mt-1 text-base font-bold text-slate-100">
                {formatPrice(signal.stop_loss)}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: `web/src/components/ThresholdGauge.tsx` を作成**

```tsx
"use client";

import { SignalThresholds } from "../types";
import Term from "./Term";

interface Props {
  probUp: number;
  thresholds: SignalThresholds;
}

/** 今日の上がる確率が、銘柄ごとの判断ラインのどこにあるかを示す横ゲージ */
export default function ThresholdGauge({ probUp, thresholds }: Props) {
  const pos = (v: number) => `${Math.min(100, Math.max(0, v * 100))}%`;
  // ラベルの重なりを避けるため2段に振り分ける(row: 0=上段, 1=下段)
  const markers = [
    { key: "sell", value: thresholds.sell, label: `売り ${(thresholds.sell * 100).toFixed(0)}%`, row: 0 },
    { key: "mild_buy", value: thresholds.mild_buy, label: `やや買い ${(thresholds.mild_buy * 100).toFixed(0)}%`, row: 1 },
    { key: "buy", value: thresholds.buy, label: `買い ${(thresholds.buy * 100).toFixed(0)}%`, row: 0 },
  ];
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5">
      <div className="mb-4 text-xs text-slate-400">
        今日の<Term k="prob_up">上がる確率</Term>は<Term k="threshold">判断ライン</Term>のどこ?
      </div>
      <div className="relative mx-1 h-2 rounded-full bg-slate-800">
        <span
          className="absolute left-0 top-0 block h-2 rounded-full bg-slate-600"
          style={{ width: pos(probUp) }}
        />
        {markers.map((m) => (
          <span
            key={m.key}
            className="absolute -top-1 block h-4 w-0.5 bg-slate-500"
            style={{ left: pos(m.value) }}
          />
        ))}
        <span
          className="absolute -top-1.5 block h-5 w-2 -translate-x-1/2 rounded-sm bg-white"
          style={{ left: pos(probUp) }}
        />
      </div>
      <div className="relative mt-2 h-9 text-xs text-slate-500">
        {markers.map((m) => (
          <span
            key={m.key}
            className="absolute -translate-x-1/2 whitespace-nowrap"
            style={{ left: pos(m.value), top: m.row === 0 ? 0 : "1.1rem" }}
          >
            {m.label}
          </span>
        ))}
      </div>
      <p className="text-xs font-semibold text-slate-200">今日: {(probUp * 100).toFixed(1)}%</p>
    </div>
  );
}
```

- [ ] **Step 3: 品質ゲート → Commit**

Run: `cd web && npm run lint && npx tsc --noEmit` → エラー0

```bash
git add web/src/components/SignalNarrative.tsx web/src/components/ThresholdGauge.tsx
git commit -m "AIのひとこと(SignalNarrative)と判断ラインゲージを追加"
```

---

### Task 13: 銘柄詳細ページ刷新 + SignalCard 削除 + StockChart 微修正

**Files:**
- Modify: `web/src/app/stocks/[ticker]/StockDetailContent.tsx`(全置換)
- Delete: `web/src/components/SignalCard.tsx`
- Modify: `web/src/components/StockChart.tsx`(2行)

- [ ] **Step 1: `StockDetailContent.tsx` を全置換**

```tsx
"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import {
  SignalOutcomeRow,
  SignalOutcomesRecent,
  TickerDetailData,
} from "../../../types";
import StockChart from "../../../components/StockChart";
import SignalNarrative from "../../../components/SignalNarrative";
import ThresholdGauge from "../../../components/ThresholdGauge";
import SiteHeader from "../../../components/SiteHeader";
import SiteFooter from "../../../components/SiteFooter";
import Term from "../../../components/Term";
import {
  actionBadgeClass,
  actionLabel,
  actionTextClass,
  changeTextClass,
  formatChangePct,
  formatPrice,
  formatProbability,
  gateLabel,
} from "../../../lib/signal";
import { fetchJson, isAvailablePayload } from "../../../lib/fetchJson";

interface Props {
  ticker: string;
}

function pctSigned(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)}%`;
}

export default function StockDetailContent({ ticker }: Props) {
  const tickerCode = decodeURIComponent(ticker);

  const [data, setData] = useState<TickerDetailData | null>(null);
  const [outcomes, setOutcomes] = useState<SignalOutcomesRecent | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
    fetch(`${basePath}/tickers/${encodeURIComponent(tickerCode)}.json`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((json: TickerDetailData) => {
        setData(json);
        setLoading(false);
      })
      .catch((err) => {
        console.error("Failed to load ticker detail", err);
        setLoading(false);
      });
    fetchJson<SignalOutcomesRecent>(
      `${basePath}/signal_outcomes_recent.json`,
      (v): v is SignalOutcomesRecent => isAvailablePayload(v),
    ).then(setOutcomes);
  }, [tickerCode]);

  const outcomeByDate = useMemo(() => {
    const m = new Map<string, SignalOutcomeRow>();
    for (const row of outcomes?.rows ?? []) {
      if (row.ticker === tickerCode) m.set(row.entry_date, row);
    }
    return m;
  }, [outcomes, tickerCode]);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-400">
        読み込み中...
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-slate-950 text-red-400">
        <p>データの読み込みに失敗しました ({tickerCode})</p>
        <Link href="/" className="text-blue-400 hover:underline">
          ダッシュボードに戻る
        </Link>
      </div>
    );
  }

  const latestSignal = data.latest_signal;
  const last = data.data[data.data.length - 1];
  const prev = data.data[data.data.length - 2];
  const change =
    last?.close != null && prev?.close != null && prev.close !== 0
      ? (last.close - prev.close) / prev.close
      : null;
  const gate = latestSignal ? gateLabel(latestSignal) : null;

  return (
    <main className="min-h-screen bg-slate-950 p-4 text-slate-200 md:p-8">
      <SiteHeader updated={data.last_update} />

      <div className="mx-auto max-w-7xl">
        <Link
          href="/"
          className="mb-4 inline-flex items-center gap-1 text-sm text-slate-400 hover:text-white"
        >
          <ArrowLeft size={16} /> 銘柄一覧へ
        </Link>

        <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h1 className="text-2xl font-bold tracking-tight text-white">{data.name}</h1>
            <span className="font-mono text-sm text-slate-500">{tickerCode}</span>
            <span className="text-xl font-bold text-white">{formatPrice(last?.close)}</span>
            {change != null && (
              <span className={`text-sm font-semibold ${changeTextClass(change)}`}>
                {formatChangePct(change)}
              </span>
            )}
          </div>
          {latestSignal && gate && (
            <div className="flex items-center gap-2">
              <span
                className={`rounded-full px-3 py-1 text-sm font-bold ${actionBadgeClass(latestSignal.action)}`}
              >
                {actionLabel(latestSignal.action)}
              </span>
              <span
                className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${gate.className}`}
              >
                <Term k="gate">{gate.text}</Term>
              </span>
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 gap-8 lg:grid-cols-3">
          <div className="space-y-8 lg:col-span-2">
            <StockChart data={data.data} tickerName={data.name} />
          </div>

          <div className="space-y-6">
            {latestSignal ? (
              <SignalNarrative signal={latestSignal} />
            ) : (
              <div className="rounded-xl border border-slate-800 bg-slate-900 p-4 text-slate-500">
                本日のシグナルはありません。
              </div>
            )}

            {latestSignal?.prob_up != null && latestSignal.thresholds && (
              <ThresholdGauge
                probUp={latestSignal.prob_up}
                thresholds={latestSignal.thresholds}
              />
            )}

            <div>
              <h2 className="mb-3 text-lg font-bold text-slate-100">これまでのサインと結果</h2>
              <div className="max-h-[480px] space-y-2 overflow-y-auto pr-2">
                {data.signals.map((entry) => {
                  const sig = entry.signal;
                  const outcome = outcomeByDate.get(entry.date);
                  return (
                    <div
                      key={`${entry.date}-${sig.action}`}
                      className="flex items-center justify-between gap-2 rounded border border-slate-800/50 bg-slate-900/50 p-3 text-sm"
                    >
                      <span className="font-mono text-xs text-slate-400">{entry.date}</span>
                      <span className={`font-bold ${actionTextClass(sig.action)}`}>
                        {actionLabel(sig.action)}
                      </span>
                      <span className="text-xs text-slate-500">
                        {formatProbability(sig.prob_up)}
                      </span>
                      <span className="w-24 text-right text-xs">
                        {outcome?.realized_ret != null ? (
                          <span
                            className={
                              outcome.realized_ret >= 0 ? "text-red-300" : "text-blue-300"
                            }
                          >
                            {pctSigned(outcome.realized_ret)}
                            {outcome.hit === true && " ○"}
                            {outcome.hit === false && " ×"}
                          </span>
                        ) : (
                          <span className="text-slate-600">—</span>
                        )}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </div>
      <SiteFooter />
    </main>
  );
}
```

- [ ] **Step 2: SignalCard を削除**(参照が無いことを確認してから)

Run: `grep -rn "SignalCard" web/src --include="*.tsx" --include="*.ts"`
Expected: ヒットは `web/src/components/SignalCard.tsx` 自身の中だけ(他ファイルからの import がゼロ)

```bash
git rm web/src/components/SignalCard.tsx
```

- [ ] **Step 3: StockChart の微修正(2行)**

`web/src/components/StockChart.tsx` 182行:

```tsx
  const [dateRange, setDateRange] = useState<DateRange>("3m");
```

(現在は `"1m"`。設計どおり既定3ヶ月へ。)

同ファイル 539-545 行付近、RSI 30 ラインの `ReferenceLine`(現在 `stroke="#22c55e"`)を売り側カラーへ:

```tsx
            <ReferenceLine
              y={30}
              stroke="#06b6d4"
              strokeDasharray="3 3"
              strokeWidth={1}
            />
```

(緑=健全性専用ルールに合わせる。MA線の配色 #facc15/#38bdf8/#4ade80 はチャート用パレットとしてそのまま。)

- [ ] **Step 4: 品質ゲート + ビルド → Commit**

Run: `cd web && npm run lint && npx tsc --noEmit && npm run build:prod`
Expected: エラー0

```bash
git add "web/src/app/stocks/[ticker]/StockDetailContent.tsx" web/src/components/StockChart.tsx
git commit -m "銘柄詳細ページを刷新(AIのひとこと/ゲージ/結果つき履歴)"
```

(パスの `[ticker]` は zsh の glob と衝突するため必ず引用符で囲む。)

(SignalCard の削除は Step 2 の `git rm` でステージ済み。)

---

### Task 14: 成績ページ刷新(ヘッドライン+やさしい見出し+健康診断)

**Files:**
- Create: `web/src/components/PerformanceHeadline.tsx`
- Modify: `web/src/app/performance/page.tsx`(全置換)
- Modify: `web/src/components/PerformanceDetail.tsx`(見出し・凡例・表ヘッダ)
- Modify: `web/src/components/ModelQualityCard.tsx`(全置換: 健康診断化)

- [ ] **Step 1: `web/src/components/PerformanceHeadline.tsx` を作成**

```tsx
"use client";

import { useEffect, useState } from "react";
import { PerformanceSummary } from "../types";
import { fetchJson, isAvailablePayload } from "../lib/fetchJson";
import Term from "./Term";

export default function PerformanceHeadline() {
  const [perf, setPerf] = useState<PerformanceSummary | null>(null);

  useEffect(() => {
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
    fetchJson<PerformanceSummary>(
      `${basePath}/performance_summary.json`,
      (v): v is PerformanceSummary => isAvailablePayload(v),
    ).then(setPerf);
  }, []);

  if (!perf || !perf.available || !perf.horizons) return null;

  const h5 = perf.horizons["5"];
  const curve = perf.equity_curve || [];
  const cumReturn = curve.length ? curve[curve.length - 1].equity - 1 : null;
  const pct = (v: number | null | undefined) =>
    v == null ? "---" : `${(v * 100).toFixed(1)}%`;

  return (
    <section className="mb-8">
      <p className="mb-3 text-sm leading-relaxed text-slate-400">
        買いサインのあと実際どうだったか、の通算成績です。
        <Term k="topix">市場平均(TOPIX)</Term>と比べて意味があったかを見ます。
      </p>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5">
          <div className="mb-1 text-xs text-slate-500">
            <Term k="hit_rate">的中率</Term>
            <span className="ml-1">(5日後)</span>
          </div>
          <div className="text-3xl font-bold text-slate-100">{pct(h5?.hit_rate)}</div>
          <div className="mt-1 text-xs text-slate-500">サイン {h5?.count ?? 0} 回</div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5">
          <div className="mb-1 text-xs text-slate-500">平均リターン(5日後)</div>
          <div className="text-3xl font-bold text-slate-100">{pct(h5?.avg_return)}</div>
          <div className="mt-1 text-xs text-slate-500">1回のサインあたり</div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5">
          <div className="mb-1 text-xs text-slate-500">
            <Term k="equity_curve">通算リターン</Term>
          </div>
          <div className="text-3xl font-bold text-slate-100">{pct(cumReturn)}</div>
          <div className="mt-1 text-xs text-slate-500">シグナル通りに売買した場合</div>
        </div>
      </div>
    </section>
  );
}
```

- [ ] **Step 2: `web/src/app/performance/page.tsx` を全置換**

```tsx
"use client";

import SiteHeader from "../../components/SiteHeader";
import SiteFooter from "../../components/SiteFooter";
import PerformanceHeadline from "../../components/PerformanceHeadline";
import PerformanceDetail from "../../components/PerformanceDetail";
import ModelQualityCard from "../../components/ModelQualityCard";

export default function PerformancePage() {
  return (
    <main className="min-h-screen bg-slate-950 p-4 text-slate-200 md:p-8">
      <SiteHeader />
      <div className="mx-auto max-w-7xl">
        <h1 className="mb-4 text-2xl font-bold tracking-tight text-white">AIの成績</h1>
        <PerformanceHeadline />
        <PerformanceDetail />
        <ModelQualityCard />
      </div>
      <SiteFooter />
    </main>
  );
}
```

(ホームと同じく client ページに統一。SiteHeader/カード群はすべて client component。)

- [ ] **Step 3: `PerformanceDetail.tsx` をやさしい言葉に差し替え**

3-a. import に追加(4-24行の import 群へ):

```tsx
import Term from "./Term";
import { actionLabel } from "../lib/signal";
```

3-b. ローカルの `actionLabel` 関数(34-43行)を**削除**(共有版を使う。「BUY」→「買い」「HOLD」→「様子見」表示になる)。

3-c. `RollingStats` の stats 配列(53-58行)を置換:

```tsx
  const stats = [
    { label: "的中率(直近20日)", value: pct(rolling.hit_rate_20d) },
    { label: "平均リターン(直近20日)", value: pct(rolling.avg_return_20d) },
    { label: "市場平均との差(直近20日)", value: pct(rolling.excess_return_20d) },
    { label: "安定度(60日)", value: rolling.sharpe_60d == null ? "—" : rolling.sharpe_60d.toFixed(2) },
  ];
```

3-d. `EquityCurveSection` の見出し(75行)を置換:

```tsx
      <h3 className="mb-4 text-lg font-bold text-white">
        <Term k="equity_curve">資産の伸び</Term>
        <span className="ml-2 text-sm font-normal text-slate-400">
          vs <Term k="topix">市場平均</Term>
        </span>
      </h3>
```

3-e. 同セクションの凡例(90-91行)を置換:

```tsx
            <Line dataKey="strategy" stroke="#f87171" dot={false} name="AI" strokeWidth={2} />
            <Line dataKey="benchmark" stroke="#94a3b8" dot={false} name="市場平均" strokeWidth={1.5} />
```

3-f. `DrawdownSection` の見出し(103行)を置換:

```tsx
      <h3 className="mb-4 text-lg font-bold text-white">
        <Term k="drawdown">一時的な落ち込み</Term>
      </h3>
```

3-g. `ReliabilitySection` の見出し部(138-143行)を置換:

```tsx
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <h3 className="text-lg font-bold text-white">
          <Term k="calibration">確率の正直さ</Term>
        </h3>
        <span className="text-sm text-slate-400">
          <Term k="brier">予測のズレ点数</Term>: {rel?.brier == null ? "—" : rel.brier.toFixed(4)}
        </span>
      </div>
```

3-h. 同セクションの説明文(148-150行)を置換:

```tsx
          <p className="mb-3 text-xs text-slate-400">
            「70%」と言ったとき本当に70%上がっているか。赤(実際)とグレー(予測)が近いほど正直な予測です。
          </p>
```

3-i. `OutcomesTable` の見出し(176行)とテーブルヘッダ(183-193行)を置換:

```tsx
      <h3 className="mb-4 text-lg font-bold text-white">最近のサインの結果</h3>
```

```tsx
              <tr className="border-b border-slate-800 text-xs text-slate-500">
                <th className="py-2 pr-3 text-left">いつ</th>
                <th className="py-2 pr-3 text-left">銘柄</th>
                <th className="py-2 pr-3 text-left">判断</th>
                <th className="py-2 pr-3 text-right"><Term k="prob_up">上がる確率</Term></th>
                <th className="py-2 pr-3 text-right">結果</th>
                <th className="py-2 pr-3 text-right"><Term k="excess_return">市場平均との差</Term></th>
                <th className="py-2 pr-3 text-center">当たった?</th>
                <th className="py-2 pr-3 text-right"><Term k="mae_mfe">最大逆行</Term></th>
                <th className="py-2 text-right"><Term k="mae_mfe">最大順行</Term></th>
              </tr>
```

3-j. 的中バッジ(218-224行)を置換:

```tsx
                    {row.hit === true ? (
                      <span className="rounded-full bg-red-500/20 px-2 py-0.5 text-xs text-red-300">○ 当たり</span>
                    ) : row.hit === false ? (
                      <span className="rounded-full bg-blue-500/20 px-2 py-0.5 text-xs text-blue-300">× はずれ</span>
                    ) : (
                      "—"
                    )}
```

- [ ] **Step 4: `ModelQualityCard.tsx` を全置換(モデルの健康診断)**

```tsx
"use client";

import { useEffect, useState } from "react";
import { ModelQuality } from "../types";
import { fetchJson, isAvailablePayload } from "../lib/fetchJson";
import Term from "./Term";

export default function ModelQualityCard() {
  const [mq, setMq] = useState<ModelQuality | null>(null);

  useEffect(() => {
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
    fetchJson<ModelQuality>(
      `${basePath}/model_quality.json`,
      (v): v is ModelQuality => isAvailablePayload(v),
    ).then(setMq);
  }, []);

  if (!mq || !mq.available || !mq.summary) return null;

  const s = mq.summary;
  const fmt = (v: number | null | undefined, digits = 3) =>
    v == null ? "---" : v.toFixed(digits);
  const healthy = !s.drift_warning;

  return (
    <section className="mb-8 rounded-xl border border-slate-800 bg-slate-900/80 p-5">
      <div className="mb-1 flex items-center justify-between gap-2">
        <h3 className="text-lg font-bold text-white">モデルの健康診断</h3>
        <span
          className={`rounded-full border px-3 py-1 text-xs font-bold ${
            healthy
              ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-300"
              : "border-amber-500/40 bg-amber-500/15 text-amber-300"
          }`}
        >
          {healthy ? "良好" : "注意"}
        </span>
      </div>
      <p className="mb-4 text-xs text-slate-400">
        いま使っている予測モデル(
        <span className="font-mono text-slate-300">{mq.active_model_version}</span>
        )が壊れていないかの週次チェックです。「注意」になると自動で警告が飛びます。
      </p>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
        <div>
          <div className="mb-1 text-xs text-slate-500">
            <Term k="brier">予測のズレ点数</Term>
          </div>
          <div className="text-2xl font-bold text-slate-100">{fmt(s.median_brier)}</div>
        </div>
        <div>
          <div className="mb-1 text-xs text-slate-500">
            <Term k="ic">順位の当たり具合</Term>
          </div>
          <div className="text-2xl font-bold text-slate-100">{fmt(s.median_ic)}</div>
        </div>
        <div>
          <div className="mb-1 text-xs text-slate-500">チェック対象</div>
          <div className="text-2xl font-bold text-slate-200">{s.tickers}銘柄</div>
        </div>
      </div>
    </section>
  );
}
```

- [ ] **Step 5: 品質ゲート + ビルド → Commit**

Run: `cd web && npm run lint && npx tsc --noEmit && npm run build:prod`
Expected: エラー0

```bash
git add web/src/components/PerformanceHeadline.tsx web/src/app/performance/page.tsx web/src/components/PerformanceDetail.tsx web/src/components/ModelQualityCard.tsx
git commit -m "成績ページを刷新(ヘッドライン/やさしい見出し/健康診断)"
```

---

### Task 15: クリーンアップ + 仕様書の as-built 更新

**Files:**
- Modify: `web/src/lib/signal.ts`(旧API削除)
- Modify: `specification_document/02_frontend_web.md`
- Modify: `specification_document/05_cross_cutting.md`
- Modify: `specification_document/plans/2026-06-11-ui-redesign.md`(状態行)

- [ ] **Step 1: 未使用になった旧APIを削除**

Run: `grep -rn "confidenceBadgeClass\|formatThresholds" web/src --include="*.tsx" --include="*.ts"`
Expected: `web/src/lib/signal.ts` の定義のみヒット(参照ゼロ)

→ `signal.ts` 末尾の「旧API」セクション(`confidenceBadgeClass` と `formatThresholds` の2関数とその区切りコメント)を削除。`SignalThresholds` の import が未使用になるので import 行から外す:

```ts
import type { Signal, SignalAction } from "../types";
```

- [ ] **Step 2: 売り=緑の残骸が無いことを確認**

Run: `grep -rn "green-" web/src --include="*.tsx" --include="*.ts"`
Expected: ヒットなし(emerald は健全性用なので残ってよい。`#4ade80` は StockChart の MA60 線でチャートパレットとして許容)

- [ ] **Step 3: 仕様書を実装に合わせて更新**

`specification_document/02_frontend_web.md`: ページ構成・コンポーネント一覧を新構成に書き換える(更新日も変更)。最低限、次を反映:

- ページ: ホーム=`SiteHeader`+`TodayHero`+`StockExplorer`+`PerformanceCard`+`PortfolioCard`+`GlossaryAccordion`+`SiteFooter` / 成績=`PerformanceHeadline`+`PerformanceDetail`+`ModelQualityCard` / 銘柄詳細=`SignalNarrative`+`ThresholdGauge`+`StockChart`+結果つき履歴
- 削除: `RegimeBanner`(→`SiteHeader`のMoodPill)・`SignalCard`(→`SignalNarrative`)
- 新規 lib: `glossary.ts`(用語辞書22語)・`search.ts`(NFKC+かな折りたたみ検索)・`indicators.ts`
- 表示規約: 売買色(買い=赤系/売り=青系/緑=健全性専用)、`HOLD`の表示名「様子見」、ヒーロー掲載条件 `gate_passed && action !== HOLD`

`specification_document/05_cross_cutting.md`: `dashboard_index.json` の契約に次の2フィールドを追記:

```
- `tickers.{code}.prev_close` (number|null, optional): 前営業日終値。データ2日分未満なら null。
- `tickers.{code}.change_pct` (number|null, optional): 前日比 (last/prev - 1)。フロントは欠如時に前日比表示を隠す。
```

`specification_document/plans/2026-06-11-ui-redesign.md` 冒頭の状態行を「実装完了・検証待ち」に更新。

- [ ] **Step 4: Commit**

```bash
git add web/src/lib/signal.ts specification_document/02_frontend_web.md specification_document/05_cross_cutting.md specification_document/plans/2026-06-11-ui-redesign.md
git commit -m "旧表示APIの削除と仕様書のas-built更新"
```

---

### Task 16: 最終検証(ビルド+実機確認)

- [ ] **Step 1: 全自動チェック**

```bash
cd web && npm run lint && npx tsc --noEmit && npm run build:prod
cd .. && uv run python tests/test_dashboard_index_change.py
uv run python tests/test_dashboard_portfolio.py
uv run python tests/test_publish_workflow.py
```

Expected: すべてパス。`test_publish_workflow.py` が通る=docs契約が壊れていない(新規ファイルを作っていない)ことの確認。

- [ ] **Step 2: ローカルでダッシュボードデータを再生成(任意・データがある場合)**

```bash
uv run python -c "from src.dashboard import export_dashboard_data; export_dashboard_data()"
```

Expected: `docs/dashboard_index.json` の各 ticker に `prev_close`/`change_pct` が出る(`web/public/` にも同期される)。data/ が無い環境ではスキップ可(フロントは欠如時も劣化運転で動く)。

- [ ] **Step 3: 実機確認(dev サーバー + ブラウザ)**

```bash
cd web && npm run dev
```

http://localhost:3000 でチェックリスト(デスクトップ幅と375px幅の両方):

1. ホーム: ヒーローに成績テスト合格の買い/売り候補だけが出る。0件なら「今日は様子見の日」。日付がシグナル日付になっている
2. 検索: 「7203」「トヨタ」「とよた」(かな違い)でヒット。チップ絞り込み・並び替えが効く。0件時メッセージ
3. ツールチップ: 点線語をクリック/ホバー→開く。Escape・外側タップで閉じる。画面端で見切れない(モバイル幅で右端の語を開く)
4. リスト行タップ→詳細ページ遷移(ツールチップと干渉しない)
5. 詳細: AIのひとことの文が action/gate の組合せで自然(gate合格買い・gate不合格・様子見の3銘柄で確認)。ゲージのライン位置。履歴に結果(○×)が出る銘柄がある
6. 成績: ヘッドライン数字、各セクション見出し、表の「判断」が日本語、○×バッジ、健康診断
7. JSON欠如の劣化: DevTools で `performance_summary.json` をブロック→成績カードが消えるだけでページは動く
8. コンソールにエラーが出ていない

- [ ] **Step 4: 仕上げコミット(検証で微修正があれば)**

```bash
git add -u web/src
git commit -m "UI刷新の実機確認に伴う微調整"
```

(修正が無ければスキップ。)

---

## スコープ外(やらないこと)

- LINE通知文言(`src/digest.py` 等)・バックエンドロジックの変更(Task 1 の additive export を除く)
- `tickers.yml`、`docs/` 新規ファイル、`daily-publish-dashboard.yml` の変更
- URL クエリへの検索状態保存、テストフレームワーク導入、ライト/ダーク切替
