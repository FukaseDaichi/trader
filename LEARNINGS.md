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

## Mistakes to Avoid
（失敗と再発防止策）

- 2026-08-27: PowerShell で python の stdout を `| Select-Object -First N` に通すと broken pipe で exit 255 になる（スクリプト自体は正常終了）。終了コードの検証はパイプなしで実行して `$LASTEXITCODE` を見る。

## Domain Knowledge
（業務・仕様に関する事実）

- 2026-08-27: TOPIX 劣後（IR -0.09）の主因は銘柄選択ではなく構造要因。①target_vol 0.12 による vol targeting で平均グロス 0.496・β0.54（上昇相場の値上がりを半分しか取れない）、②5日ごとのほぼ全入れ替え（turnover 年約50倍）でコスト累計 -5.1%/9ヶ月。銘柄選択自体は alpha +9.7%/年・Sharpe 2.03 と正。
- 2026-08-27: `docs/portfolio_backtest.json` のベンチマークは毎期 turnover 2.0 の合成コスト（累計 -10.2%/9ヶ月）を課される same-basis 設計。実際のバイ&ホールド TOPIX（コストほぼゼロ）と比べると真のギャップは約 -10pt であり、公式 IR -0.09 が示すより大きい。

## Open Questions
（未解決・要調査）

## Consolidated Principles
（統合パス専用。通常の更新処理から直接追記しない）
